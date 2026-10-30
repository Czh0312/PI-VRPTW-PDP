"""Integer equivalence and disconnected-cycle rejection of M3 arc load flows."""
from dataclasses import replace
import math
from types import SimpleNamespace

import pytest

from models.common.cycle.instance import MultiBatchInstance
from models.routing.model_3.exact.model import build_model
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny


@pytest.mark.parametrize('compressed', [False, True])
@pytest.mark.parametrize('fixture', [0, 2, 3, 4])
def test_arc_flows_preserve_nonmetric_single_site_integer_optimum(compressed, fixture):
    import gurobipy as gp
    original = tiny_problem(3, fixture)
    problem = MultiBatchInstance(original.network,
        [replace(order, satellite='S') for order in original.orders], original.operating_policy, model=3)
    expected = enumerate_tiny(problem)
    model, context = build_model(problem, compress_routes=compressed, capacity_links=True, resource_flows=True)
    try:
        model.Params.Seed = 42
        model.Params.TimeLimit = 10
        model.optimize()
        assert context['resource_flows']['applied']
        if math.isinf(expected):
            assert model.Status == gp.GRB.INFEASIBLE
        else:
            assert model.Status == gp.GRB.OPTIMAL
            assert model.ObjVal == pytest.approx(expected)
    finally:
        model.dispose()


@pytest.mark.parametrize('connected', [False, True])
def test_positive_delivery_and_return_cycle_requires_a_warehouse_connection(connected):
    import gurobipy as gp
    from models.routing.model_3.exact.resource_flows import add_resource_flows
    model = gp.Model('cycle_with_canceling_signed_net_load')
    try:
        model.Params.OutputFlag = 0
        model.Params.Seed = 42
        order = SimpleNamespace(id='o')
        nodes = [dict(order=order, kind=kind, w=1., v=1.) for kind in ('d', 'p')]
        # Delivery and pickup have equal volume/weight: a signed net-load
        # circulation alone would allow the disconnected d -> p -> d cycle.
        arcs = [(-1, 0), (0, 1), (1, -2)] if connected else [(0, 1), (1, 0)]
        active = model.addVars(arcs, lb=1., ub=1.)
        variables = SimpleNamespace(model=model,
            routes={'e2': [dict(satellite='S', x=active)]}, aggregate_arcs={},
            task_load_weight={}, task_load_volume={})
        params = SimpleNamespace(free_assignment=False, problem=SimpleNamespace(reusable=True),
            fleet=SimpleNamespace(e2_weight_cap=2., e2_volume_cap=2.))
        add_resource_flows(params, variables, {'S': nodes}, {'S': arcs}, check_budget=lambda: None)
        model.optimize()
        assert model.Status == (gp.GRB.OPTIMAL if connected else gp.GRB.INFEASIBLE)
    finally:
        model.dispose()
