"""Exact local-score equivalence, including infeasible directed route proposals."""
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
def test_prepared_scores_and_repair_match_reference_exactly(model, seed):
    rng = random.Random(seed)
    base = tiny_problem(model, seed)
    orders = [replace(o, id=f'{o.id}_{i}', goods_weight=rng.random()*7,
                      count=rng.randint(1, 3)) for i in range(3) for o in base.orders]
    p = MultiBatchInstance(base.network, orders,
        replace(base.operating_policy, e2_max_trips_per_vehicle=12), model=model)
    p.fleet.e2_weight_cap = 4.3
    p.fleet.e2_volume_cap = 3.7
    prepared = SingleBatchRouteScorer(p)
    pool = [(o.id, k) for o in orders for k in p.task_kinds]
    for _ in range(120):
        goods = {(s, 'b1'): rng.randint(0, 100) for s in p.satellites}
        route = dict(satellite=rng.choice(p.satellites), vehicle=0,
                     tasks=rng.choices(pool, k=rng.randrange(15)))
        if rng.random() < .5:
            route.update(depart_after=rng.randrange(120), return_after=rng.randrange(120))
        ready = rng.randrange(130)
        assert prepared(p, route, goods, ready) == _route_score(p, route, goods, ready)
        assert prepared(p, deepcopy(route), dict(goods), ready) == _route_score(p, route, goods, ready)
    state = chronological_state(p, rng)
    saved = deepcopy(state)
    removed = orders[1:4]
    fast = repair_orders(p, state, removed, time.perf_counter()+10)
    reference = repair_orders(p, state, removed, time.perf_counter()+10, fast_scores=False)
    assert fast == reference and fast is not None
    assert state == saved
    assert repair_orders(p, state, removed, 0) is None


@pytest.mark.parametrize('model', [1, 3])
def test_retained_scorer_uses_current_goods_vehicle_waits_site_and_mutable_tasks(model):
    from itertools import product
    p = tiny_problem(model, 1)
    scorer = SingleBatchRouteScorer(p)
    route = dict(satellite='S', vehicle=0, tasks=[(p.orders[0].id, k) for k in p.task_kinds])
    for site, goods_ready, vehicle_ready, depart, returned in product(
            p.satellites, (0, 20, 80), (0, 30, 100), (0, 40), (0, 90)):
        route.update(satellite=site, depart_after=depart, return_after=returned)
        goods = {(s, 'b1'): goods_ready for s in p.satellites}
        expected = _route_score(p, route, goods, vehicle_ready)
        assert scorer(p, route, goods, vehicle_ready) == expected
        assert scorer(p, deepcopy(route), dict(goods), vehicle_ready) == expected
        route['tasks'].reverse()
        assert scorer(p, route, goods, vehicle_ready) == _route_score(p, route, goods, vehicle_ready)
        route['tasks'].reverse()


@pytest.mark.parametrize('model', [1, 3])
def test_context_reuses_scores_across_repairs_with_changed_schedule(model):
    from models.routing.solvers.alns.single_batch_cache import SingleBatchCache
    p, rng = tiny_problem(model, 1), random.Random(5)
    cache = SingleBatchCache(p)
    assert cache._route_scorer is None
    scorer = cache.route_scorer
    state = chronological_state(p, rng)
    for i in range(12):
        if i % 3 == 0:
            state['deliveries']['b1'][0].reverse()
        if state['e2']:
            state['e2'][0]['return_after'] = i
        saved = deepcopy(state)
        removed = rng.sample(list(p.orders), 2)
        actual = repair_orders(p, state, removed, time.perf_counter()+5, scorer=scorer)
        expected = repair_orders(p, state, removed, time.perf_counter()+5, fast_scores=False)
        assert actual == expected and actual is not None and state == saved
        assert cache.route_scorer is scorer
        state = actual
    assert scorer.distance.cache_info().hits > 0
    saved = deepcopy(state)
    assert repair_orders(p, state, p.orders, 0, scorer=scorer) is None and state == saved


