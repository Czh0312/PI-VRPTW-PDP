"""Bounded route diversity versus independent complete-enumeration selection."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
import random
import time
import pytest
from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.service import solve_routing
from models.routing.solvers.alns.construction import chronological_state
from models.routing.solvers.alns.single_batch_descent import SingleBatchDescent
from tests.routing.test_single_batch_solver_audit import tiny_problem


def signature(item):
    return tuple((index, tuple(tasks)) for index, tasks in item[2])


@pytest.mark.parametrize('seed', range(6))
@pytest.mark.parametrize('capacity', [1, 5, 256])
def test_bounded_heaps_match_complete_enumeration_oracle(seed, capacity):
    base = tiny_problem(3, seed)
    orders = [replace(o, id=f'{o.id}_{i}') for i in range(3) for o in base.orders]
    problem = MultiBatchInstance(base.network, orders,
        replace(base.operating_policy, e2_max_trips_per_vehicle=12), model=3)
    state = chronological_state(problem, random.Random(seed))
    saved = deepcopy(state)
    complete = SingleBatchDescent(problem, capacity=100000)._enumerate_routes(state['e2'], time.perf_counter()+10)
    assert len(complete) < 100000
    first, counts, positioned = {}, Counter(), []
    for rank, item in enumerate(complete):
        key = tuple(sorted(index for index, _ in item[2]))
        first.setdefault(key, rank)
        positioned.append((counts[key], first[key], item))
        counts[key] += 1
    expected = [item for _, _, item in sorted(positioned, key=lambda x: x[:2])[:capacity]]
    actual = SingleBatchDescent(problem, capacity=capacity, diversify=True,
                               cache_distances=True)._enumerate_routes(state['e2'], time.perf_counter()+10)
    assert [signature(r) for r in actual] == [signature(r) for r in expected]
    assert [r[0] for r in actual] == [r[0] for r in expected]
    assert len(actual) <= capacity and len({signature(r) for r in actual}) == len(actual)
    assert state == saved


@pytest.mark.parametrize('model', [1, 2, 3, 4])
def test_public_scope_and_full_audit(model):
    p = tiny_problem(model, 1)
    result = solve_routing(model=model, method='alns', problem=p, alns_time=5, alns_iter=200, seed=14)
    assert bool(result['info'].get('descent_diversity_enabled')) is (model == 3)
    audited = audit_solution(p, result)
    assert audited['valid'] and audited['periodic_replay']['valid']
