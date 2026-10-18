"""Warehouse flow strengthens relaxations without changing exact solutions."""
from copy import deepcopy
from dataclasses import replace
import time
from unittest.mock import patch

import pytest

from models.common.cycle import MultiBatchInstance
from models.common.cycle.assignment import assigned_problem, lift_result
from models.common.cycle.audit import audit_solution
from models.routing.model_3.exact.search import engine as solver
from models.routing.model_3.exact.formulations.single_batch import build_model
from models.routing.model_3.exact.initialization import construct_incumbent
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny
from tests.routing.test_single_batch_primal import split_problem


@pytest.mark.parametrize('model', [2, 4])
@pytest.mark.parametrize('seed', range(6))
def test_site_flow_matches_independent_nonmetric_enumeration(model, seed):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model, seed)
    expected = enumerate_tiny(p)
    with patch.object(solver, 'build_model', side_effect=lambda *a, **kw: build_model(
            *a, **kw, compress_routes=True, site_flow=True)):
        result = solver.solve_exact(p, time_limit=5)
    if expected == float('inf'):
        assert result['info']['proven_infeasible']
    else:
        assert result['info']['proven_optimal'], result['info']
        assert audit_solution(p, result)['valid']
        assert result['info']['objective'] == pytest.approx(expected)


@pytest.mark.parametrize('model', [2, 4])
@pytest.mark.parametrize('site', ['S', 'T'])
def test_audited_start_extends_to_site_flow_with_unused_site_and_route_copies(model, site):
    gp = pytest.importorskip('gurobipy')
    p = tiny_problem(model, 1)
    p.fleet.e2_per_sat_count = 2
    p.operating_policy = replace(p.operating_policy, e2_max_trips_per_vehicle=3)
    assignment = {o.customer: site for o in p.orders}
    start = construct_incumbent(assigned_problem(p, assignment), time.perf_counter()+1)
    assert start
    start = lift_result(p, start, assignment)
    saved = deepcopy(start)
    m, ctx = build_model(p, compress_routes=True, site_flow=True)
    try:
        assert solver._set_start(p, m, ctx, start)
        m.update()
        for v in m.getVars():
            if v.Start != gp.GRB.UNDEFINED:
                v.LB = v.UB = v.Start
        m.Params.TimeLimit = 3
        m.optimize()
        assert m.SolCount and m.ObjVal == pytest.approx(start['info']['objective'])
        assert start == saved
    finally:
        m.dispose()


def test_delivery_and_pickup_on_different_trips_extend_with_site_flow():
    gp = pytest.importorskip('gurobipy')
    fixed = split_problem()
    p = MultiBatchInstance(fixed.network, fixed.orders, fixed.operating_policy, model=4)
    start = lift_result(p, construct_incumbent(fixed, time.perf_counter()+1),
                        {o.customer: o.satellite for o in fixed.orders})
    assert audit_solution(p, start)['valid']
    m, ctx = build_model(p, compress_routes=True, site_flow=True)
    try:
        assert solver._set_start(p, m, ctx, start)
        m.update()
        for v in m.getVars():
            if v.Start != gp.GRB.UNDEFINED:
                v.LB = v.UB = v.Start
        m.Params.TimeLimit = 3
        m.optimize()
        assert m.SolCount and m.ObjVal == pytest.approx(start['info']['objective'])
        assert all(v.VType == gp.GRB.BINARY for v in ctx['e2_site_arcs'].values())
    finally:
        m.dispose()


@pytest.mark.parametrize('model', [1, 2, 3, 4])
@pytest.mark.parametrize('compressed', [False, True])
def test_site_flow_scope(model, compressed):
    pytest.importorskip('gurobipy')
    m, ctx = build_model(tiny_problem(model, 1), compress_routes=compressed, site_flow=True)
    try:
        assert ctx['e2_site_flow'] == (compressed and model in (2, 4))
    finally:
        m.dispose()


