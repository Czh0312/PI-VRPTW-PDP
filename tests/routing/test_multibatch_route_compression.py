"""Shared M5 internal arcs preserve routes, split tasks and cycle causality."""
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

import pytest

from models.common.cycle import MultiBatchInstance
from models.common.cycle.audit import audit_solution
from models.common.cycle.state import empty_result
from tests.routing.test_multibatch_cycle import fixture, hand_solution
from tests.routing.test_single_batch_solver_audit import tiny_problem


@pytest.mark.parametrize('seed', [0, 1, 2, 4, 5])
def test_compressed_and_indexed_m5_have_same_nonmetric_optimum(seed):
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.search import engine as solver
    from models.routing.model_5.exact.formulations.full import build_model
    base = tiny_problem(3, seed)
    policy = fixture(pool=4, e2_max_trips_per_vehicle=3, e2_dispatch_prep_time=1,
                     e2_return_unload_time=1, e2_turnaround_time=1).operating_policy
    orders = [replace(o, batch='b1' if i == 0 else 'b2',
                      delivery_window=(0, 25) if i == 0 else (60, 85),
                      pickup_window=(8, 75) if i == 0 else (68, 88)) for i, o in enumerate(base.orders)]
    problem = MultiBatchInstance(base.network, orders, policy)
    problem.fleet.e2_per_sat_count = 2
    results = []
    for compressed in (False, True):
        with patch.object(solver, 'build_model', side_effect=lambda *a, **kw: build_model(
                *a, **kw, compress_multibatch_routes=compressed)):
            result = solver.solve_exact(problem, time_limit=5)
        assert result['info']['proven_optimal'], result['info']
        assert audit_solution(problem, result)['valid']
        results.append(result)
    assert results[0]['info']['objective'] == pytest.approx(results[1]['info']['objective'])
    assert results[1]['info']['variables'] < results[0]['info']['variables']


@pytest.mark.parametrize('zero,share_cutoff,linear', [(False, True, True), (True, True, True),
                                                   (False, False, False)])
def test_cross_batch_split_witness_and_shared_start_are_preserved(zero, share_cutoff, linear):
    gp = pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.formulations.full import build_model
    from models.routing.model_5.exact.search.engine import _set_start
    base = fixture(pool=2, zero=zero)
    problem = MultiBatchInstance(base.network,
        [replace(base.orders[0], pickup_window=(72, 78)), base.orders[1]], base.operating_policy)
    travel = 0 if zero else 1
    supply1, supply2 = (0, 60) if zero else (10, 70)
    result = empty_result(problem, 'manual')
    result['e1_trips'] = [
        dict(trip_id='e1_first', vehicle_id=0, batch_id='b1', departure=0,
             return_complete=0 if zero else 20,
             events=[dict(kind='d', site_id='S', quantity=1, start=supply1)]),
        dict(trip_id='e1_second', vehicle_id=0, batch_id='b2', departure=60,
             return_complete=90 if zero else 100,
             events=[dict(kind='d', site_id='S', quantity=1, start=supply2),
                     dict(kind='p', site_id='S', quantity=2, start=90)]),
    ]
    result['e2_trips'] = [
        dict(trip_id='first', satellite_id='S', vehicle_id=0, trip_index=0,
             departure=supply1, return_complete=supply1+2*travel,
             events=[dict(kind='d', order_id='o1', quantity=1, start=supply1+travel)]),
        dict(trip_id='second', satellite_id='S', vehicle_id=0, trip_index=1,
             departure=supply2, return_complete=73+travel,
             events=[dict(kind='d', order_id='o2', quantity=1, start=supply2+travel),
                     dict(kind='p', order_id='o1', quantity=1, start=72),
                     dict(kind='p', order_id='o2', quantity=1, start=73)]),
    ]
    independent = audit_solution(problem, result)
    assert independent['valid'], independent['errors']
    saved = deepcopy(result)
    model, ctx = build_model(problem, compress_multibatch_routes=True,
                            share_cutoff=share_cutoff, linear_e2_links=linear)
    try:
        assert ctx['compressed_e2_routes'] and _set_start(problem, model, ctx, result)
        model.update()
        active_links = [var for var in ctx['global_e2_arcs'].values() if var.Start == 1]
        assert len(active_links) == 2  # never overwritten by absent route copies
        for variable in model.getVars():
            if variable.Start != gp.GRB.UNDEFINED:
                variable.LB = variable.UB = variable.Start
        model.Params.TimeLimit = 3
        model.optimize()
        assert model.SolCount and model.ObjVal == pytest.approx(independent['objective'])
    finally:
        model.dispose()
    assert saved == result


