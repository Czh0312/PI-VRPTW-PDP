"""Factored departure loads preserve M5 integer schedules and LP projections."""
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

import pytest

from models.common.cycle import MultiBatchInstance
from models.common.cycle.audit import audit_solution
from models.common.cycle.state import empty_result
from tests.routing.test_multibatch_cycle import fixture, hand_solution
from tests.routing.test_single_batch_solver_audit import tiny_problem


def nonmetric_problem(seed):
    base = tiny_problem(3, seed)
    policy = fixture(pool=4, e2_max_trips_per_vehicle=3, e2_dispatch_prep_time=1,
                     e2_return_unload_time=1, e2_turnaround_time=1).operating_policy
    orders = [replace(o, batch='b1' if i == 0 else 'b2',
                      delivery_window=(0, 25) if i == 0 else (60, 85),
                      pickup_window=(8, 75) if i == 0 else (68, 88)) for i, o in enumerate(base.orders)]
    problem = MultiBatchInstance(base.network, orders, policy)
    problem.fleet.e2_per_sat_count = 2
    return problem


@pytest.mark.parametrize('seed', [0, 2, 5])
@pytest.mark.parametrize('compressed,linear', [(False, True), (True, True), (True, False)])
def test_nonmetric_integer_optima_match(seed, compressed, linear):
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact import engine as solver
    from models.routing.model_5.exact.formulation import build_model
    problem = nonmetric_problem(seed)
    results = []
    for factored in (False, True):
        with patch.object(solver, 'build_model', side_effect=lambda *a, **kw: build_model(
                *a, **kw, compress_multibatch_routes=compressed,
                linear_e2_links=linear, factor_e2_loads=factored)):
            result = solver.solve_exact(problem, time_limit=5)
        assert result['info']['proven_optimal'], result['info']
        assert audit_solution(problem, result)['valid']
        assert bool(result['info']['e2_departure_load_variables']) == factored
        results.append(result)
    assert results[0]['info']['objective'] == pytest.approx(results[1]['info']['objective'])


@pytest.mark.parametrize('seed', [0, 2, 5])
def test_linear_relaxation_projection_is_preserved(seed):
    gp = pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.formulation import build_model
    values = []
    for factored in (False, True):
        model, ctx = build_model(nonmetric_problem(seed), compress_multibatch_routes=True,
                                 factor_e2_loads=factored)
        relaxed = None
        try:
            # Both relaxations drop the same untouched E1 indicators. Retain
            # all capacity candidates as static rows in both comparisons.
            separator = ctx['capacity_separator']
            for terms, rhs in separator.candidates:
                model.addConstr(gp.quicksum(separator.variables[i] for i in terms) <= rhs)
            model.update()
            relaxed = model.relax()
            relaxed.Params.Presolve = 0
            relaxed.optimize()
            assert relaxed.Status == gp.GRB.OPTIMAL
            values.append(relaxed.ObjVal)
            if factored:
                for route in ctx['e2']:
                    for resource, var in zip(('w', 'v'), route['departure_loads']):
                        demand = sum(node[resource]*relaxed.getVarByName(route['visit'][i].VarName).X
                                     for i, node in enumerate(route['nodes']) if node['kind'] == 'd')
                        assert relaxed.getVarByName(var.VarName).X == pytest.approx(demand, abs=1e-6)
        finally:
            if relaxed is not None:
                relaxed.dispose()
            model.dispose()
    assert values[0] == pytest.approx(values[1])


@pytest.mark.parametrize('unloaded', [29, 30, 31])
def test_fixed_starts_and_cutoff_boundaries_include_departure_loads(unloaded):
    gp = pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.formulation import build_model
    from models.routing.model_5.exact.engine import _set_start
    problem = fixture(pool=2)
    start = hand_solution(problem, first_unload=unloaded)
    independent = audit_solution(problem, start)
    assert independent['valid']
    saved = deepcopy(start)
    model, ctx = build_model(problem, factor_e2_loads=True, compress_multibatch_routes=True)
    try:
        assert _set_start(problem, model, ctx, start)
        model.update()
        for route in ctx['e2']:
            assert len(route['departure_loads']) == 2
            for variable in route['departure_loads']:
                assert variable.Start != gp.GRB.UNDEFINED
        for variable in model.getVars():
            if variable.Start != gp.GRB.UNDEFINED:
                variable.LB = variable.UB = variable.Start
        model.optimize()
        assert model.SolCount and model.ObjVal == pytest.approx(independent['objective'])
        assert ctx['pickup_returns']['o1'].X == unloaded
    finally:
        model.dispose()
    assert start == saved


def test_pickup_first_cross_batch_trip_does_not_add_pickups_to_departure_load():
    gp = pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.formulation import build_model
    from models.routing.model_5.exact.engine import _set_start
    base = fixture(pool=2)
    problem = MultiBatchInstance(base.network,
        [replace(base.orders[0], pickup_window=(71, 80)), base.orders[1]], base.operating_policy)
    problem.fleet.e2_weight_cap = 3
    problem.fleet.e2_volume_cap = 2
    start = empty_result(problem, 'manual')
    start['e1_trips'] = [
        dict(trip_id='supply1', vehicle_id=0, batch_id='b1', departure=0, return_complete=20,
             events=[dict(kind='d', site_id='S', quantity=1, start=10)]),
        dict(trip_id='supply2', vehicle_id=0, batch_id='b2', departure=60, return_complete=100,
             events=[dict(kind='d', site_id='S', quantity=1, start=70),
                     dict(kind='p', site_id='S', quantity=2, start=90)]),
    ]
    start['e2_trips'] = [
        dict(trip_id='delivery1', satellite_id='S', vehicle_id=0, trip_index=0,
             departure=10, return_complete=12,
             events=[dict(kind='d', order_id='o1', quantity=1, start=11)]),
        dict(trip_id='mixed', satellite_id='S', vehicle_id=0, trip_index=1,
             departure=70, return_complete=74,
             events=[dict(kind='p', order_id='o1', quantity=1, start=71),
                     dict(kind='d', order_id='o2', quantity=1, start=72),
                     dict(kind='p', order_id='o2', quantity=1, start=73)]),
    ]
    independent = audit_solution(problem, start)
    assert independent['valid'], independent['errors']
    model, ctx = build_model(problem, factor_e2_loads=True, compress_multibatch_routes=True)
    try:
        assert _set_start(problem, model, ctx, start)
        model.update()
        mixed = next(r for r in ctx['e2'] if r['slot'] == 1)
        assert mixed['departure_loads'][0].Start == 2  # Goods plus one new box.
        assert mixed['departure_loads'][1].Start == 1
        for variable in model.getVars():
            if variable.Start != gp.GRB.UNDEFINED:
                variable.LB = variable.UB = variable.Start
        model.optimize()
        assert model.SolCount and model.ObjVal == pytest.approx(independent['objective'])
    finally:
        model.dispose()
