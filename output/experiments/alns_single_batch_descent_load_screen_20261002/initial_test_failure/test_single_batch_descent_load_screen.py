"""Event-load necessity and queue replacement for fixed single-batch descent."""
from copy import deepcopy
from itertools import permutations
import random
import time

import pytest

from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.single_batch import solve_alns
from models.routing.solvers.alns.single_batch_descent import SingleBatchDescent
from test_single_batch_descent_readiness import sequential_trips


@pytest.mark.parametrize('weight_cap,volume_cap', [(2, 2), (4, 4), (1, 10), (10, 1)])
def test_all_complete_task_orders_match_decoder_capacity(weight_cap, volume_cap):
    problem, state, _ = sequential_trips(3)
    problem.fleet.e2_weight_cap = weight_cap
    problem.fleet.e2_volume_cap = volume_cap
    descent = SingleBatchDescent(problem, screen_loads=True)
    tasks = [('A', 'd'), ('A', 'p'), ('B', 'd'), ('B', 'p')]
    for order in permutations(tasks):
        candidate = deepcopy(state)
        candidate['e2'] = [dict(satellite='S', vehicle=0, tasks=list(order))]
        issues = evaluate(problem, candidate)[2]
        assert descent.load_feasible(order) == ('E2_CAPACITY' not in issues)


@pytest.mark.parametrize('excess,expected', [(0, True), (0.5e-6, True), (2e-6, False)])
def test_decoder_tolerance_on_initial_weight(excess, expected):
    problem, _, _ = sequential_trips(3)
    task = ('B', 'd')
    problem.fleet.e2_weight_cap = problem.task(*task)[1]-excess
    descent = SingleBatchDescent(problem, screen_loads=True)
    assert descent.load_feasible((task,)) is expected


def test_event_pickups_and_volume_are_checked_after_initial_loading():
    problem, _, _ = sequential_trips(3)
    descent = SingleBatchDescent(problem, screen_loads=True)
    # Repeating external pickup tasks exposes an event overload after an
    # initially empty load. Normal generated proposals preserve task coverage.
    problem.fleet.e2_weight_cap = 100
    problem.fleet.e2_volume_cap = 1
    assert not descent.load_feasible((('A', 'p'), ('B', 'p')))
    assert descent.load_feasible(())


def test_queue_contains_best_load_feasible_geometric_candidates():
    problem, state, _ = sequential_trips(3)
    # Directed/nonmetric data stresses relocation, reversal and paired moves.
    rng = random.Random(29)
    for a, row in problem.network.distance_matrix.items():
        for b in row:
            if a != b:
                row[b] = rng.randrange(1, 30)
    original = deepcopy(state)
    broad = SingleBatchDescent(problem, capacity=10000)
    all_options = broad._enumerate_routes(state['e2'], float('inf'))
    screened = SingleBatchDescent(problem, capacity=3, screen_loads=True)
    options = screened._enumerate_routes(state['e2'], float('inf'))
    feasible = [r for r in all_options if all(screened.load_feasible(tuple(ts)) for _, ts in r[2])]
    assert [r[2] for r in options] == [r[2] for r in feasible[:3]]
    assert screened.load_screen_rejections > 0
    assert state == original


def test_cache_is_bounded_and_problem_local():
    problem, _, _ = sequential_trips(3)
    first = SingleBatchDescent(problem, screen_loads=True)
    for n in range(600):
        first.load_feasible((('A', 'p'),)*n)
    assert first.load_feasible.cache_info().currsize == 512
    second = SingleBatchDescent(problem, screen_loads=True)
    assert second.load_feasible.cache_info().currsize == 0
    disabled = SingleBatchDescent(problem)
    assert disabled.load_data is None and disabled.load_feasible is None


@pytest.mark.parametrize('model,enabled', [(1, False), (2, False), (3, True), (4, False)])
def test_public_scope_and_final_independent_audit(model, enabled):
    problem, _, _ = sequential_trips(model)
    result = solve_alns(problem, max_iterations=30, max_time=10, seed=291)
    assert result['info']['descent_load_screen_enabled'] is enabled
    assert audit_solution(problem, result)['valid']
    assert audit_solution(problem, result)['periodic_replay']['valid']
    if not enabled:
        assert result['info']['descent_load_screen_checks'] == 0
        assert result['info']['descent_load_screen_rejections'] == 0
