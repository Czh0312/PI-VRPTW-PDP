"""Final evidence validation; run only after all nine native solves finish."""
import hashlib
import json
from pathlib import Path

import pytest

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


@pytest.mark.parametrize('case',[f'S{i:02}' for i in range(1,10)])
def test_native_global_optimum_has_cold_start_provenance_and_two_cycle_audit(case):
    folder=HERE/'runs'/case
    row,result,audit=(read(folder/name) for name in ('summary.json','result.json','audit.json'))
    assert row['status']=='OPTIMAL' and row['proven_optimal']
    assert row['valid'] and row['periodic_valid'] and audit['valid'] and audit['periodic_replay']['valid']
    assert result['info']['proven_optimal'] and result['info']['complete_count_domain']
    assert row['original_K']==list(range(1,8)) and row['seed']==42
    assert not row['external_initial_solution'] and not row['historical_solution_read']
    assert not row['known_optimum_target_used'] and row['blocked_read_count']==0
    assert row['objective']==pytest.approx(audit['objective'],abs=1e-6)
    assert row['best_bound']==pytest.approx(row['objective'],abs=1e-6)
    assert len(row['optimize_calls'])==1
    call=row['optimize_calls'][0]
    assert call['NumStart']==0 and call['threads']==1 and call['seed']==42
    assert call['MIPGap']==call['MIPGapAbs']==0 and call['time_limit']>=1e11
    assert row['total_cold_solve_seconds']>=row['build_seconds']>=0
    if row['first_native_integer_solution_seconds'] is not None:
        assert 0<=row['first_native_integer_solution_seconds']<=row['total_cold_solve_seconds']
    assert all('/output/' not in path.replace('\\','/') for path in row['input_read_paths'])


def test_all_cases_finished_and_raw_inputs_frozen_source_drivers_are_unchanged():
    plan,status=read(HERE/'plan.json'),read(HERE/'batch_status.json')
    assert status['stage']=='complete' and status['completed']==[f'S{i:02}' for i in range(1,10)]
    assert status['source_and_input_hashes_unchanged']
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    assert all(sha(HERE/name)==digest for name,digest in plan['driver_hashes'].items())
    assert {p.relative_to(HERE/'source').as_posix():sha(p) for p in (HERE/'source').rglob('*.py')}==plan['source_hashes']
    assert all(sha(Path(path))==digest for path,digest in plan['input_hashes'].items())
    assert all(sha(HERE/'inputs'/Path(path).relative_to(ROOT))==digest for path,digest in plan['input_hashes'].items())
    assert plan['practical_time_cap'] is None and plan['known_optimum_target_used'] is False


def test_every_recorded_native_incumbent_is_separate_from_final_proof():
    for index in range(1,10):
        folder=HERE/'runs'/f'S{index:02}'
        row,events=read(folder/'summary.json'),read(folder/'incumbents.json')
        assert all(e['elapsed_seconds']<=row['total_cold_solve_seconds'] for e in events)
        assert all(e['objective']>=row['objective']-1e-5 for e in events)
