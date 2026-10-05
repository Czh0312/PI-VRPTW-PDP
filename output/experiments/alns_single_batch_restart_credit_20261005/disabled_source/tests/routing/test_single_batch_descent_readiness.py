"""A removed trip must not retain its old delay in the B1 descent screen."""
from copy import deepcopy
from dataclasses import replace
import time

import pytest

from models.routing.common.cycle import MultiBatchInstance, Order
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Customer, Depot, Satellite
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.single_batch_descent import SingleBatchDescent, task_distance
from models.routing.solvers.alns.single_batch_scoring import SingleBatchRouteScorer
from models.routing.solvers.alns.neighborhoods import _goods_ready, _route_score, state_key


def sequential_trips(model):
    nodes = {'D': Depot('D', 0, 0), 'S': Satellite('S', 0, 0)}
    nodes.update({c: Customer(c, 0, 0, 0, 0, (0, 65), (0, 65), 'S') for c in ('A', 'B')})
    distances = {a: {b: 0 if a==b else 1 for b in nodes} for a in nodes}
    for c in ('A', 'B'):
        distances['S'][c] = distances[c]['S'] = 10
    durations = {a: {b: int(a!=b) for b in nodes} for a in nodes}
    policy = dict(policy_version='multi_batch_cutoff_v1', cycle_start=0, cycle_end=65,
        batches=[dict(id='b1', delivery_duration=30, return_duration=30)],
        container_pool_size=2, cleaning_duration=5, time_tick_seconds=60,
        e2_max_trips_per_vehicle=4, e2_turnaround_time=1)
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=1,
        E1_Qk=20, E1_Ck=20, E2_Qv=10, E2_Cv=10, container_weight=1, container_volume=1)
    orders = [Order(c, c, 'S', 'b1', 1, 1,
                    (20, 20) if model==1 and c=='A' else (0, 25),
                    (20, 20) if c=='A' else (0, 25), 0, 2) for c in ('A', 'B')]
    problem = MultiBatchInstance(ProblemInstance(nodes, fleet, distance_matrix=distances,
        travel_time_matrix=durations), orders, policy, model=model)
    state = dict(deliveries={'b1': [['S']]}, pickup_order={'b1': ['S']},
        pickup_vehicle={'b1': {}}, e2=[dict(satellite='S', vehicle=0,
            tasks=[(c, k) for k in problem.task_kinds]) for c in ('A', 'B')])
    merged = [(c, k) for k in problem.task_kinds for c in ('B', 'A')]
    return problem, state, merged


@pytest.mark.parametrize('model', [1, 3])
@pytest.mark.parametrize('prepared', [False, True])
def test_deleted_preceding_trip_recovers_an_independently_audited_shorter_route(model, prepared):
    problem, state, merged = sequential_trips(model)
    original = deepcopy(state)
    score, decoded, issues = evaluate(problem, state)
    assert not issues and audit_solution(problem, decoded)['periodic_replay']['valid']
    route = {**state['e2'][1], 'tasks': merged}
    goods = _goods_ready(problem, state)
    stale_ready = decoded['e2_trips'][0]['return_complete']+problem.operating_policy.e2_turnaround_time
    assert stale_ready == 22
    assert _route_score(problem, route, goods, stale_ready)[0] > task_distance(problem, 'S', merged)
    complete = deepcopy(state); complete['e2'] = [route]
    new_score, result, issues = evaluate(problem, complete)
    assert not issues and new_score == score-(19 if model==1 else 17)
    assert audit_solution(problem, result)['periodic_replay']['valid']
    descent = SingleBatchDescent(problem, score_route=SingleBatchRouteScorer(problem) if prepared else None)
    descent.key = state_key(state)
    descent.queue = [('e2', ((0, []), (1, merged)))]
    assert descent.propose(state, decoded, time.perf_counter()+1) == complete
    assert state == original
    assert descent.deferred_count == descent.recovered == 1


