import json
from pathlib import Path

import pytest

from experiments.runs import recover_thesis_infrastructure as recovery
from experiments.runs import run_thesis_completion as driver


def test_transient_windows_replace_failure_recovers_without_partial_publication(tmp_path, monkeypatch):
    target = tmp_path/'live.json'
    target.write_text('{"old":true}')
    replace = recovery.os.replace
    seen = []

    def flaky(source, dest):
        seen.append(Path(source).read_text())
        if len(seen) < 3:
            assert json.loads(target.read_text()) == {'old': True}
            error = PermissionError('sharing violation')
            error.winerror = 5
            raise error
        replace(source, dest)

    monkeypatch.setattr(recovery.os, 'replace', flaky)
    monkeypatch.setattr(recovery.time, 'sleep', lambda _: None)
    recovery.write_with_retry(target, {'new': True})
    assert len(seen) == 3 and len(set(seen)) == 1
    assert json.loads(target.read_text()) == {'new': True}


@pytest.mark.parametrize('winerror,expected_calls', [(5, 8), (32, 8), (33, 8), (13, 1)])
def test_persistent_or_unrelated_permission_error_is_not_suppressed(tmp_path, monkeypatch, winerror, expected_calls):
    calls = []
    def fail(*args):
        calls.append(args)
        error = PermissionError('failure')
        error.winerror = winerror
        raise error
    monkeypatch.setattr(recovery.os, 'replace', fail)
    monkeypatch.setattr(recovery.time, 'sleep', lambda _: None)
    with pytest.raises(PermissionError):
        recovery.write_with_retry(tmp_path/'live.json', {'x': 1})
    assert len(calls) == expected_calls
    assert not (tmp_path/'live.json').exists()


def failure_study(tmp_path):
    job = dict(id='one', case_path='case.json', model=8, method='alns',
               version='frozen', seed=42, budget=300, counts=[1, 2, 3])
    first = tmp_path/'runs'/'one'/'attempt_001'
    row = driver.terminal_row(job, 'ERROR', "[WinError 5] denied: 'live.json.abc.tmp' -> 'live.json'")
    driver.finalize_attempt(first, row)
    driver.write(tmp_path/'stage_gate.json', dict(ready=False, failures=['one'],
                 terminal=1, certificate_contradictions=[]))
    return job, first, driver.sha(first/'row.json')


def test_only_complete_hash_pinned_infrastructure_failure_is_eligible(tmp_path):
    job, first, digest = failure_study(tmp_path)
    recovery.validate_failure(driver, tmp_path, {'jobs': [job]}, 'one', digest)
    with pytest.raises(ValueError, match='hash'):
        recovery.validate_failure(driver, tmp_path, {'jobs': [job]}, 'one', 'wrong')
    driver.write(first/'result.json', {})
    with pytest.raises(ValueError, match='pre-result'):
        recovery.validate_failure(driver, tmp_path, {'jobs': [job]}, 'one', digest)


def test_certificate_conflict_blocks_infrastructure_recovery(tmp_path):
    job, first, digest = failure_study(tmp_path)
    driver.write(tmp_path/'stage_gate.json', dict(ready=False, failures=['one'],
                 terminal=1, certificate_contradictions=[{'id': 'one'}]))
    with pytest.raises(ValueError, match='unresolved'):
        recovery.validate_failure(driver, tmp_path, {'jobs': [job]}, 'one', digest)


def test_missing_initial_attempt_blocks_recovery(tmp_path):
    job, _, digest = failure_study(tmp_path)
    with pytest.raises(ValueError, match='initial attempts'):
        recovery.validate_failure(driver, tmp_path, {'jobs': [job, dict(job, id='pending')]}, 'one', digest)


def test_recovery_requires_matching_configuration_and_two_cycle_audit(tmp_path):
    job, _, _ = failure_study(tmp_path)
    second = tmp_path/'runs'/'one'/'attempt_002'
    driver.write(second/'result.json', {'solution': 'fixture'})
    audit = dict(result_sha256=driver.sha(second/'result.json'),
                 audit=dict(valid=True, errors=[], periodic_replay=dict(valid=True)))
    driver.write(second/'audit.json', audit)
    row = driver.terminal_row(job, 'FEASIBLE')
    row.update(valid=True, periodic_valid=True, objective=10)
    driver.finalize_attempt(second, row, ('result.json', 'audit.json'))
    assert recovery.recovery_valid(driver, second, job)
    assert not recovery.recovery_valid(driver, second, dict(job, seed=43))
    audit['audit']['periodic_replay']['valid'] = False
    driver.write(second/'audit.json', audit)
    driver.finalize_attempt(second, row, ('result.json', 'audit.json'))
    assert not recovery.recovery_valid(driver, second, job)
