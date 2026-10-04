"""Delivery insertion prefixes preserve exact score arithmetic and task choices."""
from copy import deepcopy
from dataclasses import replace
import random
import time

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.service import solve_routing
from models.routing.solvers.alns.construction import chronological_state
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.neighborhoods import _route_score, repair_orders
from models.routing.solvers.alns.single_batch_scoring import SingleBatchRouteScorer
from tests.routing.test_single_batch_solver_audit import tiny_problem


@pytest.mark.parametrize('seed', range(6))
def test_every_delivery_position_matches_reference_with_directed_invalid_and_duplicate_tasks(seed):
    base = tiny_problem(3, seed)
    rng = random.Random(seed)
    orders = [replace(o, id=f'{o.id}_{i}', count=rng.randint(1, 3), goods_weight=rng.random()*7)
              for i in range(3) for o in base.orders]
    p = MultiBatchInstance(base.network, orders, base.operating_policy, model=3)
    p.fleet.e2_weight_cap, p.fleet.e2_volume_cap = 4.3, 3.7
    scorer = SingleBatchRouteScorer(p)
    pool = [(o.id, k) for o in orders for k in p.task_kinds]
    for _ in range(45):
        route = dict(satellite=rng.choice(p.satellites), vehicle=0,
                     tasks=rng.choices(pool, k=rng.randrange(12)),
                     depart_after=rng.randrange(120), return_after=rng.randrange(120))
        goods = {(s, 'b1'): rng.randrange(110) for s in p.satellites}
        ready = rng.randrange(120)
        before = deepcopy(route)
        prefix = scorer.prepare_delivery_insertions(p, route, goods, ready)
        oid = rng.choice(orders).id
        positions = list(range(len(route['tasks'])+1));rng.shuffle(positions)
        for position in positions+list(reversed(positions)):
            candidate = {**route, 'tasks': route['tasks'][:position]+[(oid, 'd')]+route['tasks'][position:]}
            assert prefix(oid, position) == scorer(p, candidate, goods, ready) == _route_score(p, candidate, goods, ready)
        assert route == before


@pytest.mark.parametrize('capacity', [0.1, 0.5, 1.0, 4.3, 1e16])
def test_fractional_and_nonassociative_initial_delivery_load_uses_original_order(capacity):
    base = tiny_problem(3, 1)
    orders = [replace(o, goods_weight=w) for o, w in zip(base.orders, (1e16, 1, 1))]
    p = MultiBatchInstance(base.network, orders, base.operating_policy, model=3)
    p.fleet.container_weight, p.fleet.container_volume = .125, .1
    p.fleet.e2_weight_cap, p.fleet.e2_volume_cap = capacity, .2
    scorer = SingleBatchRouteScorer(p)
    route = dict(satellite='S', tasks=[(o.id, 'd') for o in orders], return_after=100)
    goods = {(s, 'b1'): 60 for s in p.satellites}
    prefix = scorer.prepare_delivery_insertions(p, route, goods, 30)
    for oid in p.order_map:
        for pos in range(4):
            candidate = {**route, 'tasks': route['tasks'][:pos]+[(oid, 'd')]+route['tasks'][pos:]}
            assert prefix(oid, pos) == _route_score(p, candidate, goods, 30)


def test_reprepare_after_goods_waits_vehicle_and_input_changes_and_no_prefix_alias():
    p = tiny_problem(3, 1)
    scorer = SingleBatchRouteScorer(p)
    route = dict(satellite='S', tasks=[('o1', 'd')], depart_after=20, return_after=40)
    goods = {(s, 'b1'): 0 for s in p.satellites}
    for goods_ready, ready, depart, returned in [(0, 0, 20, 40), (30, 60, 80, 100), (1, 1, 1, 1)]:
        route.update(depart_after=depart, return_after=returned)
        goods = {(s, 'b1'): goods_ready for s in p.satellites}
        prefix = scorer.prepare_delivery_insertions(p, route, goods, ready)
        for pos in (1, 0, 1):
            candidate = {**route, 'tasks': route['tasks'][:pos]+[('o1', 'd')]+route['tasks'][pos:]}
            assert prefix('o1', pos) == _route_score(p, candidate, goods, ready)
    with pytest.raises(ValueError, match='one problem'):
        scorer.prepare_delivery_insertions(tiny_problem(3, 0), route, goods, 0)


@pytest.mark.parametrize('seed', [0, 1, 3, 4, 5])
def test_full_repair_choices_task_multiset_input_and_audits_match_reference(seed):
    p = tiny_problem(3, seed)
    rng = random.Random(seed)
    state = chronological_state(p, rng)
    scorer = SingleBatchRouteScorer(p)
    for _ in range(18):
        removed = rng.sample(list(p.orders), 2)
        before = deepcopy(state)
        a = repair_orders(p, state, removed, time.perf_counter()+3, scorer=scorer, cache_distances=True, cache_pickup_prefix=True)
        b = repair_orders(p, state, removed, time.perf_counter()+3, scorer=scorer, cache_distances=True, cache_delivery_prefix=True, cache_pickup_prefix=True)
        assert a == b and a is not None and state == before
        assert sorted(t for r in b['e2'] for t in r['tasks']) == sorted((o.id, k) for o in p.orders for k in p.task_kinds)
        _, decoded, issues = evaluate(p, b)
        if not issues:
            audit = audit_solution(p, decoded)
            assert audit['valid'] and audit['periodic_replay']['valid']
        assert repair_orders(p, state, removed, 0, scorer=scorer, cache_delivery_prefix=True, cache_pickup_prefix=True) is None
        state = b


