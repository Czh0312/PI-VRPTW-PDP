"""Joint stock repair crosses a real cost barrier without weakening the audit."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
import time

import pytest

from models.common.cycle import Order
from models.common.cycle.audit import audit_solution
from models.common.cycle.fixed_pool import FixedBatchPoolInstance
from models.common.domain.instance import ProblemInstance
from models.common.domain.node import Customer, Depot, Satellite
from models.routing.model_5.alns.evaluator import evaluate
from models.routing.model_5.alns.performance.cycle_cache import CycleEvaluator
from models.routing.model_5.alns.stock_chain import CriticalStockRepair, stock_requirements
from models.routing.model_5.alns.solver import solve_alns
from tools.configuration.loader import load_json


def plateau_fixture():
    nodes = {'DEPOT': Depot('DEPOT', 0, 0), 'S': Satellite('S', 0, 0)}
    nodes.update({c: Customer(c, 0, 0, 0, 0, (0, 185), (0, 185), 'S') for c in 'ABCDEFGHI'})
    distance = {a: {b: int(a != b) for b in nodes} for a in nodes}
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=1, E1_Qk=20, E1_Ck=20,
                 E2_Qv=20, E2_Cv=20, container_weight=1, container_volume=1)
    policy = dict(policy_version='multi_batch_cutoff_v1', cycle_start=0, cycle_end=185,
        time_tick_seconds=60, cleaning_duration=5, container_pool_size=9,
        e2_max_trips_per_vehicle=8,
        batches=[dict(id=f'b{i}', delivery_duration=30, return_duration=30) for i in (1, 2, 3)])
    orders, routes = [], []
    for index, pair in enumerate(('AB', 'CD', 'EF')):
        start, batch = index * 60, f'b{index + 1}'
        # A separate early return keeps every batch productive while the two
        # mixed early/late trips still create simultaneous stock bottlenecks.
        anchor = 'GHI'[index]
        orders.append(Order(anchor, anchor, 'S', batch, 1, 1,
                            (start, start + 25), (start, start + 29), 0, 1))
        routes.append(dict(satellite='S', vehicle=0, tasks=[(anchor, 'd'), (anchor, 'p')]))
        for j, customer in enumerate(pair):
            late = j == 1 and index < 2
            orders.append(Order(customer, customer, 'S', batch, 1, 1,
                (start, start + 25), (start + (35 if late else 0), start + (50 if late else 29)), 0, 1))
        routes.append(dict(satellite='S', vehicle=0, tasks=[(o, k) for o in pair for k in ('d', 'p')]))
    p = FixedBatchPoolInstance(ProblemInstance(nodes, fleet, distance_matrix=distance,
        travel_time_matrix=distance), orders, policy, {},
        load_json('config/experiments/benchmark27_v1/economic.json'))
    state = dict(e2=routes, deliveries={b.id: [['S']] for b in p.operating_policy.batches},
        pickup_order={b.id: ['S'] for b in p.operating_policy.batches},
        pickup_vehicle={b.id: {} for b in p.operating_policy.batches})
    return p, state


def test_joint_repair_crosses_two_stock_peaks_and_a_cost_barrier():
    p, state = plateau_fixture()
    saved = deepcopy(state)
    score, decoded, issues = evaluate(p, state)
    assert not issues and audit_solution(p, decoded)['periodic_replay']['valid']
    assert decoded['container_pool_size'] == 5
    assert stock_requirements(p, decoded) == (3, 5, 5)
    cached = CycleEvaluator(p)
    repair = CriticalStockRepair(p, lower_bound=2)
    proposed = repair.propose(state, decoded, lambda s: cached(p, s), time.perf_counter() + 3)
    assert proposed is not None
    new_score, result, issues = evaluate(p, proposed)
    assert not issues and audit_solution(p, result)['periodic_replay']['valid']
    assert result['container_pool_size'] == 4 and new_score < score
    path = repair.last_run['best_path']
    assert len(path) >= 2
    assert path[0]['pool'] == 5 and path[0]['cost'] > score
    assert Counter(t for r in state['e2'] for t in r['tasks']) == Counter(
        t for r in proposed['e2'] for t in r['tasks'])
    assert state == saved
    proposed['e2'][0]['tasks'].clear()
    assert state == saved


def test_no_return_of_more_expensive_intermediate_or_revisit_of_root():
    p, state = plateau_fixture()
    _, decoded, _ = evaluate(p, state)
    repair = CriticalStockRepair(p, lower_bound=2, max_depth=1)
    assert repair.propose(state, decoded, lambda s: evaluate(p, s), time.perf_counter() + 2) is None
    assert repair.last_run['lowest_pool'] == 5
    calls = repair.calls
    assert repair.propose(state, decoded, lambda s: evaluate(p, s), time.perf_counter() + 2) is None
    assert repair.calls == calls


def test_deadline_evaluation_cap_and_pool_lower_bound_are_honored():
    p, state = plateau_fixture()
    _, decoded, _ = evaluate(p, state)
    repair = CriticalStockRepair(p, lower_bound=2, max_evaluations=1)
    assert repair.propose(state, decoded, lambda s: evaluate(p, s), 0) is None
    assert repair.calls == 0
    repair.propose(state, decoded, lambda s: evaluate(p, s), time.perf_counter() + 2)
    assert repair.evaluations == 1
    at_bound = CriticalStockRepair(p, lower_bound=decoded['container_pool_size'])
    assert at_bound.propose(state, decoded, lambda s: evaluate(p, s), time.perf_counter() + 2) is None
    assert at_bound.evaluations == 0


def test_trip_limit_prevents_invalid_pair_creation_and_coverage_is_preserved():
    p, state = plateau_fixture()
    p.operating_policy = replace(p.operating_policy, e2_max_trips_per_vehicle=6)
    _, decoded, issues = evaluate(p, state)
    assert not issues
    repair = CriticalStockRepair(p, lower_bound=2)
    assert repair.propose(state, decoded, lambda s: evaluate(p, s), time.perf_counter() + 2) is None


def test_cleaning_boundary_uses_equality_and_actual_e1_return():
    p, state = plateau_fixture()
    _, decoded, _ = evaluate(p, state)
    # One synthetic release before b2: this helper only counts actual E1
    # releases; the complete decoder/auditor remains the feasibility authority.
    fake = deepcopy(decoded)
    fake['e1_trips'] = [dict(return_complete=55, events=[dict(kind='p', quantity=1)])]
    assert stock_requirements(p, fake) == (3, 5, 8)
    fake['e1_trips'][0]['return_complete'] = 56
    assert stock_requirements(p, fake) == (3, 6, 8)


def test_public_switch_outputs_audited_best_and_bounded_diagnostics():
    p, _ = plateau_fixture()
    result = solve_alns(p, max_time=2, max_iterations=100, seed=42, critical_stock_repair=True)
    assert audit_solution(p, result)['periodic_replay']['valid']
    info = result['info']
    assert info['stock_chain_enabled'] is True
    assert info['objective'] <= info['initial_objective']
    assert info['stock_chain_evaluations'] <= 192 * info['stock_chain_calls']
    with pytest.raises(ValueError, match='limits'):
        CriticalStockRepair(p, lower_bound=2, max_evaluations=0)
