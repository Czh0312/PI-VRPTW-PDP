"""Cross-stage immutable evidence inventory, not a replacement for stage findings."""
import argparse
from collections import Counter
import math
from pathlib import Path

from experiments.analysis.analyze_thesis_stages import primary_rows, table
from experiments.runs import run_thesis_completion as driver


STUDIES = ('e0_20261012_v1', 'e1a_20261012_v1', 'e1exact_20261012_v1',
    'e1c_20261012_v1', 'e1c_poolbound_20261013_v1', 'e1b_20261013_v1',
    'e2_reference_20261012_v1', 'e2_refdiag_20261013_v1', 'e2_refcutoff_20261013_v1',
    'e2_20261013_v1', 'e3_pilot_20261012_v1', 'e3_20261013_v1', 'e4_20261013_v1',
    'e5_20261013_v1', 'e6_reference_20261012_v1', 'e6_baseline_20261012_v1',
    'e6_candidate_20261013_v1')


def verify(root):
    root = Path(root).resolve()
    inventory, attempts = [], []
    for name in STUDIES:
        study = root/name
        manifest, rows = primary_rows(study)  # Frozen sources, inputs, completion hashes.
        if any(r['status'] in ('RUNNING', 'NOT_RUN') for r in rows):
            raise ValueError(f'Stage still running: {name}')
        archives = manifest['source_archive_hashes']
        for path, digest in archives.items():
            if driver.sha(study/path) != digest:
                raise ValueError(f'Source archive changed: {name}/{path}')
        gate = driver.read(study/'stage_gate.json')
        if gate.get('certificate_contradictions'):
            raise ValueError(f'Certificate conflict: {name}')
        if not gate['ready'] and name != 'e1b_20261013_v1':
            raise ValueError(f'Unreviewed stage gate: {name}')
        audited = 0
        for row in rows:
            if row['valid']:
                path = row['_path']
                saved = driver.read(path/'audit.json')
                audit = saved['audit']
                if (saved['result_sha256'] != driver.sha(path/'result.json') or not audit['valid'] or
                        not audit['periodic_replay']['valid'] or audit['periodic_replay']['cycles'] != 2 or
                        not math.isclose(audit['objective'], row['objective'], rel_tol=1e-7, abs_tol=1e-6)):
                    raise ValueError(f'Invalid archived audit: {row["id"]}')
                audited += 1
            paths = driver.attempt_dirs(study, row)
            for index, path in enumerate(paths):
                if not driver.complete_attempt(path):
                    raise ValueError(f'Incomplete attempt: {path}')
                attempt = driver.read(path/'row.json')
                attempts.append(dict(study=name, id=row['id'], attempt=index+1, primary=index == 0,
                    status=attempt['status'], valid=attempt['valid'],
                    completion_sha256=driver.sha(path/'completion.json')))
        recovery = None
        if name == 'e1b_20261013_v1':
            from experiments.runs.recover_thesis_infrastructure import recovery_valid
            recovery = driver.read(study/'infrastructure_recovery/review.json')
            job = next(r for r in rows if r['id'] == recovery['job'])
            second = study/'runs'/job['id']/'attempt_002'
            if (not recovery['ready'] or not recovery['original_gate_preserved'] or gate['ready'] or
                    not recovery_valid(driver, second, job) or
                    driver.sha(second/'completion.json') != recovery['recovery_completion_sha256'] or
                    driver.sha(job['_path']/'row.json') != recovery['first_failure_row_sha256']):
                raise ValueError('E1b recovery evidence mismatch')
        inventory.append(dict(study=name, planned=len(rows), terminal=len(rows), audited=audited,
            statuses=dict(Counter(r['status'] for r in rows)), stage_gate_ready=gate['ready'],
            recovery_reviewed=bool(recovery), manifest_sha256=driver.sha(study/'manifest.json'),
            stage_gate_sha256=driver.sha(study/'stage_gate.json'), source_archives=archives))
    table(root/'delivery/attempt_inventory.csv', attempts)
    driver.write(root/'delivery/integrity_review.json', dict(
        studies=inventory, registered_primary_calls=sum(r['planned'] for r in inventory),
        audited_primary_calls=sum(r['audited'] for r in inventory), attempts=len(attempts),
        immutable_evidence_valid=True, solver_called=False, final_acceptance=False,
        note='Archive integrity only. Stage analysis, scientific limitations and final delivery still require review.',
        analysis_source_sha256=driver.sha(Path(__file__))))
    print(dict(studies=len(inventory), primary_calls=sum(r['planned'] for r in inventory), attempts=len(attempts)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='output/thesis_completion')
    verify(parser.parse_args().root)
