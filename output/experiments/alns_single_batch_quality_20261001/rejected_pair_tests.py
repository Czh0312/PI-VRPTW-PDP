"""Whole-order relocation can separate its delivery and late pickup visits."""
from copy import deepcopy
from dataclasses import replace
import random

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.assignment import assigned_problem
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.alns.evaluator import evaluate, initial_state
from models.routing.solvers.alns.single_batch import _move
from tests.routing.test_single_batch_solver_audit import tiny_problem


class SeparatePairRng:
    def choice(self, items):
        return items[0]

    def random(self):
        return 1.0

    def randrange(self, *args):
        return 0 if len(args) == 1 else args[1]-1


@pytest.mark.parametrize('model', [3, 4])
def test_pair_can_avoid_blocking_other_deliveries_with_late_pickup(model):
    base = tiny_problem(model, 0)
    orders = [replace(o, satellite='S', delivery_window=(0, 45), pickup_window=(60, 80))
              for o in base.orders]
    p = MultiBatchInstance(base.network, orders,
        replace(base.operating_policy, e2_max_trips_per_vehicle=2), model=model)
    p = assigned_problem(p, {o.customer: 'S' for o in p.orders})
    state = initial_state(p, random.Random(0))
    a, b = orders
    state['e2'] = [dict(satellite='S', vehicle=0, tasks=[(o.id, 'd'), (o.id, 'p')])
                   for o in orders]
    saved = deepcopy(state)
    candidate = _move(p, state, SeparatePairRng(), 'pair')
    assert state == saved
    assert candidate['e2'][0]['tasks'] == [(a.id, 'd'), (b.id, 'd'), (b.id, 'p'), (a.id, 'p')]
    adjacent = deepcopy(candidate)
    adjacent['e2'][0]['tasks'] = [(a.id, 'd'), (a.id, 'p'), (b.id, 'd'), (b.id, 'p')]
    assert 'E2_TIME_WINDOW' in evaluate(p, adjacent)[2]
    _, result, issues = evaluate(p, candidate)
    assert not issues and audit_solution(p, result)['valid']


@pytest.mark.parametrize('seed', range(6))
def test_pair_relocation_preserves_coverage_ownership_and_own_precedence(seed):
    p = tiny_problem(3, 1)
    p = assigned_problem(p, {o.customer: 'S' for o in p.orders})
    rng = random.Random(seed)
    state = initial_state(p, rng)
    for _ in range(40):
        saved = deepcopy(state)
        moved = _move(p, state, rng, 'pair')
        assert state == saved
        assert sorted(t for r in moved['e2'] for t in r['tasks']) == sorted(
            (o.id, k) for o in p.orders for k in p.task_kinds)
        for route in moved['e2']:
            assert all(p.order_map[oid].satellite == route['satellite'] for oid, _ in route['tasks'])
            positions = {task: i for i, task in enumerate(route['tasks'])}
            for oid, kind in route['tasks']:
                if kind == 'p' and (oid, 'd') in positions:
                    assert positions[oid, 'd'] < positions[oid, 'p']
        state = moved
