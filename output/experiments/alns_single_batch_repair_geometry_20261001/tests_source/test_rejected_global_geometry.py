"""Repair arc reuse preserves directed bounds, ranking and context ownership."""
from copy import deepcopy
from dataclasses import replace
import random
import time

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.solvers.alns.construction import chronological_state
from models.routing.solvers.alns.neighborhoods import _route_score, repair_orders
from models.routing.solvers.alns.single_batch_scoring import SingleBatchRouteScorer
from tests.routing.test_single_batch_solver_audit import tiny_problem


@pytest.mark.parametrize('model', [1, 3])
@pytest.mark.parametrize('seed', range(6))
def test_cached_repair_matches_uncached_directed_reference(model, seed):
    rng = random.Random(seed)
    base = tiny_problem(model, seed)
    # Multiple orders share nodes; varied nonintegral loads and tight windows
    # exercise penalized proposals as well as distance-only ranking bounds.
    orders = [replace(o, id=f'{o.id}_{i}', goods_weight=rng.random()*5,
                      count=1+i) for i in range(4) for o in base.orders]
    p = MultiBatchInstance(base.network, orders,
        replace(base.operating_policy, e2_max_trips_per_vehicle=12), model=model)
    p.fleet.e2_weight_cap = 5.7
    p.fleet.e2_volume_cap = 4.3
    scorer = SingleBatchRouteScorer(p)
    state = chronological_state(p, rng)
    for step in range(10):
        state['deliveries']['b1'][0].reverse()
        if state['e2']:
            state['e2'][0].update(depart_after=step*3, return_after=step*4)
        saved = deepcopy(state)
        removed = rng.sample(orders, 4)
        actual = repair_orders(p, state, removed, time.perf_counter()+5, scorer=scorer)
        expected = repair_orders(p, state, removed, time.perf_counter()+5, fast_scores=False)
        assert actual == expected and actual is not None
        assert state == saved
        state = actual
    assert scorer.distance.cache_info().hits > 0
    assert scorer.distance.cache_info().currsize <= 4096
    saved = deepcopy(state)
    assert repair_orders(p, state, orders, 0, scorer=scorer) is None
    assert state == saved


def test_repair_reuses_prepared_arcs_and_custom_scorer_keeps_distance_api(monkeypatch):
    p = tiny_problem(3, 1)
    original_distance = p.distance
    calls = []
    def distance(a, b):
        calls.append((a, b))
        return original_distance(a, b)
    monkeypatch.setattr(p, 'distance', distance)
    state = chronological_state(p, random.Random(4))
    calls.clear()
    scorer = SingleBatchRouteScorer(p)
    first = repair_orders(p, state, p.orders[:2], time.perf_counter()+5, scorer=scorer)
    assert first is not None and calls
    count = len(calls)
    assert repair_orders(p, state, p.orders[:2], time.perf_counter()+5, scorer=scorer) == first
    assert len(calls) == count
    assert len(calls) == len(set(calls))
    assert any(a != b and (b, a) in calls for a, b in calls)

    # The scorer argument accepts arbitrary callables. An attribute named
    # distance on one of those is not a prepared, fixed-problem cache.
    def custom(problem, route, goods, ready):
        return _route_score(problem, route, goods, ready)
    def forbidden(*args):
        raise AssertionError('Custom geometry must not be selected')
    custom.distance = forbidden
    assert repair_orders(p, state, p.orders[:2], time.perf_counter()+5, scorer=custom) == first
    assert len(calls) > count


def test_changed_problem_uses_new_geometry_and_rejects_old_scorer():
    p = tiny_problem(3, 1)
    other = tiny_problem(3, 1)
    node = p.orders[0].customer
    other.network.dist[other.network.node_index['S']][other.network.node_index[node]] += 8.5
    scorer = SingleBatchRouteScorer(p)
    fresh = SingleBatchRouteScorer(other)
    assert scorer.distance('S', node) != fresh.distance('S', node)
    state = chronological_state(other, random.Random(2))
    expected = repair_orders(other, state, other.orders[:1], time.perf_counter()+5, fast_scores=False)
    assert repair_orders(other, state, other.orders[:1], time.perf_counter()+5, scorer=fresh) == expected
    with pytest.raises(ValueError, match='one problem'):
        repair_orders(other, state, other.orders[:1], time.perf_counter()+5, scorer=scorer)
