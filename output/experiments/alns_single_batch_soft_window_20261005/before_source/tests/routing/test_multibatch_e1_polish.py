"""Full-audit checks of E1 polishing with fixed E2 paths and actual times."""
from copy import deepcopy
from dataclasses import replace
import math

import pytest

from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle import MultiBatchInstance
from models.routing.solvers.exact.multibatch_e1_polish import reoptimize_e1
from tests.routing.test_multibatch_cycle import fixture, hand_solution
from tests.routing.test_multibatch_departure_loads import nonmetric_problem


def paths(result):
    return [(t['satellite_id'], t['vehicle_id'], t['trip_index'],
             [(e['order_id'], e['kind'], e.get('quantity')) for e in t['events']]) for t in result['e2_trips']]


@pytest.mark.parametrize('seed', range(6))
@pytest.mark.parametrize('retime', [False, True])
def test_polishing_preserves_full_feasibility_and_cannot_beat_known_global_optimum(seed, retime):
    pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.solver import solve_exact
    p = nonmetric_problem(seed)
    initial = solve_exact(p, time_limit=5)
    assert initial['info']['proven_optimal'] and initial['audit']['valid']
    copied = deepcopy(initial)
    result = reoptimize_e1(p, initial, time_limit=2, retime_e2=retime)
    assert initial == copied
    assert audit_solution(p, result)['valid']
    assert result['info']['objective'] == pytest.approx(initial['info']['objective'])
    assert paths(result) == paths(initial)
    if not retime:
        assert result['e2_trips'] == initial['e2_trips']
    assert result['info']['best_bound'] is None and result['info']['mip_gap'] is None
    assert not result['info']['proven_optimal'] and not result['info']['proven_infeasible']


@pytest.mark.parametrize('unload', [29, 30, 31])
@pytest.mark.parametrize('retime', [False, True])
def test_cutoff_quantities_and_exact_clean_release_remain_fully_audited(unload, retime):
    pytest.importorskip('gurobipy')
    p = fixture(pool=2, cleaning=20)
    original = hand_solution(p, unload)
    a = audit_solution(p, original)
    assert a['valid']
    result = reoptimize_e1(p, original, time_limit=2, retime_e2=retime)
    assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
    assert result['info']['objective'] <= a['objective']+1e-6
    if not retime:
        assert result['audit']['cutoff_snapshots'][0]['quantity'] == a['cutoff_snapshots'][0]['quantity']


def test_positive_e1_turnaround_uses_physical_vehicle_model():
    pytest.importorskip('gurobipy')
    p = fixture(pool=2, e1_turnaround_time=2, e2_dispatch_prep_time=2)
    original = hand_solution(p)
    for trip in original['e2_trips']:
        trip['departure'] += 2
        trip['events'][0]['start'] += 2
    assert audit_solution(p, original)['valid']
    result = reoptimize_e1(p, original, time_limit=2, retime_e2=True)
    assert result['info']['e1_polish_backend'] == 'indexed'
    assert result['audit']['valid'] and paths(result) == paths(original)
    with pytest.raises(ValueError, match='zero E1 turnaround'):
        reoptimize_e1(p, original, backend='patterns')


def test_zero_time_causality_and_trip_id_collisions_are_preserved():
    pytest.importorskip('gurobipy')
    p = fixture(pool=2, zero=True, cleaning=0)
    original = hand_solution(p)
    original['e2_trips'][0]['trip_id'] = 'polish_e1_b1_0'
    result = reoptimize_e1(p, original, time_limit=2, retime_e2=True)
    assert result['audit']['valid'] and result['info']['objective'] <= audit_solution(p, original)['objective']
    assert len({t['trip_id'] for t in result['e1_trips']+result['e2_trips']}) == len(result['e1_trips'])+len(result['e2_trips'])