@pytest.mark.parametrize('model', [1, 2, 3, 4])
@pytest.mark.parametrize('compressed', [False, True])
def test_site_flow_is_opt_in(model, compressed):
    pytest.importorskip('gurobipy')
    for setting in (None, False):
        m, ctx = build_model(tiny_problem(model, 1), compress_routes=compressed, site_flow=setting)
        try:
            assert not ctx['e2_site_flow']
        finally:
            m.dispose()


@pytest.mark.parametrize('resource', ['container_weight', 'container_volume'])
def test_site_flow_preserves_zero_container_dimensions(resource):
    pytest.importorskip('gurobipy')
    p = tiny_problem(4, 1)
    setattr(p.fleet, resource, 0)
    p = MultiBatchInstance(p.network, p.orders, p.operating_policy, model=4)
    with patch.object(solver, 'build_model', side_effect=lambda *a, **kw: build_model(
            *a, **kw, compress_routes=True, site_flow=True)):
        result = solver.solve_exact(p, time_limit=5)
    assert result['info']['proven_optimal'] and audit_solution(p, result)['valid']
    assert result['info']['objective'] == pytest.approx(enumerate_tiny(p))
    assert result['info']['e2_site_flow']
    assert result['info']['e2_site_flow_variables'] > 0


@pytest.mark.parametrize('model', [2, 4])
def test_multiple_orders_of_one_customer_share_the_warehouse_flow_assignment(model):
    pytest.importorskip('gurobipy')
    original = tiny_problem(model, 1)
    orders = list(original.orders)
    orders[1] = replace(orders[1], customer=orders[0].customer, satellite=orders[0].satellite)
    p = MultiBatchInstance(original.network, orders, original.operating_policy, model=model)
    with patch.object(solver, 'build_model', side_effect=lambda *a, **kw: build_model(
            *a, **kw, compress_routes=True, site_flow=True)):
        result = solver.solve_exact(p, time_limit=5)
    assert result['info']['proven_optimal'] and audit_solution(p, result)['valid']
    assert result['info']['objective'] == pytest.approx(enumerate_tiny(p))


def test_disaggregation_adds_continuous_variables_without_binary_route_copies():
    gp = pytest.importorskip('gurobipy')
    sizes = []
    for enabled in (False, True):
        m, ctx = build_model(tiny_problem(4, 1), compress_routes=True, site_flow=enabled)
        try:
            sizes.append((m.NumVars, m.NumBinVars))
            if enabled:
                assert all(v.VType == gp.GRB.CONTINUOUS for v in ctx['e2_site_arcs'].values())
        finally:
            m.dispose()
    assert sizes[1][0] > sizes[0][0]
    assert sizes[1][1] == sizes[0][1]


def test_site_flow_rejects_fractional_cross_warehouse_edge():
    gp = pytest.importorskip('gurobipy')
    p = tiny_problem(2, 0)
    # Test the added warehouse equations in isolation: a full edge cannot
    # connect endpoints with different fractional warehouse assignments.
    # This is independent of task time, load and physical route identity.
    m = gp.Model()
    m.Params.OutputFlag = 0
    from models.routing.model_3.exact.accelerators.single_batch.site_flow import add_site_flow
    left, right = ('o0', 'd'), ('o1', 'd')
    nodes = [dict(order=p.order_map[key[0]], kind=key[1]) for key in (left, right)]
    arc = m.addVar(lb=1, ub=1)
    assignments = {(c, s): m.addVar(lb=value, ub=value) for c, values in
                   [('C0', (.75, .25)), ('C1', (.25, .75))]
                   for s, value in zip(('S', 'T'), values)}
    routes = []
    for site in ('S', 'T'):
        routes.append(dict(satellite=site, nodes=nodes, x={
            (-1, 0): m.addVar(ub=1), (1, -2): m.addVar(ub=1)}))
    add_site_flow(m, p, routes, {'S': nodes, 'T': nodes},
                  {'S': [(0, 1)], 'T': [(0, 1)]}, {(left, right): arc}, assignments, lambda: None)
    try:
        m.optimize()
        assert m.Status == gp.GRB.INFEASIBLE
    finally:
        m.dispose()
