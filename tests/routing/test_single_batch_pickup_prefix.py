"""Pickup insertion prefixes preserve exact score arithmetic and task choices."""
from copy import deepcopy
from dataclasses import replace
import random
import time

import pytest

from models.common.cycle import MultiBatchInstance
from models.common.cycle.audit import audit_solution
from models.routing.service import solve_routing
from models.routing.model_4.alns.construction import chronological_state
from models.routing.model_4.alns.evaluator import evaluate
from models.routing.model_4.alns.neighborhoods import _route_score, repair_orders
from models.routing.model_4.alns.single_batch_scoring import SingleBatchRouteScorer
from tests.routing.test_single_batch_solver_audit import tiny_problem


@pytest.mark.parametrize('seed', range(6))
def test_every_pickup_position_matches_reference_with_directed_invalid_and_duplicate_tasks(seed):
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
        prefix = scorer.prepare_pickup_insertions(p, route, goods, ready)
        oid = rng.choice(orders).id
        positions = list(range(len(route['tasks'])+1));rng.shuffle(positions)
        for position in positions+list(reversed(positions)):
            candidate = {**route, 'tasks': route['tasks'][:position]+[(oid, 'p')]+route['tasks'][position:]}
            assert prefix(oid, position) == scorer(p, candidate, goods, ready) == _route_score(p, candidate, goods, ready)
        assert route == before


@pytest.mark.parametrize('capacity', [0.1, 0.5, 1.0, 4.3, 1e16])
def test_fractional_and_nonassociative_initial_delivery_load_is_unchanged(capacity):
    base = tiny_problem(3, 1)
    orders = [replace(o, goods_weight=w) for o, w in zip(base.orders, (1e16, 1, 1))]
    p = MultiBatchInstance(base.network, orders, base.operating_policy, model=3)
    p.fleet.container_weight, p.fleet.container_volume = .125, .1
    p.fleet.e2_weight_cap, p.fleet.e2_volume_cap = capacity, .2
    scorer = SingleBatchRouteScorer(p)
    route = dict(satellite='S', tasks=[(o.id, 'd') for o in orders], return_after=100)
    goods = {(s, 'b1'): 60 for s in p.satellites}
    prefix = scorer.prepare_pickup_insertions(p, route, goods, 30)
    for oid in p.order_map:
        for pos in range(4):
            candidate = {**route, 'tasks': route['tasks'][:pos]+[(oid, 'p')]+route['tasks'][pos:]}
            assert prefix(oid, pos) == _route_score(p, candidate, goods, 30)


def test_reprepare_after_goods_waits_vehicle_and_input_changes_and_no_prefix_alias():
    p = tiny_problem(3, 1)
    scorer = SingleBatchRouteScorer(p)
    route = dict(satellite='S', tasks=[('o1', 'd')], depart_after=20, return_after=40)
    goods = {(s, 'b1'): 0 for s in p.satellites}
    for goods_ready, ready, depart, returned in [(0, 0, 20, 40), (30, 60, 80, 100), (1, 1, 1, 1)]:
        route.update(depart_after=depart, return_after=returned)
        goods = {(s, 'b1'): goods_ready for s in p.satellites}
        prefix = scorer.prepare_pickup_insertions(p, route, goods, ready)
        for pos in (1, 0, 1):
            candidate = {**route, 'tasks': route['tasks'][:pos]+[('o1', 'p')]+route['tasks'][pos:]}
            assert prefix('o1', pos) == _route_score(p, candidate, goods, ready)
    with pytest.raises(ValueError, match='one problem'):
        scorer.prepare_pickup_insertions(tiny_problem(3, 0), route, goods, 0)


