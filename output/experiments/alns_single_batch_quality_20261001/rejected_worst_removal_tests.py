"""Distance-saving removal ranks whole orders, including repeated visits."""
from copy import deepcopy
import random
import time

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.assignment import assigned_problem
from models.routing.solvers.alns import neighborhoods
from models.routing.solvers.alns.evaluator import initial_state
from tests.routing.test_single_batch_solver_audit import tiny_problem


def test_worst_removal_uses_whole_order_savings_on_directed_nonmetric_arcs(monkeypatch):
    base = tiny_problem(3, 1)
    p = MultiBatchInstance(base.network, [base.orders[i] for i in (2, 0, 1)],
                           base.operating_policy, model=3)
    p = assigned_problem(p, {o.customer: 'S' for o in p.orders})
    for a in p.network.nodes:
        for b in p.network.nodes:
            p.network.dist[p.network.node_index[a]][p.network.node_index[b]] = 0 if a == b else 1
    p.network.dist[p.network.node_index['C0']][p.network.node_index['C1']] = 50
    p.network.dist[p.network.node_index['C1']][p.network.node_index['C0']] = 70
    state = initial_state(p, random.Random(0))
    state['e2'] = [dict(satellite='S', vehicle=0,
        tasks=[(f'o{i}', k) for i in range(3) for k in ('d', 'p')])]
    selected = []
    def repair(problem, original, removed, deadline, **kwargs):
        selected.extend(o.id for o in removed)
        return neighborhoods.clone_state(original)
    monkeypatch.setattr(neighborhoods, 'repair_orders', repair)
    class TopRng:
        def random(self):
            return 0.0
    saved = deepcopy(state)
    neighborhoods.destroy_repair(p, state, TopRng(), 'worst', time.perf_counter()+5)
    # Removing just one of two consecutive visits has zero saving. Removing
    # the complete order saves 50 for o0/o1 and only 1 for the first-listed o2.
    assert selected == ['o0', 'o1'] and state == saved


@pytest.mark.parametrize('model', [1, 3])
@pytest.mark.parametrize('seed', range(6))
def test_worst_removal_repairs_complete_tasks_without_mutating_input(model, seed):
    p = tiny_problem(model, seed)
    state = initial_state(p, random.Random(seed))
    saved = deepcopy(state)
    candidate = neighborhoods.destroy_repair(p, state, random.Random(42), 'worst', time.perf_counter()+5)
    assert state == saved
    assert sorted(t for r in candidate['e2'] for t in r['tasks']) == sorted(
        (o.id, k) for o in p.orders for k in p.task_kinds)
    assert all(p.order_map[o].satellite == r['satellite'] for r in candidate['e2'] for o, _ in r['tasks'])
    expired = neighborhoods.destroy_repair(p, state, random.Random(42), 'worst', 0)
    assert expired == saved and expired is not state