@pytest.mark.parametrize('subclass', [False, True])
def test_custom_scorer_keeps_original_call_path(subclass):
    p = tiny_problem(3, 1)
    state = chronological_state(p, random.Random(1))
    class Custom(SingleBatchRouteScorer):
        def prepare_delivery_insertions(self, *args):
            raise AssertionError('Custom scorer must retain its original score API')
    scorer = Custom(p) if subclass else _route_score
    a = repair_orders(p, state, p.orders[:1], time.perf_counter()+3, scorer=scorer)
    b = repair_orders(p, state, p.orders[:1], time.perf_counter()+3, scorer=scorer, cache_delivery_prefix=True, cache_pickup_prefix=True)
    assert a == b


@pytest.mark.parametrize('model', [1, 2, 3, 4])
def test_public_activation_is_only_m3_fixed_recovery(monkeypatch, model):
    from models.routing.solvers.alns import single_batch
    original = single_batch.destroy_repair
    flags = []
    def wrapped(*args, **kwargs):
        flags.append(kwargs.get('cache_delivery_prefix', False))
        return original(*args, **kwargs)
    monkeypatch.setattr(single_batch, 'destroy_repair', wrapped)
    result = solve_routing(model=model, method='alns', problem=tiny_problem(model, 1),
                           alns_time=5, alns_iter=100, seed=11)
    assert flags and all(flag == (model == 3) for flag in flags)
    assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']


def test_preparing_prefixes_does_not_traverse_unrequested_events():
    p = tiny_problem(3, 1)
    scorer = SingleBatchRouteScorer(p)
    original_travel = scorer.travel
    calls = []
    def travel(a, b):
        calls.append((a, b))
        return original_travel(a, b)
    scorer.travel = travel
    route = dict(satellite='S', tasks=[(o.id, 'd') for o in p.orders])
    goods = {(s, 'b1'): 0 for s in p.satellites}
    prefix = scorer.prepare_delivery_insertions(p, route, goods, 0)
    assert not calls
    for pos in (0, 1, 0, 3, 2):
        candidate = {**route, 'tasks': route['tasks'][:pos]+[('o1', 'd')]+route['tasks'][pos:]}
        assert prefix('o1', pos) == _route_score(p, candidate, goods, 0)



@pytest.mark.parametrize('tasks', [[], [('o1', 'p')], [('o1', 'p'), ('o2', 'p')]])
def test_delivery_adds_goods_wait_and_dispatch_preparation_to_pickup_only_route(tasks):
    p = tiny_problem(3, 1)
    scorer = SingleBatchRouteScorer(p)
    route = dict(satellite='S', tasks=tasks, depart_after=20, return_after=40)
    goods = {(s, 'b1'): 130 for s in p.satellites}
    helper = scorer.prepare_delivery_insertions(p, route, goods, 5)
    for oid in p.order_map:
        for pos in range(len(tasks)+1):
            candidate = {**route, 'tasks': tasks[:pos]+[(oid, 'd')]+tasks[pos:]}
            assert helper(oid, pos) == _route_score(p, candidate, goods, 5)


def test_nonassociative_initial_loads_do_not_share_prefixes():
    base = tiny_problem(3, 1)
    orders = [replace(o, count=1, goods_weight=w) for o, w in zip(base.orders, (1e16, 1, 1))]
    p = MultiBatchInstance(base.network, orders, base.operating_policy, model=3)
    p.fleet.container_weight = 0
    scorer = SingleBatchRouteScorer(p)
    route = dict(satellite='S', tasks=[('o1', 'd'), ('o2', 'd')])
    goods = {(s, 'b1'): 0 for s in p.satellites}
    helper = scorer.prepare_delivery_insertions(p, route, goods, 0)
    for pos in (0, 2, 1, 2, 0):
        candidate = {**route, 'tasks': route['tasks'][:pos]+[('o0', 'd')]+route['tasks'][pos:]}
        assert helper('o0', pos) == _route_score(p, candidate, goods, 0)
    assert len(helper.prepared) == 2


def test_delivery_preparation_has_no_travel_and_is_reused_when_initial_loads_match():
    p = tiny_problem(3, 1)
    scorer = SingleBatchRouteScorer(p)
    travel = scorer.travel;calls = []
    def wrapped(a, b):
        calls.append((a, b));return travel(a, b)
    scorer.travel = wrapped
    route = dict(satellite='S', tasks=[('o1', 'd'), ('o1', 'p'), ('o2', 'p')])
    goods = {(s, 'b1'): 0 for s in p.satellites}
    helper = scorer.prepare_delivery_insertions(p, route, goods, 0)
    assert not calls
    for pos in (1, 2, 3):
        candidate = {**route, 'tasks': route['tasks'][:pos]+[('o0', 'd')]+route['tasks'][pos:]}
        assert helper('o0', pos) == _route_score(p, candidate, goods, 0)
    assert len(helper.prepared) == 1 and len(helper.prefixes) == 4
