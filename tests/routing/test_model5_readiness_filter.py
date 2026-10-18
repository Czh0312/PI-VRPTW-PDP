"""A trip removed by a move must not retain its old vehicle delay."""
from copy import deepcopy
from dataclasses import replace
import time

import pytest

from models.common.cycle import MultiBatchInstance, Order
from models.common.cycle.audit import audit_solution
from models.common.domain.instance import ProblemInstance
from models.common.domain.node import Customer, Depot, Satellite
from models.routing.model_5.alns.evaluator import evaluate
from models.routing.model_5.alns.accelerators.intensification import CycleDescent, task_distance
from models.routing.model_5.alns.neighborhoods import _goods_ready, _route_score, state_key


def sequential_trips():
    nodes = {'D': Depot('D', 0, 0), 'S': Satellite('S', 0, 0)}
    nodes.update({c: Customer(c, 0, 0, 0, 0, (0, 125), (0, 125), 'S')
                  for c in ('A', 'B')})
    distances = {a: {b: 0 if a == b else 1 for b in nodes} for a in nodes}
    for c in ('A', 'B'):
        distances['S'][c] = distances[c]['S'] = 10
    durations = {a: {b: int(a != b) for b in nodes} for a in nodes}
    policy = dict(policy_version='multi_batch_cutoff_v1', cycle_start=0, cycle_end=125,
        batches=[dict(id='b1', delivery_duration=30, return_duration=30),
                 dict(id='b2', delivery_duration=30, return_duration=30)],
        container_pool_size=2, cleaning_duration=5, time_tick_seconds=60,
        e2_max_trips_per_vehicle=4, e2_turnaround_time=1)
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=1,
        E1_Qk=20, E1_Ck=20, E2_Qv=10, E2_Cv=10,
        container_weight=1, container_volume=1)
    orders = [Order(f'{b}_{c}', c, 'S', b, 1, 1, (start, start+25),
                    (start+20, start+20) if c == 'A' else (start, start+25), 0, 2)
              for b, start in [('b1', 0), ('b2', 60)] for c in ('A', 'B')]
    problem = MultiBatchInstance(ProblemInstance(nodes, fleet, distance_matrix=distances,
        travel_time_matrix=durations), orders, policy, model=5)
    state = dict(deliveries={b: [['S']] for b in ('b1', 'b2')},
        pickup_order={b: ['S'] for b in ('b1', 'b2')},
        pickup_vehicle={b: {} for b in ('b1', 'b2')},
        e2=[dict(satellite='S', vehicle=0, tasks=[(f'{b}_{c}', k) for k in ('d', 'p')])
            for b in ('b1', 'b2') for c in ('A', 'B')])
    return problem, state


@pytest.mark.parametrize('cache_distances', [False, True])
def test_deleted_preceding_trip_does_not_screen_out_audited_improvement(cache_distances):
    problem, state = sequential_trips()
    original = deepcopy(state)
    score, decoded, issues = evaluate(problem, state)
    assert not issues and audit_solution(problem, decoded)['periodic_replay']['valid']
    merged = [('b1_B', 'd'), ('b1_A', 'd'), ('b1_B', 'p'), ('b1_A', 'p')]
    route = {**state['e2'][1], 'tasks': merged}
    goods = _goods_ready(problem, state)
    stale_ready = decoded['e2_trips'][0]['return_complete']+problem.operating_policy.e2_turnaround_time
    distance = task_distance(problem, 'S', merged)
    assert stale_ready == 22
    assert _route_score(problem, route, goods, stale_ready)[0] > distance
    assert _route_score(problem, route, goods, problem.operating_policy.cycle_start)[0] == distance

    complete = deepcopy(state)
    complete['e2'] = [route]+complete['e2'][2:]
    improved_score, improved, issues = evaluate(problem, complete)
    assert not issues and improved_score == score-17
    audit = audit_solution(problem, improved)
    assert audit['valid'] and audit['periodic_replay']['valid']

    descent = CycleDescent(problem, cache_distances=cache_distances)
    descent.key = state_key(state)
    descent.queue = [('e2', ((0, []), (1, merged)))]
    candidate = descent.propose(state, decoded, time.perf_counter()+1)
    assert candidate == complete
    assert state == original
    assert descent.propose(state, decoded, 0) is None


