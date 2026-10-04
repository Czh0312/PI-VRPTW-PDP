"""Regret repairs a capacity trap without bypassing full schedule audits."""
from copy import deepcopy
from dataclasses import replace
import random
import time

import pytest

from models.routing.common.cycle import MultiBatchInstance, Order
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Customer, Depot, Satellite
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.neighborhoods import _route_score, repair_orders, state_key
from models.routing.solvers.alns.single_batch_regret import SingleBatchRegret
from tests.routing.test_single_batch_solver_audit import tiny_problem


def capacity_trap(model):
    nodes = {'D': Depot('D', 0, 0), 'S': Satellite('S', 0, 0)}
    nodes.update({c: Customer(c, 0, 0, 0, 0, (0, 65), (0, 65), 'S') for c in ('A', 'B', 'C', 'E')})
    distance = {a: {b: 0 if a == b else 50 for b in nodes} for a in nodes}
    for a, b, value in [('D', 'S', 1), ('S', 'C', 10), ('S', 'E', 10),
                        ('C', 'A', 1), ('C', 'B', 1), ('E', 'A', 20), ('E', 'B', 100)]:
        distance[a][b] = distance[b][a] = value
    travel = {a: {b: int(a != b) for b in nodes} for a in nodes}
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=2, E1_Qk=20, E1_Ck=20,
                 E2_Qv=4, E2_Cv=4, container_weight=1, container_volume=1)
    orders = [Order(c, c, 'S', 'b1', 1, 1, (0, 25+(c == 'B')), (0, 28), 0, 2)
              for c in ('A', 'B', 'C', 'E')]
    policy = dict(policy_version='multi_batch_cutoff_v1', cycle_start=0, cycle_end=65,
        batches=[dict(id='b1', delivery_duration=30, return_duration=30)],
        container_pool_size=4, cleaning_duration=5, time_tick_seconds=60,
        e2_max_trips_per_vehicle=1, e2_turnaround_time=1)
    problem = MultiBatchInstance(ProblemInstance(nodes, fleet, distance_matrix=distance,
        travel_time_matrix=travel), orders, policy, model=model)
    state = dict(deliveries={'b1': [['S']]}, pickup_order={'b1': ['S']}, pickup_vehicle={'b1': {}},
        e2=[dict(satellite='S', vehicle=v, tasks=[(c, k) for k in problem.task_kinds for c in pair])
            for v, pair in enumerate([('C', 'A'), ('E', 'B')])])
    return problem, state


@pytest.mark.parametrize('model', [1, 3])
@pytest.mark.parametrize('cache_options', [False, True])
@pytest.mark.parametrize('screen', [False, True])
def test_regret_reserves_the_only_cheap_outbound_capacity_slot(model, cache_options, screen):
    problem, state = capacity_trap(model)
    saved = deepcopy(state)
    removed = [problem.order_map[c] for c in ('A', 'B')]
    greedy = repair_orders(problem, state, removed, time.perf_counter()+2)
    operator = SingleBatchRegret(problem, cache_options=cache_options, screen=screen)
    regret = operator.repair(state, removed, time.perf_counter()+2)
    assert greedy is not None and regret is not None and operator.reordered > 0
    scores = []
    for candidate in (state, greedy, regret):
        score, decoded, issues = evaluate(problem, candidate)
        assert not issues
        audited = audit_solution(problem, decoded)
        assert audited['valid'] and audited['periodic_replay']['valid']
        assert score == pytest.approx(audited['objective'])
        scores.append(score)
    assert scores[2] < scores[1]
    assert state == saved
    assert sorted(t for r in regret['e2'] for t in r['tasks']) == sorted(t for r in state['e2'] for t in r['tasks'])
    assert len(regret['e2']) == 2


@pytest.mark.parametrize('model', [1, 3])
@pytest.mark.parametrize('seed', [0, 1, 4, 7])
def test_screen_and_option_cache_match_full_raw_scoring_with_repeated_customers(model, seed):
    base = tiny_problem(model, seed)
    orders = [replace(o, id=f'{o.id}_{i}', delivery_window=(0, 60), pickup_window=(8, 64))
              for i in range(3) for o in base.orders]
    problem = MultiBatchInstance(base.network, orders,
        replace(base.operating_policy, container_pool_size=20, e2_max_trips_per_vehicle=12), model=model)
    from models.routing.solvers.alns.construction import chronological_state
    rng = random.Random(seed)
    state = chronological_state(problem, rng)
    removed = rng.sample(list(problem.orders), 4)
    saved = deepcopy(state)
    a = SingleBatchRegret(problem).repair(state, removed, time.perf_counter()+5)
    b = SingleBatchRegret(problem, scorer=_route_score, cache_options=False, screen=False).repair(
        state, removed, time.perf_counter()+5)
    assert a is not None and b is not None and state_key(a) == state_key(b)
    assert evaluate(problem, a) == evaluate(problem, b)
    assert state == saved
    assert sorted(t for r in a['e2'] for t in r['tasks']) == sorted(t for r in state['e2'] for t in r['tasks'])


def test_deadline_never_returns_partial_repair_or_mutates_original(monkeypatch):
    import models.routing.solvers.alns.single_batch_regret as module
    problem, state = capacity_trap(3)
    saved = deepcopy(state)
    clock = [0.0]
    def advancing_scorer(*args):
        clock[0] += 2
        return _route_score(*args)
    monkeypatch.setattr(module.time, 'perf_counter', lambda: clock[0])
    operator = SingleBatchRegret(problem, scorer=advancing_scorer)
    assert operator.repair(state, problem.orders[:2], 0) is None
    assert operator.repair(state, problem.orders[:2], 1) is None
    result = operator(state, random.Random(3), 1)
    assert state == saved and result == state and result is not state
    assert operator.completed == 0


@pytest.mark.parametrize('model', [1, 3])
def test_seed_reproducibility_and_result_does_not_alias_input(model):
    problem, state = capacity_trap(model)
    saved = deepcopy(state)
    a = SingleBatchRegret(problem)(state, random.Random(11), time.perf_counter()+2)
    b = SingleBatchRegret(problem)(state, random.Random(11), time.perf_counter()+2)
    assert state_key(a) == state_key(b)
    a['e2'][0]['tasks'].clear()
    a['deliveries']['b1'][0].clear()
    assert state == saved


def test_regret_operator_is_exercised_and_incumbent_independently_audited():
    from models.routing.solvers.alns.single_batch import solve_alns
    problem, _ = capacity_trap(3)
    a = solve_alns(problem, max_time=10, max_iterations=160, seed=9, _regret_repair=True)
    b = solve_alns(problem, max_time=10, max_iterations=160, seed=9, _regret_repair=True)
    assert a['info']['operator_attempts']['destroy_regret'] > 0 and a['info']['regret_completed'] > 0
    assert a['e1_trips'] == b['e1_trips'] and a['e2_trips'] == b['e2_trips']
    assert audit_solution(problem, a)['periodic_replay']['valid']
    assert a['info']['proven_optimal'] is False and a['info']['mip_gap'] is None
