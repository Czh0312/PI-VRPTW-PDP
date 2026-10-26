"""Explicit recovery of a hash-pinned live.json replacement failure.

The frozen worker and all initial-attempt evidence remain untouched. This
controller changes only JSON publication retries; its review is separate from
the original first-attempt stage gate and statistics.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import time
import uuid


def write_with_retry(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                   allow_nan=False) + '\n', encoding='utf-8')
    for attempt in range(8):
        try:
            os.replace(temporary, path)
            return
        except PermissionError as exc:
            if getattr(exc, 'winerror', None) not in (5, 32, 33) or attempt == 7:
                raise
            time.sleep(.01 * 2 ** attempt)


def validate_failure(driver, out, manifest, job_id, expected_hash):
    job = next(j for j in manifest['jobs'] if j['id'] == job_id)
    for item in manifest['jobs']:
        first = out/'runs'/item['id']/'attempt_001'
        if not driver.complete_attempt(first):
            raise ValueError('All initial attempts must be complete before recovery')
    first = out/'runs'/job_id/'attempt_001'
    if driver.sha(first/'row.json') != expected_hash:
        raise ValueError('Original failure hash differs from reviewed evidence')
    row = driver.read(first/'row.json')
    error = row.get('error', '')
    if (row['status'] != 'ERROR' or row.get('valid') or
            '[WinError 5]' not in error or 'live.json.' not in error or
            '.tmp' not in error or ' -> ' not in error or
            not error.rstrip("'\"").endswith('live.json') or
            (first/'result.json').exists() or (first/'audit.json').exists()):
        raise ValueError('Not the reviewed pre-result live.json publication failure')
    gate = driver.read(out/'stage_gate.json')
    if (gate.get('ready') or gate.get('failures') != [job_id] or
            gate.get('certificate_contradictions') or
            gate.get('terminal') != len(manifest['jobs'])):
        raise ValueError('Initial gate contains unresolved or unreviewed failures')
    return job, first, gate


def recovery_valid(driver, path, job):
    if not driver.complete_attempt(path):
        return False
    row = driver.read(path/'row.json')
    audit_record = driver.read(path/'audit.json') if (path/'audit.json').exists() else {}
    audit = audit_record.get('audit', {})
    return bool(
        row.get('status') in ('FEASIBLE', 'OPTIMAL') and row.get('valid') and
        row.get('periodic_valid') and not row.get('any_audit_failure') and
        not row.get('certificate_errors') and
        all(row.get(k) == job.get(k) for k in
            ('id', 'case_path', 'model', 'method', 'version', 'seed', 'budget', 'counts')) and
        (path/'result.json').is_file() and
        audit_record.get('result_sha256') == driver.sha(path/'result.json') and
        audit.get('valid') and not audit.get('errors') and
        audit.get('periodic_replay', {}).get('valid'))


def recover(args):
    out = Path(args.study).resolve()
    # Verify frozen bytes before importing their coordinator entry point.
    from experiments.runs import run_thesis_completion as current
    manifest = current.read(out/'manifest.json')
    current.verify(out, manifest)
    spec = importlib.util.spec_from_file_location('frozen_recovery_driver', out/'driver.py')
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    driver.write = write_with_retry
    with driver.study_lock(out):
        job, first, gate = validate_failure(driver, out, manifest, args.job, args.failure_sha256)
        evidence = out/'infrastructure_recovery'
        registration = evidence/'registration.json'
        expected = dict(job=args.job, failure_row_sha256=args.failure_sha256,
                        manifest_sha256=driver.sha(out/'manifest.json'),
                        initial_stage_gate_sha256=driver.sha(out/'stage_gate.json'),
                        frozen_driver_sha256=driver.sha(out/'driver.py'),
                        recovery_controller_sha256=driver.sha(Path(__file__)),
                        additional_budget_seconds=job['budget'],
                        frozen_worker=True, primary_statistics='first_attempt_only',
                        concurrency_note='One recovery call; original worker/thread settings retained, no concurrent formal calls')
        if registration.exists():
            if driver.read(registration) != expected:
                raise ValueError('Recovery registration changed')
        else:
            if len(driver.attempt_dirs(out, job)) != 1:
                raise ValueError('Unregistered retry already exists')
            write_with_retry(registration, expected)
            write_with_retry(evidence/'initial_stage_gate.json', gate)
        attempts = driver.attempt_dirs(out, job)
        if len(attempts) == 1:
            driver.execute(out, manifest, job)
        elif len(attempts) != 2:
            raise ValueError('Additional retries require a new explicit review')
        second = out/'runs'/job['id']/'attempt_002'
        ready = recovery_valid(driver, second, job)
        driver.verify(out, manifest)
        if driver.sha(first/'row.json') != args.failure_sha256:
            raise ValueError('First attempt changed')
        if driver.sha(out/'stage_gate.json') != expected['initial_stage_gate_sha256']:
            raise ValueError('Original stage gate changed')
        summary = driver.summarize(out, manifest)
        review = dict(ready=ready, reviewed_at=driver.now(),
                      job=job['id'], original_gate_preserved=True,
                      first_failure_row_sha256=args.failure_sha256,
                      recovery_completion_sha256=driver.sha(second/'completion.json'),
                      registration_sha256=driver.sha(registration),
                      initial_statuses=summary['statuses'],
                      rationale='Hash-pinned pre-result infrastructure failure; frozen-worker recovery independently audited including two-cycle replay',
                      limitations='Recovery is supplementary, with different execution time and one active call; never replaces initial statistical denominator')
        write_with_retry(evidence/'review.json', review)
        print(json.dumps(review, ensure_ascii=False))
        if not ready:
            raise RuntimeError('Recovery did not pass independent audit; continuation blocked')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True)
    parser.add_argument('--job', required=True)
    parser.add_argument('--failure-sha256', required=True)
    recover(parser.parse_args())


if __name__ == '__main__':
    main()
