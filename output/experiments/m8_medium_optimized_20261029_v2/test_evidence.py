"""Audit final M01 scope, all native columns, and honest limit reporting."""
import pytest

from study import HERE, load, read, save, sha
from audit_native import audit_all
from models.common.cycle.audit import audit_solution
from models.cost.comparison import evaluate_comparison_cost


def test_frozen_full_domain_and_actual_attempt():
    manifest = read(HERE / 'manifest.json')
    row = read(HERE / 'summary.json')
    assert all(sha(path) == expected for path, expected in manifest['hashes'].items())
    assert row['original_sources_and_inputs_unchanged']
    assert row['customers'] == 20 and row['original_K'] == list(range(1, 21))
    assert manifest['time_tick_seconds'] == 1
    assert row['threads'] == 1 and row['seed'] == 42
    assert row['complete_route_library'] and row['route_columns'] == 1268786
    assert row['enumerated_prefixes'] == 285828896
    assert row['total_wall_seconds'] >= row['compile_seconds']+row['enumeration_seconds']+row['master_build_seconds']
    assert not row['historical_lower_bound_used'] and row['historical_feasible_plan_used']
    assert not row['cold_start_end_to_end']


def test_all_archived_columns_independently_replay():
    row = read(HERE / 'summary.json')
    result = audit_all()
    assert result['valid'] and result['route_columns'] == row['route_columns']
    assert result['exhaustive_prefixes'] == row['enumerated_prefixes']
    save(HERE / 'independent_route_audit.json', result)


def test_final_original_cost_business_and_limit_status():
    p = load()
    result = read(HERE / 'best_audited_result.json')
    row = read(HERE / 'summary.json')
    audit = audit_solution(p, result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    cost = evaluate_comparison_cost(p, result, p.cost_profile.config, audit=audit)['total']
    assert cost == pytest.approx(row['audited_upper_bound'], abs=1e-6)
    assert row['global_lower_bound'] <= cost+1e-6
    assert row['proven_optimal'] == (row['absolute_gap'] <= 1e-6)
    assert row['status'] != 'ERROR'
    raw = read(HERE / 'input_only_bound.json')
    assert not raw['prior_U_or_L_supplied'] and not raw['K_or_N_fixed']
    if not raw['finite_native_bound_available']:
        assert raw['lower_bound'] is None and not row['proven_optimal']
    if not row['proven_optimal']:
        assert row['status'] == 'TIME_LIMIT'
    save(HERE / 'independent_final_audit.json', audit)
