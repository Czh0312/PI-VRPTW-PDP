"""Complete-cycle audits of restricted route-pool primal improvements."""
from copy import deepcopy
from dataclasses import replace
import math
import time

import pytest

from models.common.cycle import MultiBatchInstance
from models.common.cycle.audit import audit_solution
from models.routing.model_5.exact.multibatch_route_pool import candidate_pools, reoptimize_route_pool
from tests.routing.test_multibatch_cycle import fixture, hand_solution
from tests.routing.test_multibatch_departure_loads import nonmetric_problem
from tests.routing.test_multibatch_primal_quality import quality_fixture


@pytest.mark.parametrize('seed', range(6))
@pytest.mark.parametrize('transfers', [False, True])
def test_pool_cannot_beat_known_full_optimum_and_does_not_claim_global_proof(seed, transfers):
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.engine import solve_exact
    p = nonmetric_problem(seed)
    initial = solve_exact(p, time_limit=5)
    assert initial['info']['proven_optimal']
    copied = deepcopy(initial)
    result = reoptimize_route_pool(p, initial, time_limit=2, transfers=transfers)
    assert initial == copied
    assert audit_solution(p, result)['valid']
    assert result['info']['objective'] == pytest.approx(initial['info']['objective'])
    assert result['info']['best_bound'] is None and result['info']['mip_gap'] is None
    assert not result['info']['proven_optimal'] and not result['info']['proven_infeasible']
    assert not result['info']['route_pool_complete']


def test_reordering_improves_actual_e2_path_and_matches_full_optimum():
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.multibatch_primal import construct_multibatch_incumbent
    from models.routing.model_5.exact.engine import solve_exact
    p = quality_fixture()
    initial = construct_multibatch_incumbent(p, time.perf_counter()+2, improve=False)
    result = reoptimize_route_pool(p, initial, time_limit=2, transfers=False)
    assert initial['info']['objective'] == 6 and result['info']['objective'] == 4
    assert audit_solution(p, result)['valid']
    assert result['info']['route_pool_subproblem_optimal'] and not result['info']['proven_optimal']
    full = solve_exact(p, time_limit=5, initial_result=result)
    assert full['info']['proven_optimal'] and full['info']['objective'] == 4


@pytest.mark.parametrize('unload', [29, 30, 31])
def test_cutoff_and_clean_release_boundary(unload):
    pytest.importorskip('gurobipy')
    p = fixture(pool=2, cleaning=20)
    initial = hand_solution(p, unload)
    result = reoptimize_route_pool(p, initial, time_limit=2)
    assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
    assert result['info']['objective'] <= audit_solution(p, initial)['objective']


def test_positive_e1_turnaround_retains_physical_vehicle_model():
    pytest.importorskip('gurobipy')
    p = fixture(pool=2, e1_turnaround_time=2, e2_dispatch_prep_time=2)
    initial = hand_solution(p)
    for trip in initial['e2_trips']:
        trip['departure'] += 2
        trip['events'][0]['start'] += 2
    result = reoptimize_route_pool(p, initial, time_limit=2)
    assert result['info']['route_pool_backend'] == 'indexed' and result['audit']['valid']


def test_zero_times_remain_causally_valid_and_original_ids_are_not_modified():
    pytest.importorskip('gurobipy')
    p = fixture(pool=2, zero=True, cleaning=0)
    initial = hand_solution(p)
    initial['e2_trips'][0]['trip_id'] = 'polish_e1_b1_0'
    result = reoptimize_route_pool(p, initial, time_limit=2)
    assert result['audit']['valid'] and result['info']['objective'] == 0
    assert len({t['trip_id'] for t in result['e1_trips']+result['e2_trips']}) == len(result['e1_trips'])+len(result['e2_trips'])


def test_pool_pruning_always_keeps_original_and_timeout_preserves_incumbent():
    p = fixture(pool=2)
    initial = hand_solution(p)
    pools = candidate_pools(p, initial, max_columns=1, deadline=0)
    assert all(len(row) == 1 for row in pools)
    result = reoptimize_route_pool(p, initial, time_limit=1e-12)
    assert result['audit']['valid'] and result['info']['objective'] == 44
    assert result['info']['route_pool_status'] == 'TIME_LIMIT_BUILD'
    for value in (0, -1, math.inf, math.nan):
        with pytest.raises(ValueError):
            reoptimize_route_pool(p, initial, time_limit=value)
    for value in (0, True, 1.5):
        with pytest.raises(ValueError):
            reoptimize_route_pool(p, initial, max_columns=value)
        with pytest.raises(ValueError):
            reoptimize_route_pool(p, initial, max_passes=value)
    with pytest.raises(ValueError, match='independent audit'):
        reoptimize_route_pool(p, {})
    with pytest.raises(ValueError, match='multibatch M5'):
        reoptimize_route_pool(fixture(one_batch=True), {})


