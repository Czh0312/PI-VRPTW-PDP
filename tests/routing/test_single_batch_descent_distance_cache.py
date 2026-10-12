"""Arc memoization must preserve directed gains, ordering and audited proposals."""
from copy import deepcopy
from dataclasses import replace
import random
import time

import pytest

from models.common.cycle import MultiBatchInstance
from models.common.cycle.audit import audit_solution
from models.routing.model_3.alns.construction import chronological_state
from models.routing.model_3.alns.evaluator import evaluate
from models.routing.model_3.alns.neighborhoods import state_key
from models.routing.model_3.alns.single_batch_descent import SingleBatchDescent, task_distance
from tests.routing.test_single_batch_solver_audit import tiny_problem
from tests.routing.test_single_batch_descent_readiness import sequential_trips


@pytest.mark.parametrize('model', [1, 3])
@pytest.mark.parametrize('seed', range(6))
def test_complete_directed_enumeration_and_site_options_match_reference(model, seed):
    base = tiny_problem(model, seed)
    orders = [replace(o, id=f'{o.id}_{i}') for i in range(3) for o in base.orders]
    problem = MultiBatchInstance(base.network, orders,
        replace(base.operating_policy, e2_max_trips_per_vehicle=12), model=model)
    state = chronological_state(problem, random.Random(seed))
    saved = deepcopy(state)
    fast = SingleBatchDescent(problem, cache_distances=True)
    reference = SingleBatchDescent(problem, cache_distances=False)
    assert fast._enumerate_routes(state['e2'], time.perf_counter()+10) == reference._enumerate_routes(
        state['e2'], time.perf_counter()+10)
    assert fast._e2_candidates(state, time.perf_counter()+10) == reference._e2_candidates(
        state, time.perf_counter()+10)
    assert fast.distance.cache_info().hits > 0
    for route in state['e2']:
        assert fast.task_distance(route['satellite'], route['tasks']) == task_distance(
            problem, route['satellite'], route['tasks'])
    assert state == saved


@pytest.mark.parametrize('model', [1, 3])
def test_recovered_shorter_proposal_matches_uncached_screen_and_still_audits(model):
    problem, state, merged = sequential_trips(model)
    saved = deepcopy(state)
    _, decoded, issues = evaluate(problem, state)
    assert not issues
    proposals = []
    for cached in (False, True):
        descent = SingleBatchDescent(problem, cache_distances=cached)
        descent.key = state_key(state)
        descent.queue = [('e2', ((0, []), (1, merged)))]
        result = descent.propose(state, decoded, time.perf_counter()+1)
        assert result is not None
        assert audit_solution(problem, evaluate(problem, result)[1])['periodic_replay']['valid']
        proposals.append(result)
    assert proposals[0] == proposals[1] and state == saved


def test_arc_cache_is_bounded_directed_and_new_context_does_not_reuse_input_values():
    problem = tiny_problem(1, 0)
    calls = []
    def distance(a, b):
        calls.append((a, b))
        return float(100*a+b)
    problem.distance = distance
    descent = SingleBatchDescent(problem, cache_distances=True)
    assert descent.distance(1, 2) == 102 and descent.distance(2, 1) == 201
    assert descent.distance(1, 2) == 102 and len(calls) == 2
    for a in range(65):
        for b in range(65):
            assert descent.distance(a, b) == distance(a, b)
    assert descent.distance.cache_info().currsize == 4096
    # A new solve/context rebinds the API and starts with an empty arc cache.
    problem.distance = lambda a, b: float(100*a+b+7)
    fresh = SingleBatchDescent(problem, cache_distances=True)
    assert fresh.distance.cache_info().currsize == 0
    assert fresh.distance(1, 2) == 109