def test_explicit_departure_bound_still_rejects_late_merge():
    problem, state = sequential_trips()
    _, decoded, issues = evaluate(problem, state)
    assert not issues
    state['e2'][1]['depart_after'] = 22
    merged = [('b1_B', 'd'), ('b1_A', 'd'), ('b1_B', 'p'), ('b1_A', 'p')]
    descent = CycleDescent(problem)
    descent.key = state_key(state)
    descent.queue = [('e2', ((0, []), (1, merged)))]
    assert descent.propose(state, decoded, time.perf_counter()+1) is None
    assert state['e2'][1]['depart_after'] == 22
    assert descent.filtered == 1


def test_departure_hint_is_not_delayed_again_by_dispatch_preparation():
    problem, state = sequential_trips()
    problem.operating_policy = replace(problem.operating_policy, e2_dispatch_prep_time=5)
    state['e2'] = [dict(satellite='S', vehicle=0, depart_after=start+16,
        return_after=start+22,
        tasks=[(f'{b}_B', 'd'), (f'{b}_A', 'd'), (f'{b}_B', 'p'), (f'{b}_A', 'p')])
        for b, start in [('b1', 0), ('b2', 60)]]
    _, decoded, issues = evaluate(problem, state)
    assert not issues and audit_solution(problem, decoded)['periodic_replay']['valid']
    original = deepcopy(state)
    # A departure hint is already an absolute departure bound. Preparation
    # constrains supply/vehicle readiness, not this bound a second time.
    route = state['e2'][0]
    assert _route_score(problem, route, _goods_ready(problem, state), 0)[0] > task_distance(problem, 'S', route['tasks'])
    descent = CycleDescent(problem)
    descent.key = state_key(state)
    descent.queue = [('e2', ((0, route['tasks']),))]
    assert descent.propose(state, decoded, time.perf_counter()+1) == original
    assert state == original


def test_newly_feasible_ownership_move_waits_until_original_queue_is_exhausted():
    problem, state = sequential_trips()
    _, decoded, issues = evaluate(problem, state)
    assert not issues
    merged = [('b1_B', 'd'), ('b1_A', 'd'), ('b1_B', 'p'), ('b1_A', 'p')]
    descent = CycleDescent(problem)
    descent.key = state_key(state)
    descent.queue = [('pickup', ('b1', 'S', 0)), ('e2', ((0, []), (1, merged)))]
    first = descent.propose(state, decoded, time.perf_counter()+1)
    assert first['e2'] == state['e2']
    assert first['pickup_vehicle']['b1'] == {'S': 0}
    second = descent.propose(state, decoded, time.perf_counter()+1)
    assert len(second['e2']) == 3
    assert second['e2'][0]['tasks'] == merged
    assert audit_solution(problem, evaluate(problem, second)[1])['periodic_replay']['valid']
    assert descent.propose(state, decoded, time.perf_counter()+1) is None


def test_full_decoder_rejects_later_vehicle_conflict_passing_local_screen():
    problem, state = sequential_trips()
    # The first trip is locally feasible with this return wait, but the
    # unchanged following trip misses its customer window.
    state['e2'][0]['return_after'] = 25
    route = state['e2'][0]
    assert _route_score(problem, route, _goods_ready(problem, state), 0)[0] == task_distance(problem, 'S', route['tasks'])
    _, _, issues = evaluate(problem, state)
    assert 'E2_TIME_WINDOW' in issues
    descent = CycleDescent(problem)
    descent.key = state_key(state)
    descent.queue = [('e2', ((0, state['e2'][0]['tasks']),))]
    candidate = descent.propose(state, {'e2_trips': []}, time.perf_counter()+1)
    assert candidate is not None
    assert 'E2_TIME_WINDOW' in evaluate(problem, candidate)[2]
