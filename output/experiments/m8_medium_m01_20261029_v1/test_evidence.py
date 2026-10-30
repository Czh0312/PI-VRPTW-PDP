"""Audit the single timing attempt without turning a limit into a proof."""
import json

import pytest

from probe import HERE, TOL, load, master, read, save, sha
from models.common.cycle.audit import audit_solution
from models.cost.comparison import evaluate_comparison_cost


def test_frozen_scope_and_actual_timing():
    manifest = read(HERE / 'manifest.json')
    row = read(HERE / 'summary.json')
    changes = read(HERE / 'source_changes.json')
    # Another ongoing task changed two M3-only files. Preserve that fact;
    # every input and file used by this M8 enumeration/audit must match.
    allowed = {str(HERE.parents[2] / 'models/routing/model_3/exact/milp_solver.py'),
               str(HERE.parents[2] / 'models/routing/model_3/exact/route_partition.py')}
    assert {item['path'] for item in changes} <= allowed
    assert all(sha(path) == digest for path, digest in manifest['hashes'].items() if path not in allowed)
    for item in changes:
        assert item['before'] == manifest['hashes'][item['path']]
        assert sha(item['path']) == item['after']
    assert row['original_sources_and_inputs_unchanged'] == (not changes)
    assert row['customers'] == 20 and row['case'] == 'M01'
    assert row['original_K'] == manifest['original_K'] == list(range(1, 21))
    assert manifest['time_tick_seconds'] == 1 and row['threads'] == 1 and row['seed'] == 42
    assert row['total_wall_seconds'] >= row['enumeration_seconds'] > 0
    assert row['historical_feasible_plan_used'] and not row['historical_lower_bound_used']
    assert not row['cold_start_end_to_end'] and not row['production_solver_modified']


def test_final_original_business_cost_and_two_cycle_audit():
    p = load()
    result = read(HERE / 'best_audited_result.json')
    row = read(HERE / 'summary.json')
    audit = audit_solution(p, result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    cost = evaluate_comparison_cost(p, result, p.cost_profile.config, audit=audit)['total']
    assert cost == pytest.approx(row['audited_upper_bound'], abs=TOL)
    assert row['selected_K'] == result['cycle_batch_count']
    assert row['selected_N'] == result['container_pool_size']
    assert row['global_lower_bound'] <= cost + TOL
    assert row['gap_over_upper'] == pytest.approx(max(0., cost-row['global_lower_bound'])/cost)
    save(HERE / 'independent_final_audit.json', audit)


def test_incomplete_enumeration_never_becomes_a_master_bound():
    row = read(HERE / 'summary.json')
    if not row['complete_route_library']:
        assert row['status'] == 'TIME_LIMIT_ENUMERATION'
        assert row['stopped_stage'] == 'trip_enumeration'
        assert not row['proven_optimal']
        assert not (HERE / 'trip_library.json').exists()
        assert not (HERE / 'input_only_bound.json').exists()
        assert row['lower_bound_source'] == 'necessary_pool_bound_only'
        assert row['total_wall_seconds'] >= row['budget_seconds']
        assert row['enumeration_seconds'] >= row['budget_seconds']-2
        assert row['last_enumeration_progress']['prefixes'] > 0
        physical = read(HERE / 'physical_bound.json')
        p = load()
        expected = (physical['physical']['pool_lower_bound'] * p.cost_profile.rates['container_pool_cost_per_period']
                    + sum(p.cost_profile.constant_cost(p.total).values()))
        assert row['global_lower_bound'] == pytest.approx(expected)
        assert physical['transport_costs_relaxed']
    else:
        library = read(HERE / 'trip_library.json')
        p = load()
        assert len(library) == row['route_columns']
        assert len({r['mask'] for r in library}) == len(library)
        assert all(0 < r['mask'] < (1 << (2*len(p.orders))) for r in library)
        assert all(r['deliveries'] == r['mask'] & ((1 << len(p.orders))-1) for r in library)
        if (HERE / 'input_only_bound.json').exists():
            raw = read(HERE / 'input_only_bound.json')
            assert raw['complete_route_library']
            assert not raw['prior_U_or_L_supplied'] and not raw['K_or_N_fixed']


def test_optimality_claim_requires_closure_and_independent_replay():
    row = read(HERE / 'summary.json')
    assert row['proven_optimal'] == (row['absolute_gap'] <= TOL)
    if row['proven_optimal']:
        # An original feasible upper bound plus a fresh independent full-K
        # lower bound is sufficient. Never supply a known optimum as LB.
        import gurobipy as gp
        p = load()
        m, ctx, _ = master(p, read(HERE / 'trip_library.json'))
        try:
            m.Params.Seed = 101
            m.Params.Threads = 1
            m.Params.TimeLimit = 120
            m.optimize()
            assert m.ObjBound >= row['audited_upper_bound']-TOL
            save(HERE / 'independent_bound_replay.json', dict(status=m.Status, bound=m.ObjBound,
                seconds=m.Runtime, seed=101, known_optimum_floor_used=False, cost_cap_used=False))
        finally:
            m.dispose()
    else:
        assert row['status'] != 'OPTIMAL_CERTIFICATE'
        assert row['global_lower_bound'] < row['audited_upper_bound']-TOL