def test_new_solve_reprepares_same_problem_after_input_edit(monkeypatch):
    from models.routing.solvers.alns import single_batch
    from models.routing.solvers.alns.single_batch_cache import SingleBatchCache
    contexts = []
    def context(problem):
        cache = SingleBatchCache(problem)
        contexts.append(cache)
        return cache
    monkeypatch.setattr(single_batch, 'SingleBatchCache', context)
    p = tiny_problem(1, 1)
    first = single_batch.solve_alns(p, max_time=10, max_iterations=40)
    scorer = contexts[-1].route_scorer
    route = dict(satellite='S', tasks=[(p.orders[0].id, 'd')])
    goods = {(s, 'b1'): 0 for s in p.satellites}
    previous = scorer(p, route, goods, 0)
    p.fleet.container_weight = 2
    p._travel['S', p.orders[0].customer] += 2
    second = single_batch.solve_alns(p, max_time=10, max_iterations=40)
    other = contexts[-1].route_scorer
    assert other is not scorer and len(contexts) == 2
    assert other(p, route, goods, 0) == _route_score(p, route, goods, 0)
    assert other(p, route, goods, 0) != previous
    assert other.tasks[p.orders[0].id, 'd'][1] != scorer.tasks[p.orders[0].id, 'd'][1]
    assert first['audit']['valid']
    assert second['info']['status'] in ('FEASIBLE', 'HEURISTIC_NO_SOLUTION')


def test_partition_route_scorers_are_isolated_and_wrong_context_is_rejected():
    from models.routing.common.cycle.assignment import input_assignment
    from models.routing.solvers.alns.assignment import AssignmentSearch
    from models.routing.solvers.alns.single_batch import solve_alns
    p = tiny_problem(4, 1)
    search = AssignmentSearch(p, 42)
    assignment = input_assignment(p)
    fixed, first = search.context(assignment)
    changed = {c: next(s for s in p.satellites if s != site) for c, site in assignment.items()}
    other, second = search.context(changed)
    assert first.route_scorer is search.context(assignment)[1].route_scorer
    assert first.route_scorer is not second.route_scorer
    with pytest.raises(ValueError, match='one problem'):
        first.route_scorer(other, dict(tasks=[]), {}, 0)
    with pytest.raises(ValueError, match='one problem'):
        solve_alns(other, _context=first)


@pytest.mark.parametrize('model', [1, 3])
@pytest.mark.parametrize('seed', [1, 3, 4])
def test_prepared_decoder_matches_reference_for_valid_and_invalid_moves(model, seed):
    from models.routing.solvers.alns.evaluator import evaluate, initial_state
    from models.routing.solvers.alns.single_batch import _move
    from models.routing.solvers.alns.single_batch_cache import SingleBatchEvaluator
    p = tiny_problem(model, seed)
    rng = random.Random(7)
    state = initial_state(p, rng)
    memo = SingleBatchEvaluator(p, capacity=2, distance_capacity=3)
    reference = evaluate(p, state)
    actual = memo(p, state)
    assert actual == reference
    actual[1]['e1_trips'][0]['events'][0]['quantity'] = 999
    actual[1]['e2_trips'][0]['events'][0]['order_id'] = 'changed'
    assert memo(p, state) == reference
    for i in range(120):
        op = ['relocate', 'reverse', 'vehicle', 'split_merge', 'e1_delivery', 'e1_pickup', 'pair'][i % 7]
        state = _move(p, state, rng, op)
        if i % 10 == 0 and state['e2']:
            state['e2'][0]['return_after'] = 100
        assert memo(p, state) == evaluate(p, state)
        assert len(memo.distances) <= 3 and len(memo.entries) <= 2 and len(memo.deliveries) <= 2
    assert memo.distance_hits > 0 and memo.distance_misses > 0
    assert memo.delivery_hits > 0 and memo.delivery_misses > 0


