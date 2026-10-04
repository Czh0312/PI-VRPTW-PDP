"""Seeded repair diversity must preserve complete requests and decoder authority."""
from copy import deepcopy
from dataclasses import replace
import random
import time

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.assignment import assigned_problem, input_assignment
from models.routing.solvers.alns.construction import chronological_state
from models.routing.solvers.alns import neighborhoods
from tests.routing.test_single_batch_solver_audit import tiny_problem


def repair_problem(model):
    base = tiny_problem(model, 1)
    orders = [replace(o, id=f'{o.id}_{i}', delivery_window=(0, 70),
                      pickup_window=(0, 80)) for i in range(3) for o in base.orders]
    problem = MultiBatchInstance(base.network, orders,
        replace(base.operating_policy, container_pool_size=9,
                e2_max_trips_per_vehicle=12), model=model)
    return assigned_problem(problem, input_assignment(problem)) if model in (2, 4) else problem


@pytest.mark.parametrize('model', [1, 2, 3, 4])
@pytest.mark.parametrize('seed', [42, 43, 44])
def test_shuffled_repair_matches_unscreened_reference_and_preserves_requests(model, seed):
    problem = repair_problem(model)
    state = chronological_state(problem, random.Random(0))
    saved = deepcopy(state)
    removed = list(problem.orders)[1:6]
    saved_removed = list(removed)
    fast = neighborhoods.repair_orders(problem, state, removed, time.perf_counter()+5,
                                       order_rng=random.Random(seed))
    reference = neighborhoods.repair_orders(problem, state, removed, time.perf_counter()+5,
        order_rng=random.Random(seed), screen=False, cache_scores=False, fast_scores=False)
    assert fast is not None and fast == reference
    assert state == saved and removed == saved_removed
    assert sorted(t for r in fast['e2'] for t in r['tasks']) == sorted(
        (o.id, kind) for o in problem.orders for kind in problem.task_kinds)
    for route in fast['e2']:
        assert all(problem.order_map[oid].satellite == route['satellite'] for oid, _ in route['tasks'])
        for oid, kind in route['tasks']:
            if kind == 'p' and (oid, 'd') in route['tasks']:
                assert route['tasks'].index((oid, 'd')) < route['tasks'].index((oid, 'p'))


@pytest.mark.parametrize('model', [1, 3])
def test_shuffled_order_changes_proposals_without_mutating_the_seed(model):
    problem = repair_problem(model)
    state = chronological_state(problem, random.Random(0))
    saved = deepcopy(state)
    proposals = [neighborhoods.destroy_repair(problem, state, random.Random(seed),
                 'shuffled', time.perf_counter()+5) for seed in range(8)]
    assert len({neighborhoods.state_key(p) for p in proposals}) > 1
    assert state == saved
    assert neighborhoods.destroy_repair(problem, state, random.Random(0),
        'shuffled', 0) == saved


@pytest.mark.parametrize('model', [1, 3])
def test_shuffled_repair_expiry_never_exposes_partial_request_coverage(model, monkeypatch):
    problem = repair_problem(model)
    state = chronological_state(problem, random.Random(0))
    saved = deepcopy(state)
    calls = []
    reference = neighborhoods._route_score
    def expire_after_scoring(*args):
        calls.append(True)
        return reference(*args)
    monkeypatch.setattr(neighborhoods, '_route_score', expire_after_scoring)
    monkeypatch.setattr(neighborhoods.time, 'perf_counter', lambda: 2 if calls else 0)
    assert neighborhoods.repair_orders(problem, state, list(problem.orders), 1,
        order_rng=random.Random(42), fast_scores=False) is None
    assert calls and state == saved
