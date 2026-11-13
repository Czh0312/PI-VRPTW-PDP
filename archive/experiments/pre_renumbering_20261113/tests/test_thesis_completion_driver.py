"""Evidence integrity, exclusive ownership and recovery of experiment attempts."""
from argparse import Namespace
import json
import os
import subprocess
import sys
import hashlib
import zipfile
from types import SimpleNamespace

import pytest

from experiments.runs import run_thesis_completion as driver


def study(tmp_path):
    job = dict(id='case_m6_exact_original_k3_t1_s42', case='case', model=6,
               method='exact', version='original', seed=42, budget=1)
    manifest = dict(jobs=[job], workers=1, source_hashes={}, input_hashes={},
                    total_call_budget=1, phase='test', timeout_overhead=10)
    driver.write(tmp_path/'manifest.json', manifest)
    driver.write(tmp_path/'manifest.sha256.json', dict(sha256=driver.sha(tmp_path/'manifest.json')))
    return manifest, job


def test_hash_change_blocks_resume_before_worker_launch(tmp_path, monkeypatch):
    manifest, _ = study(tmp_path)
    manifest['workers'] = 2
    driver.write(tmp_path/'manifest.json', manifest)
    monkeypatch.setattr(driver, 'execute', lambda *a: pytest.fail('must not launch'))
    with pytest.raises(ValueError, match='manifest changed'):
        driver.run(Namespace(output=str(tmp_path), retry_failed=False))


def test_missing_stage_gate_prevents_launch(tmp_path, monkeypatch):
    manifest, _ = study(tmp_path)
    manifest['prerequisites'] = [str(tmp_path/'prior')]
    driver.write(tmp_path/'manifest.json', manifest)
    driver.write(tmp_path/'manifest.sha256.json', dict(sha256=driver.sha(tmp_path/'manifest.json')))
    monkeypatch.setattr(driver, 'execute', lambda *a: pytest.fail('must not launch without prerequisite'))
    with pytest.raises(RuntimeError, match='Prerequisite'):
        driver.run(Namespace(output=str(tmp_path), retry_failed=False))


def test_continuation_stops_on_predecessor_failure(tmp_path):
    previous, following = tmp_path/'previous', tmp_path/'following'
    driver.write(previous/'pipeline.json', dict(state='NEEDS_REVIEW'))
    driver.pipeline(Namespace(output=str(following), studies=[], after_pipeline=str(previous)))
    assert driver.read(following/'pipeline.json')['state'] == 'NEEDS_REVIEW'


def test_continuation_waits_until_predecessor_completed(tmp_path, monkeypatch):
    previous, following = tmp_path/'previous', tmp_path/'following'
    driver.write(previous/'pipeline.json', dict(state='RUNNING'))
    sleeps = []
    def advance(seconds):
        sleeps.append(seconds)
        assert driver.read(following/'pipeline.json')['state'] == 'WAITING'
        driver.write(previous/'pipeline.json', dict(state='COMPLETED'))
    monkeypatch.setattr(driver.time, 'sleep', advance)
    driver.pipeline(Namespace(output=str(following), studies=[], after_pipeline=str(previous)))
    assert sleeps == [30]
    assert driver.read(following/'pipeline.json')['state'] == 'COMPLETED'


def test_extended_calendar_cannot_claim_infeasible_against_standard_witness(tmp_path):
    manifest, job = study(tmp_path)
    jobs = [dict(job, id=group, group=group, mechanism='same_input', counts=[2])
            for group in ('standard_m7', 'delayed_m7')]
    manifest['jobs'] = jobs
    driver.write(tmp_path/'manifest.json', manifest)
    driver.write(tmp_path/'manifest.sha256.json', dict(sha256=driver.sha(tmp_path/'manifest.json')))
    (tmp_path/'tables').mkdir()
    for index, item in enumerate(jobs):
        row = driver.terminal_row(item, 'FEASIBLE' if index == 0 else 'INFEASIBLE')
        row.update(valid=index == 0, objective=10 if index == 0 else None, proven_infeasible=index == 1)
        driver.finalize_attempt(tmp_path/'runs'/item['id']/'attempt_001', row)
    gate = driver.analyze(tmp_path)
    assert not gate['ready']
    assert gate['certificate_contradictions'][0]['subset_reference'] == 'standard_m7'