def test_timeout_preserves_incumbent_without_global_subproblem_proof(tmp_path):
    p = fixture(pool=2)
    initial = hand_solution(p)
    result = reoptimize_e1(p, initial, time_limit=1e-12, retime_e2=True)
    assert result['audit']['valid'] and result['info']['objective'] == 44
    assert result['info']['e1_polish_status'] == 'TIME_LIMIT_BUILD'
    assert result['info']['best_bound'] is None and not result['info']['proven_optimal']
    for budget in (0, -1, math.inf, math.nan):
        with pytest.raises(ValueError):
            reoptimize_e1(p, initial, time_limit=budget)
    with pytest.raises(ValueError, match='independent audit'):
        reoptimize_e1(p, {})
    with pytest.raises(ValueError, match='multibatch M5'):
        reoptimize_e1(fixture(one_batch=True), {})


def test_public_entry_and_milp_budget_include_polishing():
    pytest.importorskip('gurobipy')
    from models.routing.variants.model_5.exact import reoptimize_e1 as public, solve_exact
    p = fixture(pool=2)
    result = public(p, hand_solution(p), time_limit=2, retime_e2=True)
    assert result['audit']['valid']
    full = solve_exact(p, time_limit=2, initial_result=hand_solution(p), e1_polish_time=1)
    assert full['audit']['valid'] and full['info']['proven_optimal']
    assert full['info']['e1_polish']['e1_subproblem_optimal']
    assert full['info']['runtime'] < 2.25
    generated = solve_exact(p, time_limit=2, e1_polish_time=1)
    assert generated['audit']['valid'] and generated['info']['e1_polish'] is not None
    for value in (-1, math.inf, math.nan):
        with pytest.raises(ValueError, match='e1_polish_time'):
            solve_exact(p, e1_polish_time=value)


def test_joint_timing_rejects_preparation_on_a_busy_e2_vehicle():
    gp = pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.multibatch_e1_routes import build_e1_routes
    from models.routing.solvers.exact.multibatch_e1_polish import add_e2_timing
    base = fixture(pool=2, e2_dispatch_prep_time=2)
    p = MultiBatchInstance(base.network,
        [replace(base.orders[0], pickup_window=(70, 70)),
         replace(base.orders[1], delivery_window=(74, 74))], base.operating_policy)
    initial = hand_solution(p, 71)
    first, second = initial['e2_trips']
    first['departure'], first['events'][0]['start'], first['events'][1]['start'] = 12, 13, 70
    second['departure'], second['events'][0]['start'] = 73, 74
    assert audit_solution(p, initial)['valid']
    m, ctx = build_e1_routes(p)
    try:
        add_e2_timing(p, m, ctx, initial)
        # The previous trip cannot return before 71. Departure at 72 would
        # occupy preparation at 70..72 while that same vehicle is still out.
        m.addConstr(ctx['e2_timing'][1][0] == 72)
        m.optimize()
        assert m.Status == gp.GRB.INFEASIBLE
    finally:
        m.dispose()


def test_fixed_schedule_uses_earliest_of_all_supply_deadlines():
    pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.multibatch_e1_routes import build_e1_routes
    from models.routing.solvers.exact.multibatch_e1_polish import fix_e2_schedule
    base = fixture(pool=3, e2_max_trips_per_vehicle=3)
    p = MultiBatchInstance(base.network,
        [base.orders[0], replace(base.orders[0], id='extra'), base.orders[1]], base.operating_policy)
    initial = hand_solution(base)
    initial['batches'][0]['container_demand'] = 2
    for e in initial['e1_trips'][0]['events']:
        e['quantity'] = 2
    initial['e2_trips'][1]['trip_index'] = 2
    initial['e2_trips'].insert(1, dict(trip_id='extra_trip', satellite_id='S', vehicle_id=0,
        trip_index=1, departure=18, return_complete=23, events=[
            dict(kind='d', order_id='extra', start=19, quantity=1),
            dict(kind='p', order_id='extra', start=22, quantity=1)]))
    assert audit_solution(p, initial)['valid']
    m, ctx = build_e1_routes(p)
    try:
        fix_e2_schedule(p, ctx, initial)
        m.update()
        assert ctx['supplies']['S', 'b1'].UB == 10
    finally:
        m.dispose()