@pytest.mark.parametrize('invalid', ['foreign_route', 'zero_cycle'])
def test_global_graph_cannot_cross_fixed_route_memberships_or_form_zero_cycles(invalid):
    gp = pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.formulations.full import build_model
    base = fixture(pool=3 if invalid == 'zero_cycle' else 2, zero=True)
    orders = ([replace(base.orders[0], id=f'o{i}', goods_weight=0, pickup_window=(0, 25))
               for i in (1, 2, 3)] if invalid == 'zero_cycle' else
              [replace(base.orders[0], pickup_window=(60, 89)), base.orders[1]])
    problem = MultiBatchInstance(base.network, orders, base.operating_policy)
    problem.fleet.e2_per_sat_count = 2
    model, ctx = build_model(problem, compress_multibatch_routes=True)
    try:
        for r in ctx['e2']:
            active = r['slot'] == 0 and (invalid != 'zero_cycle' or r['vehicle'] == 0)
            r['used'].LB = r['used'].UB = int(active)
            for i, node in enumerate(r['nodes']):
                assigned = active and (invalid == 'zero_cycle' or
                                       node['order'].id == ('o1' if r['vehicle'] == 0 else 'o2'))
                r['visit'][i].LB = r['visit'][i].UB = int(assigned)
        links = ([(('o1', 'd'), ('o2', 'p')), (('o2', 'd'), ('o1', 'p'))]
                 if invalid == 'foreign_route' else [(('o1', 'd'), ('o2', 'd')), (('o2', 'd'), ('o1', 'p')),
                    (('o1', 'p'), ('o2', 'p')), (('o2', 'p'), ('o1', 'd'))])
        # The zero-time cycle has net zero load and one route identity. The
        # third order supplies a source-to-sink path, so degrees and boundary
        # counts alone cannot rule out this disconnected cycle.
        for key in links:
            ctx['global_e2_arcs'][key].LB = ctx['global_e2_arcs'][key].UB = 1
        model.Params.TimeLimit = 3
        model.optimize()
        assert model.Status == gp.GRB.INFEASIBLE
    finally:
        model.dispose()


def test_unshared_task_states_keep_indexed_routes():
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.formulations.full import build_model
    model, ctx = build_model(fixture(), share_e2=False, compress_multibatch_routes=True)
    try:
        assert not ctx['compressed_e2_routes'] and not ctx['global_e2_arcs']
    finally:
        model.dispose()


@pytest.mark.parametrize('unloaded', [29, 30, 31])
def test_shared_routes_and_all_capacity_cuts_preserve_cutoff_boundary(unloaded):
    gp = pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.formulations.full import build_model
    from models.routing.model_5.exact.search.engine import _set_start
    problem = fixture(pool=2)
    result = hand_solution(problem, first_unload=unloaded)
    independent = audit_solution(problem, result)
    assert independent['valid']
    model, ctx = build_model(problem, compress_multibatch_routes=True)
    try:
        separator = ctx['capacity_separator']
        for terms, rhs in separator.candidates:
            model.addConstr(gp.quicksum(separator.variables[i] for i in terms) <= rhs)
        assert _set_start(problem, model, ctx, result)
        model.update()
        for var in model.getVars():
            if var.Start != gp.GRB.UNDEFINED:
                var.LB = var.UB = var.Start
        model.Params.TimeLimit = 3
        model.optimize()
        assert model.SolCount and model.ObjVal == pytest.approx(independent['objective'])
        assert ctx['pickup_returns']['o1'].X == unloaded
    finally:
        model.dispose()
