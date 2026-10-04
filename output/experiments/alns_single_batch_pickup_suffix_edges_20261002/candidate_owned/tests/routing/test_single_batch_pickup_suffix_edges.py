"""Static suffix legs preserve pickup scores and lazy prefix state."""
from copy import deepcopy
from dataclasses import replace
import random
import struct

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.solvers.alns.single_batch_scoring import SingleBatchRouteScorer
from tests.routing.test_single_batch_solver_audit import tiny_problem


@pytest.mark.parametrize('seed', range(8))
def test_branch_and_reference_match_bitwise_with_shuffled_suffix_requests(seed):
    rng = random.Random(seed)
    base = tiny_problem(3, seed)
    orders = [replace(o, id=f'{o.id}_{i}', count=rng.randint(1, 3),
                      goods_weight=rng.choice((.125, 1.1, 1e16)))
              for i in range(3) for o in base.orders]
    p = MultiBatchInstance(base.network, orders, base.operating_policy, model=3)
    p.fleet.e2_weight_cap, p.fleet.e2_volume_cap = 4.3, .1
    branch = SingleBatchRouteScorer(p)
    reference = SingleBatchRouteScorer(p, cache_pickup_edges=False)
    pool = [(o.id, k) for o in orders for k in p.task_kinds]
    for _ in range(40):
        route = dict(satellite=rng.choice(p.satellites), vehicle=0,
                     tasks=rng.choices(pool, k=rng.randrange(12)),
                     depart_after=rng.randrange(120), return_after=rng.randrange(120))
        goods = {(s, 'b1'): rng.randrange(110) for s in p.satellites}
        ready = rng.randrange(120)
        before = deepcopy(route)
        a = branch.prepare_pickup_insertions(p, route, goods, ready)
        b = reference.prepare_pickup_insertions(p, route, goods, ready)
        positions = list(range(len(route['tasks'])+1))
        rng.shuffle(positions)
        for pos in positions+positions[::-1]:
            oid = rng.choice(orders).id
            x, y = a(oid, pos), b(oid, pos)
            assert tuple(struct.pack('!d', v) for v in x) == tuple(struct.pack('!d', v) for v in y)
            assert a.prefixes == b.prefixes
        assert route == before


@pytest.mark.parametrize('return_after', [-0.0, 0.0, float('nan'), float('inf')])
def test_return_bound_preserves_max_argument_order_and_nonfinite_scores(return_after):
    p = tiny_problem(3, 1)
    route = dict(satellite='S', tasks=[('o1', 'd')], return_after=return_after)
    goods = {(s, 'b1'): 0 for s in p.satellites}
    a = SingleBatchRouteScorer(p).prepare_pickup_insertions(p, route, goods, 0)
    b = SingleBatchRouteScorer(p, cache_pickup_edges=False).prepare_pickup_insertions(p, route, goods, 0)
    for pos in (0, 1, 0):
        assert tuple(struct.pack('!d', v) for v in a('o1', pos)) == tuple(struct.pack('!d', v) for v in b('o1', pos))


def test_edge_tape_is_lazy_route_local_and_skips_unchanged_leg_calls_on_repeat():
    p = tiny_problem(3, 1)
    scorer = SingleBatchRouteScorer(p)
    calls = []
    for name in ('distance', 'travel'):
        original = getattr(scorer, name)
        def wrapped(a, b, original=original, name=name):
            calls.append((name, a, b))
            return original(a, b)
        setattr(scorer, name, wrapped)
    route = dict(satellite='S', tasks=[(o.id, k) for k in ('d', 'p') for o in p.orders])
    goods = {(s, 'b1'): 0 for s in p.satellites}
    prepared = scorer.prepare_pickup_insertions(p, route, goods, 0)
    assert not calls and prepared.edge_tasks is None
    first = prepared('o1', 0)
    tape = prepared.edge_tasks
    assert len(tape) == len(route['tasks'])
    calls.clear()
    assert prepared('o1', 0) == first
    assert len(calls) == 6 and prepared.edge_tasks is tape
    other = scorer.prepare_pickup_insertions(p, route, goods, 80)
    assert other.edge_tasks is None and other.prefixes is not prepared.prefixes
    other('o2', 0)
    assert other.edge_tasks is not tape


def test_end_insertion_does_not_prepare_an_unused_static_suffix():
    p = tiny_problem(3, 1)
    scorer = SingleBatchRouteScorer(p)
    route = dict(satellite='S', tasks=[(o.id, 'd') for o in p.orders])
    goods = {(s, 'b1'): 0 for s in p.satellites}
    prepared = scorer.prepare_pickup_insertions(p, route, goods, 0)
    prepared('o1', len(route['tasks']))
    assert prepared.edge_tasks is None
    prepared('o1', 0)
    assert len(prepared.edge_tasks) == len(route['tasks'])