def three_slot_case(prep=0, zero=False):
    base = fixture(pool=3, e2_max_trips_per_vehicle=3, e2_dispatch_prep_time=prep, zero=zero)
    p = MultiBatchInstance(base.network,
        [base.orders[0], replace(base.orders[0], id='extra'), base.orders[1]], base.operating_policy)
    initial = hand_solution(base)
    initial['batches'][0]['container_demand'] = 2
    for event in initial['e1_trips'][0]['events']:
        event['quantity'] = 2
    initial['e2_trips'][1]['trip_index'] = 2
    initial['e2_trips'].insert(1, dict(trip_id='extra_trip', satellite_id='S', vehicle_id=0,
        trip_index=1, departure=18, return_complete=23, events=[
            dict(kind='d', order_id='extra', start=19, quantity=1),
            dict(kind='p', order_id='extra', start=22, quantity=1)]))
    for trip in initial['e2_trips']:
        trip['departure'] += prep
        trip['events'][0]['start'] += prep
    assert audit_solution(p, initial)['valid']
    return p, initial


def test_whole_order_transfer_merges_two_trips_without_losing_any_task():
    pytest.importorskip('gurobipy')
    p, initial = three_slot_case()
    result = reoptimize_route_pool(p, initial, time_limit=2)
    assert result['audit']['valid']
    assert result['info']['objective'] == 44 < audit_solution(p, initial)['objective']
    assert len(result['e2_trips']) == 2
    keys = [(e['order_id'], e['kind']) for t in result['e2_trips'] for e in t['events']]
    assert len(keys) == len(set(keys)) == 6


def test_physical_vehicle_links_survive_inactive_intermediate_slot():
    gp = pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.multibatch_e1_routes import build_e1_routes
    from models.routing.model_5.exact.multibatch_route_pool import add_route_pool
    p, initial = three_slot_case()
    # Slot 1 is absent; slot 0 cannot stay out beyond slot 2's departure.
    initial['e2_trips'].pop(1)
    p = MultiBatchInstance(p.network, [p.orders[0], p.orders[2]], p.operating_policy)
    initial['batches'][0]['container_demand'] = 1
    for event in initial['e1_trips'][0]['events']:
        event['quantity'] = 1
    pools = candidate_pools(p, initial, max_columns=1)
    m, ctx = build_e1_routes(p)
    try:
        add_route_pool(p, m, ctx, initial, pools)
        m.addConstr(ctx['pool_slots'][0]['end'] == 75)
        m.addConstr(ctx['pool_slots'][1]['start'] == 70)
        m.optimize()
        assert m.Status == gp.GRB.INFEASIBLE
    finally:
        m.dispose()


def test_busy_vehicle_preparation_is_part_of_route_pool_timeline():
    gp = pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.multibatch_e1_routes import build_e1_routes
    from models.routing.model_5.exact.multibatch_route_pool import add_route_pool
    p, initial = three_slot_case(prep=2)
    pools = candidate_pools(p, initial, max_columns=1)
    m, ctx = build_e1_routes(p)
    try:
        add_route_pool(p, m, ctx, initial, pools)
        m.addConstr(ctx['pool_slots'][0]['end'] == 18)
        m.addConstr(ctx['pool_slots'][1]['start'] == 19)
        m.optimize()
        assert m.Status == gp.GRB.INFEASIBLE
    finally:
        m.dispose()


def test_zero_time_cross_trip_precedence_cycle_is_excluded():
    gp = pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.multibatch_e1_routes import build_e1_routes
    from models.routing.model_5.exact.multibatch_route_pool import add_route_pool
    from models.routing.model_5.exact.pickup_route_pricing import evaluate_column, route_context
    p, initial = three_slot_case(zero=True)
    context = route_context(p, 'S')
    pools = [[evaluate_column(p, context, [('extra', 'p'), ('o1', 'd')])],
             [evaluate_column(p, context, [('o1', 'p'), ('extra', 'd')])],
             [evaluate_column(p, context, [('o2', 'd'), ('o2', 'p')])]]
    assert all(row[0] is not None for row in pools)
    m, ctx = build_e1_routes(p)
    try:
        add_route_pool(p, m, ctx, initial, pools)
        m.optimize()
        assert m.Status == gp.GRB.INFEASIBLE
    finally:
        m.dispose()


def test_public_warm_start_preprocessing_shares_original_total_budget():
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact import reoptimize_route_pool as public, solve_exact
    p = fixture(pool=2)
    assert public(p, hand_solution(p), time_limit=1)['audit']['valid']
    result = solve_exact(p, initial_result=hand_solution(p), time_limit=2,
                         e1_polish_time=.25, route_pool_time=.5)
    assert result['audit']['valid'] and result['info']['proven_optimal']
    assert result['info']['route_pool']['route_pool_complete'] is False
    assert result['info']['runtime'] < 2.25
    automatic = solve_exact(p, time_limit=2, route_pool_time=.5)
    assert automatic['audit']['valid'] and automatic['info']['route_pool'] is not None
    for value in (-1, math.inf, math.nan):
        with pytest.raises(ValueError, match='route_pool_time'):
            solve_exact(p, route_pool_time=value)