@pytest.mark.parametrize('model', [1, 3])
@pytest.mark.parametrize('prepared', [False, True])
def test_deleted_trip_does_not_override_explicit_late_departure(model, prepared):
    problem, state, merged = sequential_trips(model)
    _, decoded, _ = evaluate(problem, state)
    state['e2'][1]['depart_after'] = 22
    descent = SingleBatchDescent(problem, score_route=SingleBatchRouteScorer(problem) if prepared else None)
    descent.key = state_key(state)
    descent.queue = [('e2', ((0, []), (1, merged)))]
    assert descent.propose(state, decoded, time.perf_counter()+1) is None
    assert state['e2'][1]['depart_after'] == 22
    assert descent.filtered == 1 and descent.recovered == 0


@pytest.mark.parametrize('model', [1, 3])
@pytest.mark.parametrize('prepared', [False, True])
def test_departure_hint_is_an_absolute_bound_without_repeating_dispatch_preparation(model, prepared):
    problem, state, merged = sequential_trips(model)
    problem.operating_policy = replace(problem.operating_policy, e2_dispatch_prep_time=5)
    state['e2'] = [dict(satellite='S', vehicle=0, tasks=merged,
                        depart_after=16, return_after=22)]
    _, decoded, issues = evaluate(problem, state)
    assert not issues and audit_solution(problem, decoded)['periodic_replay']['valid']
    assert _route_score(problem, state['e2'][0], _goods_ready(problem, state), 0)[0] > task_distance(problem, 'S', merged)
    descent = SingleBatchDescent(problem, score_route=SingleBatchRouteScorer(problem) if prepared else None)
    descent.key = state_key(state)
    descent.queue = [('e2', ((0, merged),))]
    saved = deepcopy(state)
    assert descent.propose(state, decoded, time.perf_counter()+1) == state
    assert state == saved


@pytest.mark.parametrize('model', [1, 3])
def test_recovered_move_preserves_original_candidate_priority_and_no_aliases(model):
    problem, state, merged = sequential_trips(model)
    _, decoded, issues = evaluate(problem, state)
    assert not issues
    descent = SingleBatchDescent(problem)
    descent.key = state_key(state)
    descent.queue = [('pickup', ('b1', 'S', 0)), ('e2', ((0, []), (1, merged)))]
    first = descent.propose(state, decoded, time.perf_counter()+1)
    assert first['e2'] == state['e2']
    assert first['pickup_vehicle']['b1'] == {'S': 0}
    assert descent.deferred_count == 1 and descent.recovered == 0
    second = descent.propose(state, decoded, time.perf_counter()+1)
    assert len(second['e2']) == 1 and second['e2'][0]['tasks'] == merged
    assert audit_solution(problem, evaluate(problem, second)[1])['periodic_replay']['valid']
    second['e2'][0]['tasks'].clear()
    first['deliveries']['b1'][0].clear()
    assert len(state['e2'][1]['tasks']) == len(problem.task_kinds)
    assert state['deliveries'] == {'b1': [['S']]}
    assert descent.propose(state, decoded, time.perf_counter()+1) is None


@pytest.mark.parametrize('model', [1, 3])
def test_empty_trips_do_not_shift_decoded_vehicle_readiness_indices(model):
    problem, state, _ = sequential_trips(model)
    state['e2'].insert(1, dict(satellite='S', vehicle=0, tasks=[]))
    _, decoded, issues = evaluate(problem, state)
    assert not issues and len(decoded['e2_trips']) == 2
    descent = SingleBatchDescent(problem)
    descent._prepare_context(state, decoded, state_key(state))
    assert descent.actual_before == {0: 0, 2: 22}
    assert descent._prepare_bounds(state, state_key(state), time.perf_counter()+1)
    assert descent.before == {0: 0, 1: 22, 2: 22}


