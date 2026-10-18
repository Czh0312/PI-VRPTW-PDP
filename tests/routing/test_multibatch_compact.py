"""Equivalence of shared M5 task states and the route-indexed formulation."""
from copy import deepcopy
from dataclasses import replace

import pytest

from models.common.cycle import MultiBatchInstance, MultiBatchPolicy
from models.common.cycle.audit import audit_solution
from models.common.cycle.state import empty_result
from tests.routing.test_multibatch_cycle import fixture
from tests.routing.test_single_batch_solver_audit import tiny_problem


@pytest.mark.parametrize('seed', [0, 1, 2, 4, 5])
def test_shared_and_indexed_m5_have_same_optimum_on_directed_nonmetric_instances(monkeypatch, seed):
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.search import engine as solver
    from models.routing.model_5.exact.formulations.full import build_model
    base = tiny_problem(3, seed)
    policy = MultiBatchPolicy.from_config(dict(policy_version='multi_batch_cutoff_v1', cycle_start=0,
        cycle_end=125, batches=[dict(id='b1', delivery_duration=30, return_duration=30),
                               dict(id='b2', delivery_duration=30, return_duration=30)],
        container_pool_size=4, cleaning_duration=3, time_tick_seconds=60, e2_max_trips_per_vehicle=2,
        e1_load_time=1, e2_dispatch_prep_time=1, e2_return_unload_time=1, depot_return_unload_time=1))
    orders = [replace(o, batch='b1' if i == 0 else 'b2',
                      delivery_window=(0, 25) if i == 0 else (60, 85),
                      pickup_window=(8, 70) if i == 0 else (68, 100)) for i, o in enumerate(base.orders)]
    problem = MultiBatchInstance(base.network, orders, policy)
    problem.fleet.e2_per_sat_count = 2
    compact = solver.solve_exact(problem, time_limit=5)
    def indexed(*args, **kwargs):
        return build_model(*args, **kwargs, share_e2=False)
    monkeypatch.setattr(solver, 'build_model', indexed)
    reference = solver.solve_exact(problem, time_limit=5)
    assert compact['info']['proven_optimal'] and reference['info']['proven_optimal']
    assert audit_solution(problem, compact)['valid'] and audit_solution(problem, reference)['valid']
    assert compact['info']['objective'] == pytest.approx(reference['info']['objective'])
    assert compact['info']['variables'] < reference['info']['variables']
    assert compact['info']['constraints'] < reference['info']['constraints']


def test_shared_m5_accepts_pickups_from_an_earlier_batch_on_later_delivery_trip():
    gp = pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.formulations.full import build_model
    from models.routing.model_5.exact.search.engine import _set_start
    base = fixture(pool=2)
    problem = MultiBatchInstance(base.network,
        [replace(base.orders[0], pickup_window=(72, 78)), base.orders[1]], base.operating_policy)
    result = empty_result(problem, 'manual')
    result['e1_trips'] = [
        dict(trip_id='e1_first', vehicle_id=0, batch_id='b1', departure=0, return_complete=20,
             events=[dict(kind='d', site_id='S', quantity=1, start=10)]),
        dict(trip_id='e1_second', vehicle_id=0, batch_id='b2', departure=60, return_complete=100,
             events=[dict(kind='d', site_id='S', quantity=1, start=70),
                     dict(kind='p', site_id='S', quantity=2, start=90)]),
    ]
    result['e2_trips'] = [
        dict(trip_id='e2_first', satellite_id='S', vehicle_id=0, trip_index=0, departure=10,
             return_complete=12, events=[dict(kind='d', order_id='o1', quantity=1, start=11)]),
        dict(trip_id='e2_second', satellite_id='S', vehicle_id=0, trip_index=1, departure=70,
             return_complete=74, events=[dict(kind='d', order_id='o2', quantity=1, start=71),
                                        dict(kind='p', order_id='o1', quantity=1, start=72),
                                        dict(kind='p', order_id='o2', quantity=1, start=73)]),
    ]
    audit = audit_solution(problem, result)
    assert audit['valid'], audit['errors']
    original = deepcopy(result)
    for share in (False, True):
        model, ctx = build_model(problem, share_e2=share)
        try:
            assert _set_start(problem, model, ctx, result)
            model.update()
            for variable in model.getVars():
                if variable.Start != gp.GRB.UNDEFINED:
                    variable.LB = variable.UB = variable.Start
            model.Params.TimeLimit = 3
            model.optimize()
            assert model.SolCount and model.ObjVal == pytest.approx(audit['objective'])
        finally:
            model.dispose()
    assert result == original


def test_m5_time_bounds_keep_delivery_after_its_own_cutoff_legal():
    # Only E1 unloading must meet each batch's cutoff in M5. An E2 delivery
    # may occur later if the order window and final recovery permit it.
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.search.engine import solve_exact
    base = fixture(pool=2)
    problem = MultiBatchInstance(base.network,
        [replace(base.orders[0], delivery_window=(35, 40), pickup_window=(36, 45)), base.orders[1]],
        base.operating_policy)
    result = solve_exact(problem, time_limit=3)
    assert result['info']['proven_optimal'] and result['audit']['valid']
    assert next(e['start'] for t in result['e2_trips'] for e in t['events']
                if e['order_id'] == 'o1' and e['kind'] == 'd') > 30


def test_m5_bounds_use_relaxed_paths_when_direct_satellite_arcs_are_long():
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.search.engine import solve_exact
    base = tiny_problem(3, 2)
    policy = replace(fixture(pool=4).operating_policy, e2_max_trips_per_vehicle=2)
    orders = [replace(o, id=f'{o.id}_{b.id}', satellite='S', batch=b.id,
                      delivery_window=(b.start, b.start+20), pickup_window=(b.start, b.cutoff-2),
                      service_time=0, min_use_time=0)
              for b in policy.batches for o in base.orders]
    problem = MultiBatchInstance(base.network, orders, policy)
    for (a, b) in problem._travel:
        problem._travel[a, b] = 0 if a == b else 1
    # The first customer's delivery and return are feasible via the second
    # customer's tasks. Direct-arc earliest/latest bounds would reject both.
    problem._travel['S', 'C0'] = problem._travel['C0', 'S'] = 100
    result = solve_exact(problem, time_limit=5)
    assert result['info']['proven_optimal'] and result['audit']['valid']
