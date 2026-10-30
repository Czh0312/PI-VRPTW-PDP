"""Validate exactly this frozen 36-run cold-start comparison; no extra solves."""
import hashlib
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


@pytest.mark.parametrize('seconds',[5,30])
@pytest.mark.parametrize('case',[f'S{i:02}' for i in range(1,10)])
@pytest.mark.parametrize('method',['alns','exact'])
def test_cold_run_has_complete_evidence_and_preserves_raw_domain(seconds,case,method):
    folder = HERE/'runs'/f'b{seconds:02}'/case/method
    row,result = read(folder/'summary.json'),read(folder/'result.json')
    audit = read(folder/'audit.json')
    assert row['seed'] == 42 and row['budget_seconds'] == seconds
    assert row['original_K'] == list(range(1,8))
    assert not row['external_initial_solution'] and not row['historical_data_read']
    assert not row['exact_reference_used'] and row['read_guard_blocked_count'] == 0
    assert row['input_loading_seconds'] <= row['solver_wall_seconds']
    if row['valid']:
        assert audit['valid'] and audit['periodic_replay']['valid']
        assert row['objective'] == pytest.approx(audit['objective'])
    else:
        assert row['objective'] is None and not row['proven_optimal']
        assert row['status'] in ('TIME_LIMIT','TIME_LIMIT_BUILD','INFEASIBLE')
    if method == 'exact':
        assert all(call['NumStart'] == 0 and call['threads'] == 1 for call in row['optimize_calls'])
        assert not result['info'].get('mip_start_supplied',False)
    else:
        assert result['info']['initial_incumbent_cost'] is None
        assert not result['info']['proven_optimal']


def test_frozen_worker_source_inputs_and_driver_are_unchanged():
    plan = read(HERE/'plan.json')
    sha = lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    assert sha(HERE/'bench.py') == plan['driver_sha256']
    actual = {p.relative_to(HERE/'source').as_posix():sha(p) for p in (HERE/'source').rglob('*.py')}
    assert actual == plan['source_hashes']
    assert all(sha(Path(p)) == digest for p,digest in plan['input_hashes'].items())
    assert read(HERE/'completion.json')['jobs'] == 36


def test_scoring_counts_failures_and_never_turns_no_incumbent_into_zero_gap():
    analysis = read(HERE/'analysis.json')
    assert analysis['root_seeds'] == [42] and not analysis['historical_solutions_read']
    assert len(analysis['rows']) == 36
    for row in analysis['rows']:
        if not row['valid']:
            assert row['gap_percent'] is None and not row['known_optimum_hit']
        else:
            assert row['gap_percent'] >= -1e-5
    for group in analysis['budgets'].values():
        for data in group['metrics'].values():
            assert data['cases'] == 9
            assert data['mean_gap_all_cases_percent'] is None if data['feasible'] < 9 else data['mean_gap_all_cases_percent'] is not None
