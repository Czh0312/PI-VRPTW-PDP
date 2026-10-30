"""Validity of subset rows, fractional separation and interrupted LP bounds."""
from types import SimpleNamespace
import time

import pytest

from models.routing.model_3.exact.network_cuts import required_crossings, separate


def small_arc_problem():
    orders = [SimpleNamespace(id=f'o{i}', customer=f'C{i}', satellite='S', count=1,
                              service_time=1, min_use_time=0) for i in range(4)]

    def distance(a, b):
        if a == b:
            return 0.
        if 'S' in (a, b):
            return 1. if 'C3' in (a, b) else 100.
        return 1. if 'C3' not in (a, b) else 100.

    problem = SimpleNamespace(orders=orders, task_kinds=('d', 'p'),
        fleet=SimpleNamespace(e2_weight_cap=4., e2_volume_cap=4.),
        operating_policy=SimpleNamespace(cycle_start=0, cycle_end=1000,
            e2_turnaround_time=0, e2_return_unload_time=0),
        task=lambda order, kind: (1, 1., 1.), distance=distance,
        travel=lambda a, b: 0 if a == b else 1)
    parameters = SimpleNamespace(task_windows={(order.id, kind): (0, 100)
        for order in orders for kind in ('d', 'p')})
    return problem, parameters


def test_min_cut_finds_fractionally_connected_cycle():
    nodes = [dict(kind=kind, w=1., v=1., site=f'C{i}', window=(0, 100))
             for i in range(3) for kind in ('d', 'p')]
    values = {(-1, 0): .25, (0, 1): 1., (1, 2): 1., (2, 3): 1.,
              (3, 0): .75, (3, -2): .25,
              (-1, 4): 1., (4, 5): 1., (5, -2): 1.}
    # The entire positive graph is weakly connected to the warehouse. A
    # component-only detector misses the first four tasks' 0.25 exit flow.
    cuts = separate(nodes, list(values), values, weight_cap=3., volume_cap=3.,
        distance=lambda a, b: float(a != b), known=set(), deadline=time.perf_counter()+2)
    assert any(cut['tasks'] == [0, 1, 2, 3] and cut['required'] == 1 for cut in cuts)


def test_rounded_capacity_rows_hold_for_all_subsets_of_physical_routes():
    nodes = [dict(kind=kind, w=1., v=1.) for i in range(3) for kind in ('d', 'p')]
    # Capacity two: the first trip serves two boxes, the second one box.
    physical_routes = [(0, 2, 3, 1), (4, 5)]
    for mask in range(1, 1 << len(nodes)):
        subset = {i for i in range(len(nodes)) if mask & (1 << i)}
        outgoing = sum(a in subset and b not in subset
            for route in physical_routes for a, b in zip((-1,)+route, route+(-2,)))
        assert outgoing >= required_crossings(nodes, subset, 2., 2.)
    assert required_crossings(nodes, {0, 2, 4}, 2., 2.) == 2


def test_separation_deadline_is_a_resource_limit_only():
    assert separate([], [], {}, weight_cap=1., volume_cap=1., distance=lambda a, b: 0.,
                    known=set(), deadline=time.perf_counter()-1) == []


def test_interrupted_cut_LP_keeps_only_last_optimal_bound(monkeypatch):
    import gurobipy as gp
    from models.routing.model_3.exact.route_bounds import assignment_distance_bound
    original = gp.Model.optimize
    first = []

    def optimize(model, *args, **kwargs):
        if first:
            model.reset()
            model.Params.Presolve = 0
            model.Params.IterationLimit = 0
            model.Params.TimeLimit = 0
        result = original(model, *args, **kwargs)
        if not first:
            assert model.Status == gp.GRB.OPTIMAL
            first.append(model.ObjVal)
        return result

    monkeypatch.setattr(gp.Model, 'optimize', optimize)
    problem, parameters = small_arc_problem()
    lower, info = assignment_distance_bound(problem, parameters, 'S', deadline=time.perf_counter()+2)
    assert info['capacity_cuts'] and len(info['LP_calls']) >= 2
    assert info['native_status'] in (gp.GRB.TIME_LIMIT, gp.GRB.ITERATION_LIMIT)
    assert lower == pytest.approx(first[0])


def test_connected_auxiliary_bound_is_stronger_without_changing_input():
    from models.routing.model_3.exact.route_bounds import assignment_distance_bound
    problem, parameters = small_arc_problem()
    lower, info = assignment_distance_bound(problem, parameters, 'S', deadline=time.perf_counter()+2)
    assert info['capacity_cuts']
    assert lower > 5.  # A three-order cycle survives two-arc conflicts.
    # One physical route serving C0,C1,C2, then a separate C3 route is feasible.
    assert lower <= 204.+1e-6


def test_every_precedence_conflict_forms_a_cycle_in_the_original_causal_graph():
    from models.routing.model_3.exact.precedence_cuts import conflict_pairs
    nodes = [dict(order=SimpleNamespace(id=f'o{i}'), kind=kind)
             for i in range(3) for kind in ('d', 'p')]
    arcs = [(a, b) for a in range(6) for b in range(6)
            if a != b and not (a % 2 == 1 and b == a-1)]
    pairs = conflict_pairs(nodes, arcs)
    assert tuple(sorted(((1, 2), (3, 0)))) in pairs
    for first, second in pairs:
        edges = {(0, 1), (2, 3), (4, 5), first, second}
        indegree = {i: sum(b == i for a, b in edges) for i in range(6)}
        queue = [i for i, degree in indegree.items() if degree == 0]
        done = 0
        while queue:
            at = queue.pop()
            done += 1
            for a, b in edges:
                if a == at:
                    indegree[b] -= 1
                    if indegree[b] == 0:
                        queue.append(b)
        assert done < 6
