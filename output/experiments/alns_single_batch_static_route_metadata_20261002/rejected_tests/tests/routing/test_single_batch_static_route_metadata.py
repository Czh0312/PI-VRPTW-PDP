"""Static route metadata retains ordered arithmetic and dynamic scoring inputs."""
from copy import deepcopy
from dataclasses import replace
import random
import struct

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.solvers.alns.neighborhoods import _route_score
from models.routing.solvers.alns.single_batch_scoring import SingleBatchRouteScorer
from tests.routing.test_single_batch_solver_audit import tiny_problem


def bits(score):
    return tuple(struct.pack('!d', value) for value in score)


@pytest.mark.parametrize('model', [1, 2, 3, 4])
@pytest.mark.parametrize('seed', range(5))
def test_cached_static_rows_match_reference_bitwise_under_dynamic_inputs(model, seed):
    rng = random.Random(seed)
    base = tiny_problem(model, seed)
    orders = [replace(o, id=f'{o.id}_{i}', count=rng.randint(1, 3),
                      goods_weight=rng.choice((.125, 1.1, 1e16)))
              for i in range(3) for o in base.orders]
    p = MultiBatchInstance(base.network, orders, base.operating_policy, model=model)
    enabled = SingleBatchRouteScorer(p, static_route_cache_size=3)
    disabled = SingleBatchRouteScorer(p, static_route_cache_size=0)
    pool = [(o.id, k) for o in orders for k in p.task_kinds]
    for _ in range(55):
        route = dict(satellite=rng.choice(p.satellites), vehicle=0,
                     tasks=rng.choices(pool, k=rng.randrange(12)))
        before = deepcopy(route)
        for repeat in range(3):
            route.update(satellite=rng.choice(p.satellites), depart_after=rng.randrange(120),
                         return_after=rng.randrange(120))
            goods = {(s, 'b1'): rng.randrange(120) for s in p.satellites}
            p.fleet.e2_weight_cap = rng.choice((.1, 4.3, 1e16))
            p.fleet.e2_volume_cap = rng.choice((.1, 3.7, 1e16))
            ready = rng.randrange(120)
            assert bits(enabled(p, route, goods, ready)) == bits(disabled(p, route, goods, ready)) == bits(_route_score(p, route, goods, ready))
            assert route['tasks'] == before['tasks']
        if model in (3, 4):
            assert enabled.route_metadata.cache_info().currsize <= 3
        else:
            assert enabled.route_metadata is None


@pytest.mark.parametrize('model', [3, 4])
def test_cache_is_order_sensitive_bounded_and_independent_of_route_mutations(model):
    p = tiny_problem(model, 1)
    scorer = SingleBatchRouteScorer(p, static_route_cache_size=2)
    reference = SingleBatchRouteScorer(p, static_route_cache_size=0)
    route = dict(satellite='S', tasks=[(o.id, 'd') for o in p.orders])
    goods = {(s, 'b1'): 0 for s in p.satellites}
    scorer(p, route, goods, 0)
    scorer(p, route, goods, 80)
    assert scorer.route_metadata.cache_info().hits == 1
    cached = scorer.route_metadata(tuple(route['tasks']))
    assert isinstance(cached[0], tuple)
    route['tasks'].reverse()
    assert bits(scorer(p, route, goods, 0)) == bits(reference(p, route, goods, 0))
    route['tasks'].append(('o1', 'p'))
    assert bits(scorer(p, route, goods, 0)) == bits(reference(p, route, goods, 0))
    assert scorer.route_metadata.cache_info().currsize == 2
    fresh = SingleBatchRouteScorer(p, static_route_cache_size=2)
    assert fresh.route_metadata.cache_info().currsize == 0
    assert fresh.route_metadata is not scorer.route_metadata
    with pytest.raises(ValueError, match='one problem'):
        scorer(tiny_problem(model, 1), route, goods, 0)
    with pytest.raises(KeyError):
        scorer(p, {**route, 'tasks': [('missing', 'd')]}, goods, 0)


@pytest.mark.parametrize('model', [1, 2, 3, 4])
def test_empty_routes_retain_original_ready_value_and_do_not_fill_cache(model):
    p = tiny_problem(model, 1)
    scorer = SingleBatchRouteScorer(p)
    assert scorer(p, dict(satellite='S', tasks=[]), {}, -0.0) == (0.0, -0.0)
    if scorer.route_metadata is not None:
        assert scorer.route_metadata.cache_info().currsize == 0