def test_provisional_delivery_cache_rechecks_coverage_and_keeps_final_returns_separate():
    from models.routing.solvers.alns.evaluator import evaluate, initial_state
    from models.routing.solvers.alns.single_batch_cache import SingleBatchEvaluator
    from tests.routing.test_single_batch_solver_audit import nonmetric_problem
    p = nonmetric_problem()
    state = dict(deliveries={'b1': [['T', 'S']]}, pickup_order={'b1': ['S', 'T']},
                 pickup_vehicle={'b1': {}}, e2=[
                     dict(satellite='S', vehicle=0, tasks=[('a', 'd'), ('a', 'p')]),
                     dict(satellite='T', vehicle=0, tasks=[('b', 'd'), ('b', 'p')])])
    memo = SingleBatchEvaluator(p, capacity=2)
    # Its delivery-only return is late, but insertion of pickups gives a valid
    # nonmetric mixed route. The cache cannot reject the provisional return.
    assert memo(p, state) == evaluate(p, state) and not memo(p, state)[2]
    batch = p.operating_policy.batches[0]
    saved = memo.delivery_check(batch, 0, state['deliveries']['b1'][0])
    assert isinstance(saved[0], tuple) and isinstance(saved[1], tuple)
    changed = deepcopy(state)
    changed['pickup_order']['b1'].reverse()
    assert memo(p, changed) == evaluate(p, changed)
    missing = deepcopy(state)
    missing['deliveries']['b1'][0].pop()
    assert memo(p, missing) == evaluate(p, missing)
    assert memo(p, missing)[2] == ['E1_DELIVERY_COVERAGE']
    repeated = deepcopy(state)
    repeated['deliveries']['b1'][0].append('T')
    assert memo(p, repeated) == evaluate(p, repeated)
    empty = initial_state(p, random.Random(1))
    empty['deliveries']['b1'][0].clear()
    assert memo(p, empty) == evaluate(p, empty)
    assert len(memo.deliveries) <= 2


def test_prepared_distances_cover_layer_site_sequence_and_duplicate_visits():
    from models.routing.solvers.alns.evaluator import route_distance
    from models.routing.solvers.alns.single_batch_cache import SingleBatchEvaluator
    p = tiny_problem(3, 1)
    memo = SingleBatchEvaluator(p, distance_capacity=2)
    for layer in ('e1', 'e2'):
        field = 'site_id' if layer == 'e1' else 'order_id'
        ids = p.satellites if layer == 'e1' else [o.id for o in p.orders]
        for site in p.satellites:
            trip = dict(satellite_id=site, events=[])
            for sequence in (ids, ids[::-1], [ids[0], ids[0]], []):
                trip['events'][:] = [{field: oid} for oid in sequence]
                assert memo.route_distance(p, trip, layer) == route_distance(p, trip, layer)
                assert memo.route_distance(p, deepcopy(trip), layer) == route_distance(p, trip, layer)
                assert len(memo.distances) <= 2
    with pytest.raises(ValueError, match='one problem'):
        memo.route_distance(tiny_problem(3, 1), trip, layer)


def test_prepared_scorer_cannot_cross_problem_instances():
    first, other = tiny_problem(3, 2), tiny_problem(3, 2)
    with pytest.raises(ValueError, match='one problem'):
        SingleBatchRouteScorer(first)(other, dict(tasks=[]), {}, 0)


@pytest.mark.parametrize('model', [1, 3])
def test_single_batch_e1_cache_keeps_full_decoder_results_and_return_ownership(model):
    from models.routing.solvers.alns.cycle_cache import CycleEvaluator
    from models.routing.solvers.alns.evaluator import evaluate, initial_state
    from models.routing.solvers.alns.single_batch import _move
    p = tiny_problem(model, 1)
    rng = random.Random(4)
    state = initial_state(p, rng)
    memo = CycleEvaluator(p, capacity=2)
    reference = evaluate(p, state)
    actual = memo(p, state)
    assert actual == reference
    assert actual[1]['e1_trips']
    actual[1]['e1_trips'][0]['events'][0]['quantity'] = 999
    assert memo(p, state) == reference
    for i in range(60):
        op = ['relocate', 'reverse', 'vehicle', 'e1_delivery', 'e1_pickup'][i % 5]
        state = _move(p, state, rng, op)
        if i % 10 == 0 and state['e2']:
            state['e2'][0]['return_after'] = 100
        assert memo(p, state) == evaluate(p, state)
    assert memo.hits > 0 and memo.misses > 0 and len(memo.entries) <= 2