def test_corrupted_completed_result_is_not_silently_skipped(tmp_path):
    _, job = study(tmp_path)
    path = tmp_path/'runs'/job['id']/'attempt_001'
    driver.finalize_attempt(path, driver.terminal_row(job, 'ERROR', 'original failure'))
    assert driver.complete_attempt(path)
    driver.write(path/'row.json', driver.terminal_row(job, 'OPTIMAL'))
    with pytest.raises(ValueError, match='changed'):
        driver.complete_attempt(path)


def test_retry_preserves_first_failure_denominator(tmp_path):
    manifest, job = study(tmp_path)
    first = tmp_path/'runs'/job['id']/'attempt_001'
    second = tmp_path/'runs'/job['id']/'attempt_002'
    driver.finalize_attempt(first, driver.terminal_row(job, 'ERROR', 'first'))
    row = driver.terminal_row(job, 'OPTIMAL')
    row.update(valid=True, objective=5, proven_optimal=True)
    driver.finalize_attempt(second, row)
    summary = driver.summarize(tmp_path, manifest)
    assert summary['terminal'] == 1 and summary['attempts'] == 2
    assert summary['audited_feasible'] == summary['optimal'] == 0
    assert summary['statuses'] == {'ERROR': 1}


def test_recovery_retains_interrupted_attempt_and_requires_explicit_retry(tmp_path, monkeypatch):
    _, job = study(tmp_path)
    first = tmp_path/'runs'/job['id']/'attempt_001'
    driver.write(first/'partial.json', dict(evidence='preserved'))
    monkeypatch.setattr(driver, 'execute', lambda *a: pytest.fail('retry requires explicit flag'))
    driver.run(Namespace(output=str(tmp_path), retry_failed=False))
    assert driver.read(first/'row.json')['status'] == 'INTERRUPTED'
    assert driver.read(first/'partial.json') == {'evidence': 'preserved'}


def test_recovery_blocks_surviving_worker_without_signalling_it(tmp_path, monkeypatch):
    _, job = study(tmp_path)
    first = tmp_path/'runs'/job['id']/'attempt_001'
    driver.write(first/'launch.json', dict(pid=os.getpid()))
    monkeypatch.setattr(driver, 'execute', lambda *a: pytest.fail('must not launch'))
    assert driver.pid_alive(os.getpid())
    with pytest.raises(RuntimeError, match='still be alive'):
        driver.run(Namespace(output=str(tmp_path), retry_failed=False))
    assert not (first/'completion.json').exists()


