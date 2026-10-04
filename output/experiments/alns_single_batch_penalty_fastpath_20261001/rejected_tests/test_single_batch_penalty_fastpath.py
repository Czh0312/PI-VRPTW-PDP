"""Penalty arithmetic agrees exactly at float capacity and time boundaries."""
from copy import deepcopy
from dataclasses import replace
import math

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.solvers.alns.neighborhoods import _route_score
from models.routing.solvers.alns.single_batch_scoring import SingleBatchRouteScorer
from tests.routing.test_single_batch_solver_audit import tiny_problem


@pytest.mark.parametrize('model', [1, 3])
@pytest.mark.parametrize('capacity', [0.1, 0.5, 1.0, 3.7,
    math.nextafter(3.7, -math.inf), math.nextafter(3.7, math.inf), 1e16])
def test_exact_scores_with_zero_and_positive_penalties_at_float_boundaries(model, capacity):
    base = tiny_problem(model, 1)
    orders = [replace(o, goods_weight=w) for o, w in zip(base.orders, (0.0, 3.6, 1e16))]
    p = MultiBatchInstance(base.network, orders, base.operating_policy, model=model)
    p.fleet.container_weight = .1
    p.fleet.container_volume = .125
    p.fleet.e2_weight_cap = capacity
    p.fleet.e2_volume_cap = .375
    scorer = SingleBatchRouteScorer(p)
    tasks = [(o.id, kind) for o in orders for kind in p.task_kinds]
    for selected in ([], tasks[:1], tasks[:2], tasks, list(reversed(tasks)), tasks+tasks):
        for ready in (0, 60, 95, 100):
            for returned in (0, p.operating_policy.batches[0].cutoff,
                             p.operating_policy.cycle_end-p.operating_policy.e2_turnaround_time,
                             p.operating_policy.cycle_end+1):
                goods = {(s, 'b1'): ready for s in p.satellites}
                route = dict(satellite='S', vehicle=0, tasks=selected, return_after=returned)
                before = deepcopy(route)
                assert scorer(p, route, goods, ready) == _route_score(p, route, goods, ready)
                assert route == before


@pytest.mark.parametrize('model', [1, 3])
def test_zero_distance_zero_penalties_remain_exactly_zero(model):
    p = tiny_problem(model, 0)
    scorer = SingleBatchRouteScorer(p)
    route = dict(satellite='S', vehicle=0, tasks=[(p.orders[1].id, k) for k in p.task_kinds])
    goods = {(s, 'b1'): 0 for s in p.satellites}
    actual = scorer(p, route, goods, 0)
    assert actual == _route_score(p, route, goods, 0)
    assert actual[0] == 0.0
