"""General scoring comparisons retain ordered max/sum arithmetic bitwise."""
from dataclasses import replace
from copy import deepcopy
import random
import struct

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.solvers.alns.neighborhoods import _route_score
from models.routing.solvers.alns.single_batch_scoring import SingleBatchRouteScorer
from tests.routing.test_single_batch_solver_audit import tiny_problem


def bits(values):
    return tuple(struct.pack('!d', value) for value in values)


@pytest.mark.parametrize('model', [1, 2, 3, 4])
@pytest.mark.parametrize('seed', range(6))
def test_general_comparisons_match_original_with_dynamic_inputs(model, seed):
    rng = random.Random(seed)
    base = tiny_problem(model, seed)
    orders = [replace(o, id=f'{o.id}_{i}', count=rng.randint(1, 3),
                      goods_weight=rng.choice((.125, 1.1, 1e16)))
              for i in range(3) for o in base.orders]
    p = MultiBatchInstance(base.network, orders, base.operating_policy, model=model)
    enabled = SingleBatchRouteScorer(p)
    original = SingleBatchRouteScorer(p, branch_route_score=False)
    assert enabled.branch_route_score == (model in (3, 4))
    pool = [(o.id, k) for o in orders for k in p.task_kinds]
    for _ in range(65):
        route = dict(satellite=rng.choice(p.satellites), vehicle=0,
                     tasks=rng.choices(pool, k=rng.randrange(14)),
                     depart_after=rng.randrange(130), return_after=rng.randrange(130))
        goods = {(s, 'b1'): rng.randrange(130) for s in p.satellites}
        p.fleet.e2_weight_cap = rng.choice((.1, 1.0, 4.3, 1e16))
        p.fleet.e2_volume_cap = rng.choice((.1, 1.0, 3.7, 1e16))
        ready = rng.randrange(130)
        before = deepcopy(route)
        assert bits(enabled(p, route, goods, ready)) == bits(original(p, route, goods, ready)) == bits(_route_score(p, route, goods, ready))
        assert route == before


@pytest.mark.parametrize('boundary', [-0.0, 0.0, 1.0, float('nan'), float('inf')])
def test_ties_and_nonfinite_max_operands_keep_original_argument_direction(boundary):
    p = tiny_problem(3, 1)
    enabled = SingleBatchRouteScorer(p)
    original = SingleBatchRouteScorer(p, branch_route_score=False)
    route = dict(satellite='S', tasks=[('o1', 'd'), ('o1', 'p')], return_after=boundary)
    goods = {(s, 'b1'): boundary for s in p.satellites}
    p.fleet.e2_weight_cap, p.fleet.e2_volume_cap = boundary, 1.0
    assert bits(enabled(p, route, goods, boundary)) == bits(original(p, route, goods, boundary))
    with pytest.raises(ValueError, match='one problem'):
        enabled(tiny_problem(3, 1), route, goods, 0)