def test_os_lock_rejects_competing_driver(tmp_path):
    code = "from pathlib import Path; from experiments.runs.run_thesis_completion import study_lock; import sys\nwith study_lock(Path(sys.argv[1])): print('acquired')"
    with driver.study_lock(tmp_path):
        result = subprocess.run([sys.executable, '-c', code, str(tmp_path)], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Another driver' in result.stderr
    with driver.study_lock(tmp_path):
        pass


def test_all_planned_jobs_remain_in_pending_summary(tmp_path):
    manifest, _ = study(tmp_path)
    summary = driver.summarize(tmp_path, manifest)
    assert summary['planned'] == 1 and summary['terminal'] == 0
    assert summary['statuses'] == {'NOT_RUN': 1}
    assert summary['state'] == 'IN_PROGRESS'


def test_source_archive_restores_exact_bytes_and_refuses_modified_source(tmp_path):
    manifest, _ = study(tmp_path)
    name = 'sources/original/models/example.py'
    payload = b'x = 1\n'
    with zipfile.ZipFile(tmp_path/'source_original.zip', 'w') as archive:
        archive.writestr(name, payload)
    manifest['source_hashes'] = {name: hashlib.sha256(payload).hexdigest()}
    manifest['source_archive_hashes'] = {'source_original.zip': driver.sha(tmp_path/'source_original.zip')}
    driver.write(tmp_path/'manifest.json', manifest)
    driver.write(tmp_path/'manifest.sha256.json', dict(sha256=driver.sha(tmp_path/'manifest.json')))
    driver.restore_sources(tmp_path)
    assert (tmp_path/name).read_bytes() == payload
    (tmp_path/name).write_text('modified', encoding='utf-8')
    with pytest.raises(ValueError, match='overwrite changed source'):
        driver.restore_sources(tmp_path)


def test_e0_gate_rejects_lower_bound_above_audited_alns_cost(tmp_path):
    manifest, job = study(tmp_path)
    alns = {**job, 'id': 'alns', 'method': 'alns'}
    manifest['jobs'].append(alns)
    driver.write(tmp_path/'manifest.json', manifest)
    driver.write(tmp_path/'manifest.sha256.json', dict(sha256=driver.sha(tmp_path/'manifest.json')))
    for item, cost, bound in [(job, 12, 11), (alns, 10, None)]:
        row = driver.terminal_row(item, 'FEASIBLE')
        row.update(valid=True, objective=cost, best_bound=bound)
        driver.finalize_attempt(tmp_path/'runs'/item['id']/'attempt_001', row)
    (tmp_path/'tables').mkdir()
    gate = driver.analyze(tmp_path)
    assert not gate['ready']
    assert len(gate['certificate_contradictions']) == 1


def test_e0_gate_allows_ordinary_time_limits(tmp_path):
    manifest, job = study(tmp_path)
    driver.finalize_attempt(tmp_path/'runs'/job['id']/'attempt_001', driver.terminal_row(job, 'TIME_LIMIT'))
    (tmp_path/'tables').mkdir()
    assert driver.analyze(tmp_path)['ready']


def test_observer_waits_for_outer_audit_and_preserves_solver_inputs():
    trace, history, called = [], [], []
    problem = SimpleNamespace(operating_policy=SimpleNamespace(batches=[1], time_tick_subdivision=1))

    def candidate(child, **kwargs):
        called.append(kwargs)
        return dict(info=dict(objective=7, status='FEASIBLE'), audit=dict(valid=True))

    def aggregate(problem, *, solve_candidate, **kwargs):
        rejected = solve_candidate(problem, seed=42, max_time=3, max_iterations=5)
        assert not history  # Child return alone does not confirm outer acceptance.
        rejected['audit']['valid'] = False
        accepted = solve_candidate(problem, seed=43, max_time=2, max_iterations=4)
        assert history[-1]['objective'] is None
        return accepted

    observed = driver.observe_counts(aggregate, trace, history, driver.time.perf_counter())
    result = observed(problem, solve_candidate=candidate)
    assert result['info']['objective'] == history[-1]['objective'] == 7
    assert [c['seed'] for c in called] == [42, 43]
    assert [c['budget'] for c in trace] == [3, 2]
    assert len(trace) == len(history) == 2


@pytest.mark.parametrize('number', [6, 7, 8])
def test_worker_records_unified_milp_without_per_count_diagnostics(tmp_path, monkeypatch, number):
    """Run the real new solver through the formal worker's evidence pipeline."""
    import csv
    from tests.routing.test_unified_batch_milp import instance
    import tools.configuration.loader as loader
    import models.common.cycle as cycle
    p = instance(number)
    config = {'economic_config': p.cost_profile.config}
    monkeypatch.setattr(loader, 'load_routing_case', lambda *a, **k: (config, p.fleet))
    monkeypatch.setattr(cycle, 'load_instance', lambda *a, **k: p)
    # Keep the temporary source path from escaping this test's interpreter.
    monkeypatch.setattr(driver.sys, 'path', list(driver.sys.path))
    job = dict(id=f'unified_m{number}', case='tiny', model=number, method='exact',
               version='current', seed=42, budget=10, iterations=0, case_path='tiny.json')
    driver.write(tmp_path/'manifest.json', dict(jobs=[job], source_hashes={}, input_hashes={}))
    driver.write(tmp_path/'manifest.sha256.json', dict(sha256=driver.sha(tmp_path/'manifest.json')))
    driver.worker(Namespace(output=str(tmp_path), job=job['id'], attempt=1))
    path = tmp_path/'runs'/job['id']/'attempt_001'
    row = driver.read(path/'row.json')
    assert row['valid'] and row['proven_optimal'] and row['complete_count_domain']
    assert row['exact_strategy'] == 'gurobi_unified_milp'
    assert row['candidate_count'] == 2 and row['visits'] == 0
    assert driver.read(path/'candidate_diagnostics.json') == []
    history = list(csv.DictReader((path/'anytime.csv').open()))
    assert len(history) == 2 and history[-1]['source'] == 'confirmed_call_return'
    assert float(history[-1]['objective']) == pytest.approx(row['objective'])
    assert int(history[-1]['batch_count']) == row['k']
