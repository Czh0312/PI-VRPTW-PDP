"""Regret must preserve complete requests and reduce an audited greedy trap."""
from copy import deepcopy
import random
import time

import pytest

from models.routing.common.cycle import MultiBatchInstance, Order
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Customer, Depot, Satellite
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.multibatch_repair import RegretRepair
from models.routing.solvers.alns.neighborhoods import destroy_repair, repair_orders, state_key


def constrained_routes():
    nodes = {'D': Depot('D', 0, 0), 'S': Satellite('S', 0, 0)}
    nodes.update({c: Customer(c, 0, 0, 0, 0, (0, 125), (0, 125), 'S')
                  for c in ('A', 'B', 'C', 'E')})
    distance = {a: {b: 0 if a == b else 50 for b in nodes} for a in nodes}
    for a, b, value in [('D','S',1), ('S','C',10), ('S','E',10),
                        ('C','A',1), ('C','B',1), ('E','A',20), ('E','B',100)]:
        distance[a][b] = distance[b][a] = value
    travel = {a: {b: int(a != b) for b in nodes} for a in nodes}
    policy = dict(policy_version='multi_batch_cutoff_v1', cycle_start=0, cycle_end=125,
        batches=[dict(id='b1', delivery_duration=30, return_duration=30),
                 dict(id='b2', delivery_duration=30, return_duration=30)],
        container_pool_size=8, cleaning_duration=5, time_tick_seconds=60,
        e2_max_trips_per_vehicle=2, e2_turnaround_time=1)
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=2,
        E1_Qk=20, E1_Ck=20, E2_Qv=4, E2_Cv=4,
        container_weight=1, container_volume=1)
    orders = [Order(f'{b}_{c}', c, 'S', b, 1, 1,
                    (start, start+25+(c=='B')), (start, start+28), 0, 2)
              for b, start in [('b1', 0), ('b2', 60)] for c in ('A','B','C','E')]
    problem = MultiBatchInstance(ProblemInstance(nodes, fleet, distance_matrix=distance,
        travel_time_matrix=travel), orders, policy, model=5)
    state = dict(deliveries={b: [['S']] for b in ('b1','b2')},
        pickup_order={b: ['S'] for b in ('b1','b2')},
        pickup_vehicle={b: {} for b in ('b1','b2')},
        e2=[dict(satellite='S', vehicle=v,
                 tasks=[(f'{b}_{c}', k) for k in ('d','p') for c in pair])
            for b in ('b1','b2') for v, pair in enumerate([('C','A'),('E','B')])])
    return problem, state


@pytest.mark.parametrize('cache_options', [False, True])
@pytest.mark.parametrize('regret_pickups', [False, True])
def test_regret_saves_the_only_cheap_capacity_slot_for_the_constrained_order(cache_options, regret_pickups):
    problem, state = constrained_routes()
    original = deepcopy(state)
    removed = [problem.order_map['b1_A'], problem.order_map['b1_B']]
    greedy = repair_orders(problem, state, removed, time.perf_counter()+2)
    repair = RegretRepair(problem, cache_options=cache_options, regret_pickups=regret_pickups)
    candidate = repair.repair(state, removed, time.perf_counter()+2)
    assert greedy is not None and candidate is not None and repair.reordered > 0
    outcomes = []
    for proposal in (state, greedy, candidate):
        value, decoded, issues = evaluate(problem, proposal)
        assert not issues
        audit = audit_solution(problem, decoded)
        assert audit['valid'] and audit['periodic_replay']['valid']
        assert value == pytest.approx(audit['objective'])
        outcomes.append(value)
    assert outcomes[2] < outcomes[1]
    assert state == original
    assert sorted(t for r in candidate['e2'] for t in r['tasks']) == sorted(
        t for r in state['e2'] for t in r['tasks'])
    assert len(candidate['e2']) == len(state['e2'])


def test_deadline_never_returns_or_mutates_a_partial_schedule():
    problem, state = constrained_routes()
    original = state_key(state)
    assert RegretRepair(problem).repair(state, problem.orders[:2], 0) is None
    assert state_key(state) == original
    repair = RegretRepair(problem)
    result = repair(problem, state, random.Random(0), 'related', 0)
    assert state_key(result) == original and result is not state
    assert repair.interrupted == 1 and repair.completed == 0


def test_random_greedy_operator_keeps_its_previous_choices_and_rng_state():
    problem, state = constrained_routes()
    a, b = random.Random(17), random.Random(17)
    expected = destroy_repair(problem, state, a, 'random', time.perf_counter()+2)
    repair = RegretRepair(problem)
    actual = repair(problem, state, b, 'random', time.perf_counter()+2)
    assert state_key(actual) == state_key(expected) and a.getstate() == b.getstate()
    assert repair.calls == repair.completed == 0


def test_same_seed_repair_is_reproducible_and_does_not_alias_original():
    problem, state = constrained_routes()
    repair = RegretRepair(problem)
    a = repair(problem, state, random.Random(23), 'related', time.perf_counter()+2)
    b = repair(problem, state, random.Random(23), 'related', time.perf_counter()+2)
    assert state_key(a) == state_key(b)
    original = state_key(state)
    a['e2'][0]['tasks'].clear()
    assert state_key(state) == original


@pytest.mark.parametrize('seed', [0, 1, 7, 23])
@pytest.mark.parametrize('regret_pickups', [False, True])
def test_route_option_cache_preserves_choices_after_prefix_and_index_changes(seed, regret_pickups):
    problem, state = constrained_routes()
    rng = random.Random(seed)
    removed = rng.sample(problem.orders, 4)
    a = RegretRepair(problem, cache_options=False, regret_pickups=regret_pickups).repair(state, removed, float('inf'))
    b = RegretRepair(problem, cache_options=True, regret_pickups=regret_pickups).repair(state, removed, float('inf'))
    assert a is not None and b is not None
    assert state_key(a) == state_key(b)
    assert evaluate(problem, a) == evaluate(problem, b)
