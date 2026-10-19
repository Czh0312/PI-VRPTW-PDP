"""Capacity cut validity uses independent route-load and permutation checks."""
from dataclasses import replace
from itertools import permutations
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from models.common.cycle.audit import audit_solution
from models.routing.model_3.exact.branch_cut.capacity import build_capacity_separator, minimum_routes
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny


def test_delivery_and_pickup_capacity_are_rounded_separately_and_conservatively():
    p = tiny_problem(3, 0)
    p.fleet.e2_weight_cap = 2
    p.fleet.e2_volume_cap = 1
    tasks = [(o.id, kind) for o in p.orders for kind in p.task_kinds]
    assert minimum_routes(p, tasks) == 2  # Two paired routes fit; adding D+P would say 3 or 4.
    assert minimum_routes(p, []) == 0
    p.fleet.container_weight = p.fleet.container_volume = 0
    p.orders = [replace(o, goods_weight=1+1e-7) for o in p.orders]
    p.order_map = {o.id: o for o in p.orders}
    assert minimum_routes(p, tasks) == 1


def test_every_capacity_feasible_partition_satisfies_every_candidate():
    # Enumerate all 6! task permutations and all 2^5 route boundaries. This
    # oracle checks raw departure and running loads without MILP constraints,
    # shortest paths, the exact seed constructor, or the cut bound function.
    p = tiny_problem(4, 1)
    p.fleet.e2_weight_cap = 5
    p.fleet.e2_volume_cap = 2
    p.orders = [replace(o, goods_weight=i+1) for i, o in enumerate(p.orders)]
    p.order_map = {o.id: o for o in p.orders}
    tasks = [(o.id, kind) for o in p.orders for kind in p.task_kinds]
    arcs = {(a, b): None for a in tasks for b in tasks if a != b}
    separator = build_capacity_separator(p, arcs, lambda: None)
    assert separator.candidates
    keys = list(arcs)
    feasible = 0
    for sequence in permutations(tasks):
        for boundaries in range(1 << (len(tasks)-1)):
            routes, route = [], []
            for i, task in enumerate(sequence):
                route.append(task)
                if i == len(tasks)-1 or boundaries & (1 << i):
                    routes.append(route)
                    route = []
            selected = set()
            valid = True
            for route in routes:
                weight = sum(p.order_map[oid].goods_weight+1 for oid, kind in route if kind == 'd')
                volume = sum(kind == 'd' for _, kind in route)
                if weight > 5 or volume > 2:
                    valid = False
                    break
                for oid, kind in route:
                    weight += -(p.order_map[oid].goods_weight+1) if kind == 'd' else 1
                    volume += -1 if kind == 'd' else 1
                    if not (0 <= weight <= 5 and 0 <= volume <= 2):
                        valid = False
                        break
                if not valid:
                    break
                selected.update(zip(route, route[1:]))
            if valid:
                feasible += 1
                for terms, rhs in separator.candidates:
                    assert sum(keys[i] in selected for i in terms) <= rhs
    assert feasible > 0


@pytest.mark.parametrize('model', [1, 2, 3, 4])
@pytest.mark.parametrize('seed', [0, 1, 4])
@pytest.mark.parametrize('compressed', [False, True])
def test_cut_model_matches_independent_tiny_oracle(model, seed, compressed):
    pytest.importorskip('gurobipy')
    from models.routing.model_3.exact import enhanced_solver as solver
    from models.routing.model_3.exact.formulation import build_model
    p = tiny_problem(model, seed)
    expected = enumerate_tiny(p)
    with patch.object(solver, 'build_model', side_effect=lambda *a, **kw: build_model(
            *a, **kw, capacity_cuts=True, compress_routes=compressed)):
        result = solver.solve_exact(p, time_limit=8)
    assert result['info']['proven_optimal'], result['info']
    assert audit_solution(p, result)['valid']
    assert result['info']['objective'] == pytest.approx(expected)


def test_callback_skips_nonroot_nodes_and_reports_failure():
    gp = pytest.importorskip('gurobipy')
    from models.routing.model_3.exact.branch_cut.capacity import CapacityCutSeparator
    separator = CapacityCutSeparator([])
    node = [1]
    queried = []
    terminated = []
    def cb_get(which):
        return gp.GRB.OPTIMAL if which == gp.GRB.Callback.MIPNODE_STATUS else node[0]
    def fail(variables):
        queried.append(True)
        raise RuntimeError('forced relaxation failure')
    model = SimpleNamespace(cbGet=cb_get, cbGetNodeRel=fail,
                            terminate=lambda: terminated.append(True))
    separator(model, gp.GRB.Callback.MIPNODE)
    assert not queried
    node[0] = 0
    separator(model, gp.GRB.Callback.MIPNODE)
    assert terminated and 'forced relaxation failure' in separator.error


def test_cuts_follow_build_deadline():
    p = tiny_problem(4, 1)
    def expired():
        raise TimeoutError('cut deadline')
    with pytest.raises(TimeoutError, match='cut deadline'):
        build_capacity_separator(p, {}, expired)


@pytest.mark.parametrize('model', [1, 2, 3, 4])
@pytest.mark.parametrize('compressed', [False, True])
def test_default_separation_is_limited_to_compressed_pickup_models(model, compressed):
    pytest.importorskip('gurobipy')
    from models.routing.model_3.exact.formulation import build_model
    p = tiny_problem(model, 1)
    m, ctx = build_model(p, compress_routes=compressed)
    try:
        assert (ctx['capacity_separator'] is not None) == (compressed and p.reusable)
    finally:
        m.dispose()


def test_audited_cross_trip_start_satisfies_all_static_candidates():
    gp = pytest.importorskip('gurobipy')
    import time
    from models.routing.model_3.exact.formulation import build_model
    from models.routing.model_3.exact.initialization import construct_incumbent
    from models.routing.model_3.exact.enhanced_solver import _set_start
    from tests.routing.test_single_batch_primal import split_problem
    p = split_problem()
    start = construct_incumbent(p, time.perf_counter()+1)
    assert start and audit_solution(p, start)['valid']
    m, ctx = build_model(p, compress_routes=True, capacity_cuts=True)
    try:
        separator = ctx['capacity_separator']
        assert separator.candidates
        for terms, rhs in separator.candidates:
            m.addConstr(gp.quicksum(separator.variables[i] for i in terms) <= rhs)
        assert _set_start(p, m, ctx, start)
        m.update()
        for variable in m.getVars():
            if variable.Start != gp.GRB.UNDEFINED:
                variable.LB = variable.UB = variable.Start
        m.Params.TimeLimit = 3
        m.optimize()
        assert m.SolCount and m.ObjVal == pytest.approx(start['info']['objective'])
    finally:
        m.dispose()
