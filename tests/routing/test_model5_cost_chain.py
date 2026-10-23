"""Cost-aware repair must preserve economics, physical replay and hard limits."""
from collections import Counter
from copy import deepcopy
import time

import pytest

from models.common.cycle import Order
from models.common.cycle.audit import audit_solution
from models.common.cycle.fixed_pool import FixedBatchPoolInstance
from models.common.domain.instance import ProblemInstance
from models.common.domain.node import Customer, Depot, Satellite
from models.routing.model_5.alns.cost_chain import CostAwareStockRepair, _Node, _select_frontier
from models.routing.model_5.alns.evaluator import evaluate
from models.routing.model_5.alns.solver import solve_alns
from tests.routing.test_model5_stock_chain import plateau_fixture
from tools.configuration.loader import load_json


def consolidation_fixture():
    nodes = {'DEPOT': Depot('DEPOT', 0, 0), **{s: Satellite(s, 0, 0) for s in ('S', 'T')}}
    nodes.update({c: Customer(c, 0, 0, 0, 0, (0, 125), (0, 125), s)
                  for c, s in zip('ABCD', ('S', 'T', 'S', 'T'))})
    distance = {a: {b: int(a != b) for b in nodes} for a in nodes}
    travel = deepcopy(distance)
    distance['S']['T'], distance['T']['S'] = 0.1, 0.2
    fleet = dict(E1_vehicle_count=2, vehicles_per_satellite=1, E1_Qk=20, E1_Ck=20,
                 E2_Qv=20, E2_Cv=20, container_weight=1, container_volume=1)
    policy = dict(policy_version='multi_batch_cutoff_v1', cycle_start=0, cycle_end=125,
        time_tick_seconds=60, cleaning_duration=5, container_pool_size=4,
        e2_max_trips_per_vehicle=2,
        batches=[dict(id=f'b{i}', delivery_duration=30, return_duration=30) for i in (1, 2)])
    orders = [Order(c, c, s, f'b{i // 2 + 1}', 1, 1, (i // 2 * 60, i // 2 * 60 + 25),
        (i // 2 * 60, i // 2 * 60 + 29), 0, 1)
        for i, (c, s) in enumerate(zip('ABCD', ('S', 'T', 'S', 'T')))]
    p = FixedBatchPoolInstance(ProblemInstance(nodes, fleet, distance_matrix=distance,
        travel_time_matrix=travel), orders, policy, {},
        load_json('config/experiments/benchmark27_v1/economic.json'))
    state = dict(e2=[dict(satellite=o.satellite, vehicle=0, tasks=[(o.id, 'd'), (o.id, 'p')])
                    for o in orders], deliveries={b.id: [['S'], ['T']] for b in p.operating_policy.batches},
        pickup_order={b.id: ['S', 'T'] for b in p.operating_policy.batches},
        pickup_vehicle={b.id: {'S': 0, 'T': 1} for b in p.operating_policy.batches})
    return p, state


def test_frontier_keeps_cheap_larger_pool_and_stock_progress():
    def node(req, nonpool):
        pool_cost = 5 * max(req)
        return _Node({}, dict(info=dict(objective=nonpool + pool_cost),
            cost_components=dict(container_pool=pool_cost)), req, [])
    cheap = node((5, 7, 7, 7), 100)
    small = node((5, 5, 5, 5), 125)
    expensive = node((5, 7, 7, 7), 105)
    selected = _select_frontier([small, expensive, cheap], target=6, width=2)
    assert selected == [cheap, small]


def test_real_cost_barrier_still_crossed_and_only_cost_improvement_returned():
    p, state = plateau_fixture()
    original = deepcopy(state)
    score, result, _ = evaluate(p, state)
    repair = CostAwareStockRepair(p, lower_bound=2)
    proposed = repair.propose(state, result, lambda s: evaluate(p, s), time.perf_counter() + 2)
    value, decoded, issues = evaluate(p, proposed)
    assert not issues and value < score
    assert audit_solution(p, decoded)['periodic_replay']['valid']
    assert repair.last_run['best_path'][0]['cost'] > score
    assert state == original
    assert Counter(t for r in state['e2'] for t in r['tasks']) == Counter(
        t for r in proposed['e2'] for t in r['tasks'])
    shallow = CostAwareStockRepair(p, lower_bound=2, max_depth=1)
    assert shallow.propose(state, result, lambda s: evaluate(p, s), time.perf_counter() + 2) is None


def test_consolidation_at_pool_lower_bound_remains_available_and_auditable():
    p, state = consolidation_fixture()
    saved = deepcopy(state)
    cost, decoded, issues = evaluate(p, state)
    assert not issues and decoded['container_pool_size'] == 2
    repair = CostAwareStockRepair(p, lower_bound=2)
    proposed = repair.propose(state, decoded, lambda s: evaluate(p, s), time.perf_counter() + 2)
    value, result, issues = evaluate(p, proposed)
    assert not issues and value < cost
    assert result['container_pool_size'] == 2
    assert len(result['e1_trips']) < len(decoded['e1_trips'])
    assert audit_solution(p, result)['periodic_replay']['valid']
    assert state == saved


def test_evaluation_deadline_and_repeated_root_limits():
    p, state = plateau_fixture()
    _, decoded, _ = evaluate(p, state)
    repair = CostAwareStockRepair(p, lower_bound=2, max_evaluations=1)
    assert repair.propose(state, decoded, lambda s: evaluate(p, s), 0) is None
    assert repair.calls == 0
    repair.propose(state, decoded, lambda s: evaluate(p, s), time.perf_counter() + 2)
    assert repair.evaluations == 1
    assert repair.propose(state, decoded, lambda s: evaluate(p, s), time.perf_counter() + 2) is None
    assert repair.calls == 1


def test_public_strategy_is_explicit_and_result_passes_independent_replay():
    p, _ = consolidation_fixture()
    result = solve_alns(p, max_time=2, max_iterations=100, seed=42,
                       critical_stock_repair=True, stock_chain_strategy='cost_aware')
    assert result['info']['stock_chain_strategy'] == 'cost_aware'
    assert audit_solution(p, result)['periodic_replay']['valid']
    with pytest.raises(ValueError, match='requires'):
        solve_alns(p, stock_chain_strategy='cost_aware')
    with pytest.raises(ValueError, match='Unknown'):
        solve_alns(p, critical_stock_repair=True, stock_chain_strategy='typo')
