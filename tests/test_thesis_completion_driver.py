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