def test_cached_context_refreshes_when_absolute_wait_changes(monkeypatch):
    import models.routing.solvers.alns.single_batch_descent as module
    problem, state, _ = sequential_trips(1)
    _, decoded, _ = evaluate(problem, state)
    calls = []
    original = module._goods_ready
    def goods(p, s):
        calls.append(state_key(s))
        return original(p, s)
    monkeypatch.setattr(module, '_goods_ready', goods)
    descent = SingleBatchDescent(problem)
    key = state_key(state)
    descent._prepare_context(state, decoded, key)
    descent._prepare_context(deepcopy(state), decoded, key)
    assert len(calls) == 1
    state['e2'][0]['return_after'] = 22
    _, decoded, issues = evaluate(problem, state)
    assert not issues
    descent._prepare_context(state, decoded, state_key(state))
    assert len(calls) == 2 and descent.actual_before[1] == 23
    descent.reset()
    assert descent.context_key is None and descent.bounds_key is None
    assert not descent.queue and not descent.deferred


def test_expired_or_interrupted_screen_retains_complete_candidate(monkeypatch):
    import models.routing.solvers.alns.single_batch_descent as module
    problem, state, merged = sequential_trips(1)
    saved = deepcopy(state)
    _, decoded, _ = evaluate(problem, state)
    clock = [0.0]
    def scorer(p, route, goods, ready):
        clock[0] += 2
        return _route_score(p, route, goods, ready)
    monkeypatch.setattr(module.time, 'perf_counter', lambda: clock[0])
    descent = SingleBatchDescent(problem, score_route=scorer)
    descent.key = state_key(state)
    detail = ((0, []), (1, merged))
    descent.deferred.append(detail)
    assert descent.propose(state, decoded, 0) is None
    assert list(descent.deferred) == [detail]
    assert descent.propose(state, decoded, 1) is None
    assert descent.bounds_key is None and list(descent.deferred) == [detail]
    candidate = descent.propose(state, decoded, 100)
    assert len(candidate['e2']) == 1
    assert audit_solution(problem, evaluate(problem, candidate)[1])['valid']
    assert state == saved


def test_local_screen_does_not_certify_unchanged_suffix_vehicle_feasibility():
    problem, state, _ = sequential_trips(3)
    _, decoded, issues = evaluate(problem, state)
    assert not issues
    state['e2'][0]['return_after'] = 25
    descent = SingleBatchDescent(problem)
    descent.key = state_key(state)
    descent.queue = [('e2', ((0, state['e2'][0]['tasks']),))]
    candidate = descent.propose(state, decoded, time.perf_counter()+1)
    assert candidate is not None
    assert 'E2_TIME_WINDOW' in evaluate(problem, candidate)[2]


@pytest.mark.parametrize('prepared', [False, True])
def test_prefix_bounds_are_optimistic_when_pickup_waits_for_another_vehicle(prepared):
    base, state, _ = sequential_trips(3)
    base.fleet.e2_per_sat_count = 2
    orders = [replace(o, pickup_window=(0, 25)) for o in base.orders]
    problem = MultiBatchInstance(base.network, orders, base.operating_policy, model=3)
    state['e2'] = [dict(satellite='S', vehicle=0, tasks=[('A', 'd')], depart_after=15),
                   dict(satellite='S', vehicle=1, tasks=[('A', 'p')]),
                   dict(satellite='S', vehicle=1, tasks=[('B', 'd'), ('B', 'p')])]
    _, decoded, issues = evaluate(problem, state)
    assert not issues and audit_solution(problem, decoded)['periodic_replay']['valid']
    descent = SingleBatchDescent(problem, score_route=SingleBatchRouteScorer(problem) if prepared else None)
    key = state_key(state)
    descent._prepare_context(state, decoded, key)
    assert descent._prepare_bounds(state, key, time.perf_counter()+1)
    assert descent.before[2] < descent.actual_before[2]
    assert all(descent.before[i] <= actual for i, actual in descent.actual_before.items())