@pytest.mark.parametrize('seed', [0, 1, 3, 4, 5])
def test_full_repair_choices_task_multiset_input_and_audits_match_reference(seed):
    p = tiny_problem(3, seed)
    rng = random.Random(seed)
    state = chronological_state(p, rng)
    scorer = SingleBatchRouteScorer(p)
    for _ in range(18):
        removed = rng.sample(list(p.orders), 2)
        before = deepcopy(state)
        a = repair_orders(p, state, removed, time.perf_counter()+3, scorer=scorer, cache_distances=True)
        b = repair_orders(p, state, removed, time.perf_counter()+3, scorer=scorer, cache_distances=True, cache_pickup_prefix=True)
        assert a == b and a is not None and state == before
        assert sorted(t for r in b['e2'] for t in r['tasks']) == sorted((o.id, k) for o in p.orders for k in p.task_kinds)
        _, decoded, issues = evaluate(p, b)
        if not issues:
            audit = audit_solution(p, decoded)
            assert audit['valid'] and audit['periodic_replay']['valid']
        assert repair_orders(p, state, removed, 0, scorer=scorer, cache_pickup_prefix=True) is None
        state = b


@pytest.mark.parametrize('subclass', [False, True])
def test_custom_scorer_keeps_original_call_path(subclass):
    p = tiny_problem(3, 1)
    state = chronological_state(p, random.Random(1))
    class Custom(SingleBatchRouteScorer):
        def prepare_pickup_insertions(self, *args):
            raise AssertionError('Custom scorer must retain its original score API')
    scorer = Custom(p) if subclass else _route_score
    a = repair_orders(p, state, p.orders[:1], time.perf_counter()+3, scorer=scorer)
    b = repair_orders(p, state, p.orders[:1], time.perf_counter()+3, scorer=scorer, cache_pickup_prefix=True)
    assert a == b


@pytest.mark.parametrize('model', [1, 2, 3, 4])
def test_public_activation_is_only_m3_fixed_recovery(monkeypatch, model):
    from models.routing.model_4.alns import single_batch
    original = single_batch.destroy_repair
    flags = []
    def wrapped(*args, **kwargs):
        flags.append(kwargs.get('cache_pickup_prefix', False))
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
    prefix = scorer.prepare_pickup_insertions(p, route, goods, 0)
    assert not calls
    for pos in (0, 1, 0, 3, 2):
        candidate = {**route, 'tasks': route['tasks'][:pos]+[('o1', 'p')]+route['tasks'][pos:]}
        assert prefix('o1', pos) == _route_score(p, candidate, goods, 0)


def test_unchanged_route_prefix_is_shared_between_pickup_requests_within_one_repair(monkeypatch):
    from models.common.domain.instance import ProblemInstance
    base = tiny_problem(3, 0)
    orders = [replace(base.orders[0], id=f'x{i}') for i in range(8)]
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=3, E1_Qk=100, E1_Ck=100,
                 E2_Qv=100, E2_Cv=100, container_weight=1, container_volume=1)
    nodes = base.network.nodes
    matrix = {a: {b: 0 for b in nodes} for a in nodes}
    p = MultiBatchInstance(ProblemInstance(nodes, fleet, distance_matrix=matrix,
        travel_time_matrix=matrix), orders,
        replace(base.operating_policy, container_pool_size=8, e2_max_trips_per_vehicle=12), model=3)
    state = dict(deliveries={'b1': [['T']]}, pickup_order={'b1': ['T', 'S']}, pickup_vehicle={'b1': {}},
        e2=[dict(satellite='T', vehicle=v, tasks=[(o.id, 'd') for o in row]+[(o.id, 'p') for o in row])
            for v, row in enumerate((orders[:3], orders[3:6], orders[6:]))])
    scorer = SingleBatchRouteScorer(p)
    factory = scorer.prepare_pickup_insertions
    queries = []
    def prepare(*args):
        prefix = factory(*args)
        seen = [];queries.append(seen)
        def score(oid, pos):
            seen.append(oid)
            return prefix(oid, pos)
        return score
    monkeypatch.setattr(scorer, 'prepare_pickup_insertions', prepare)
    removed = [orders[i] for i in (0, 3, 6)]
    a = repair_orders(p, state, removed, time.perf_counter()+5, scorer=scorer)
    b = repair_orders(p, state, removed, time.perf_counter()+5, scorer=scorer, cache_pickup_prefix=True)
    assert a == b and any(len(set(seen)) > 1 for seen in queries)
    queries.clear()
    c = repair_orders(p, state, removed, time.perf_counter()+5, scorer=scorer, cache_pickup_prefix=True, cache_scores=False)
    assert c == a and any(len(set(seen)) > 1 for seen in queries)
