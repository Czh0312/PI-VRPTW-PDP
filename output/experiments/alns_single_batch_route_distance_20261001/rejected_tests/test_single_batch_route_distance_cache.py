"""Full physical visit sequences reuse geometry without reusing feasibility."""
from copy import deepcopy
from dataclasses import replace
import itertools
import random
import time

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.alns.construction import chronological_state
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.single_batch_descent import SingleBatchDescent, task_distance
from tests.routing.test_single_batch_descent_readiness import sequential_trips
from tests.routing.test_single_batch_solver_audit import tiny_problem


@pytest.mark.parametrize('model', [1, 3])
@pytest.mark.parametrize('seed', range(6))
def test_full_directed_geometry_order_and_all_values_match_arc_only_reference(model, seed):
    base = tiny_problem(model, seed)
    orders = [replace(o, id=f'{o.id}_{i}') for i in range(3) for o in base.orders]
    p = MultiBatchInstance(base.network, orders,
        replace(base.operating_policy, e2_max_trips_per_vehicle=12), model=model)
    rng = random.Random(seed)
    state = chronological_state(p, rng)
    saved = deepcopy(state)
    reference = SingleBatchDescent(p, cache_sites=False, cache_distances=True)
    cached = SingleBatchDescent(p, cache_sites=False, cache_distances=True, cache_route_distances=True)
    expected = reference._enumerate_routes(state['e2'], time.perf_counter()+10)
    assert cached._enumerate_routes(state['e2'], time.perf_counter()+10) == expected
    assert cached._e2_candidates(state, time.perf_counter()+10) == reference._e2_candidates(state, time.perf_counter()+10)
    pool = [(o.id, kind) for o in orders for kind in p.task_kinds]
    for _ in range(60):
        tasks = rng.choices(pool, k=rng.randrange(12))
        site = rng.choice(p.satellites)
        assert cached.task_distance(site, tasks) == task_distance(p, site, tasks)
    assert cached.route_distance.cache_info().hits > 0
    assert state == saved


def test_keys_share_physical_visits_but_distinguish_site_direction_and_duplicates():
    base = tiny_problem(3, 1)
    orders = [replace(o, id=f'{o.id}_{i}') for i in range(2) for o in base.orders]
    p = MultiBatchInstance(base.network, orders, base.operating_policy, model=3)
    cached = SingleBatchDescent(p, cache_distances=True, cache_route_distances=True)
    first = [('o0_0', 'd'), ('o1_0', 'p'), ('o2_0', 'd')]
    second = [('o0_1', 'p'), ('o1_1', 'd'), ('o2_1', 'p')]
    assert cached.task_distance('S', first) == task_distance(p, 'S', first)
    assert cached.task_distance('S', second) == task_distance(p, 'S', second)
    assert cached.route_distance.cache_info().misses == 1
    assert cached.route_distance.cache_info().hits == 1
    for site, tasks in [('T', first), ('S', list(reversed(first))), ('S', first+first[:1])]:
        assert cached.task_distance(site, tasks) == task_distance(p, site, tasks)
    assert cached.route_distance.cache_info().misses == 4


def test_positive_self_arcs_and_empty_route_are_not_collapsed():
    p = tiny_problem(3, 1)
    calls = []
    def distance(a, b):
        calls.append((a, b))
        return 3.0 if a == b else 1.0
    p.distance = distance
    cached = SingleBatchDescent(p, cache_route_distances=True)
    assert cached.task_distance('S', []) == 0.0 and not calls
    tasks = [('o0', 'd'), ('o0', 'p')]
    assert cached.task_distance('S', tasks) == task_distance(p, 'S', tasks) == 5.0
    assert ('C0', 'C0') in calls


def test_original_float_sum_order_is_preserved():
    p = tiny_problem(3, 1)
    matrix = {('S', 'C0'): 1e16, ('C0', 'C1'): 1.0, ('C1', 'S'): 1.0}
    p.distance = lambda a, b: matrix.get((a, b), 0.0)
    cached = SingleBatchDescent(p, cache_route_distances=True)
    tasks = [('o0', 'd'), ('o1', 'p')]
    assert cached.task_distance('S', tasks) == task_distance(p, 'S', tasks) == 1e16
    assert cached.task_distance('S', list(tasks)) == 1e16


def test_full_sequence_cache_is_bounded_and_evicted_routes_are_recomputed():
    p = tiny_problem(3, 1)
    cached = SingleBatchDescent(p, cache_distances=True, cache_route_distances=True)
    first = ('C0',)*12
    for visits in itertools.islice(itertools.product(('C0', 'C1'), repeat=12), 2050):
        cached.route_distance('S', visits)
    assert cached.route_distance.cache_info().currsize == 2048
    misses = cached.route_distance.cache_info().misses
    tasks = [('o0', 'd')]*12
    assert cached.task_distance('S', tasks) == task_distance(p, 'S', tasks)
    assert cached.route_distance.cache_info().misses == misses+1


def test_new_context_rebinds_changed_problem_geometry():
    p = tiny_problem(3, 1)
    cached = SingleBatchDescent(p, cache_distances=True, cache_route_distances=True)
    tasks = [('o0', 'd'), ('o1', 'd')]
    old = cached.task_distance('S', tasks)
    previous = p.distance
    p.distance = lambda a, b: previous(a, b)+1.0
    fresh = SingleBatchDescent(p, cache_distances=True, cache_route_distances=True)
    assert fresh.route_distance.cache_info().currsize == 0
    assert fresh.task_distance('S', tasks) == task_distance(p, 'S', tasks) == old+3


def test_cached_geometry_keeps_deferred_proposals_and_independent_audits_fresh():
    p, state, _ = sequential_trips(3)
    _, decoded, issues = evaluate(p, state)
    assert not issues
    saved = deepcopy(state)
    reference = SingleBatchDescent(p, cache_sites=False, cache_distances=True)
    cached = SingleBatchDescent(p, cache_sites=False, cache_distances=True, cache_route_distances=True)
    improved = False
    while True:
        a = reference.propose(state, decoded, time.perf_counter()+10)
        b = cached.propose(state, decoded, time.perf_counter()+10)
        assert a == b
        if a is None:
            break
        score, result, violations = evaluate(p, b)
        if not violations:
            audit = audit_solution(p, result)
            assert audit['valid'] and audit['periodic_replay']['valid']
            improved |= score < decoded['info']['objective']
        b['e2'][0]['tasks'].clear()
        assert state == saved
    assert improved and reference.recovered == cached.recovered
