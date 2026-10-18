"""Repair equivalence and joint-search partition/coverage regressions."""
from copy import deepcopy
from dataclasses import replace
import random
import time
from itertools import permutations

import pytest

from models.common.cycle import MultiBatchInstance
from models.common.cycle.assignment import input_assignment, assigned_problem
from models.common.cycle.audit import audit_solution
from models.routing.model_4.alns.search.assignment import AssignmentSearch, solve_alns
from models.routing.model_3.alns.construction import chronological_state, repair_assignment_state
from models.routing.model_3.alns.evaluator import evaluate, initial_state
from models.routing.model_3.alns import neighborhoods
from tests.routing.test_single_batch_solver_audit import tiny_problem


@pytest.mark.parametrize('model', [1, 3])
@pytest.mark.parametrize('seed', range(6))
def test_repair_cache_matches_full_rescoring_with_vehicle_reuse(model, seed, monkeypatch):
    base = tiny_problem(model, seed)
    orders = [replace(o, id=f'{o.id}_{i}') for i in range(3) for o in base.orders]
    p = MultiBatchInstance(base.network, orders,
                          replace(base.operating_policy, e2_max_trips_per_vehicle=12), model=model)
    state = chronological_state(p, random.Random(seed))
    original = deepcopy(state)
    removed = list(p.orders)[1:4]
    calls = []
    score = neighborhoods._route_score
    def counted(*args):
        calls.append(1)
        return score(*args)
    monkeypatch.setattr(neighborhoods, '_route_score', counted)
    cached = neighborhoods.repair_orders(p, state, removed, time.perf_counter()+10,
                                         fast_scores=False)
    cached_calls = len(calls)
    calls.clear()
    reference = neighborhoods.repair_orders(p, state, removed, time.perf_counter()+10,
                                            cache_scores=False, fast_scores=False)
    assert cached is not None and cached == reference
    assert cached_calls <= len(calls)
    assert state == original


def test_projection_preserves_tasks_when_decoder_cannot_produce_a_schedule():
    p = tiny_problem(4, 2)
    current = assigned_problem(p, input_assignment(p))
    state = initial_state(current, random.Random(1))
    # Put pickup before delivery: the complete search state still exists even
    # when the decoder returns early for a timing cycle.
    for route in state['e2']:
        route['tasks'].reverse()
    _, partial, violations = evaluate(current, state)
    assert 'TIMING_CYCLE' in violations and not partial['e2_trips']
    moved = {c: next(s for s in p.satellites if s != site)
             for c, site in input_assignment(p).items()}
    new = assigned_problem(p, moved)
    saved = deepcopy(state)
    repaired = repair_assignment_state(new, state, time.perf_counter()+5)
    assert repaired is not None and state == saved
    assert sorted(t for r in repaired['e2'] for t in r['tasks']) == sorted(
        (o.id, kind) for o in p.orders for kind in p.task_kinds)
    assert all(new.order_map[o].satellite == r['satellite']
               for r in repaired['e2'] for o, _ in r['tasks'])


def test_partition_evaluation_cache_never_reuses_another_assignment():
    p = tiny_problem(4, 2)
    search = AssignmentSearch(p, 42)
    fixed, first = search.context(input_assignment(p))
    state = initial_state(fixed, random.Random(1))
    a = first(state)
    changed = {c: next(s for s in p.satellites if s != site)
               for c, site in input_assignment(p).items()}
    other, second = search.context(changed)
    assert first is not second and fixed is not other
    b = second(state)
    assert 'E2_TASK_COVERAGE' in b[2]
    assert search.context(input_assignment(p))[1](state) is a


@pytest.mark.parametrize('model', [2, 4])
def test_best_partition_survives_later_accepted_equal_cost_assignments(model):
    p = tiny_problem(model, 0)
    original = input_assignment(p)
    r = solve_alns(p, max_time=10, max_iterations=160, seed=42)
    assert r['info']['assignment_attempts'] > 0
    assert r['assignment'] == original
    assert r['audit']['valid'] and audit_solution(p, r)['valid']
    assert r['info']['objective'] == 0
    assert not r['info']['proven_optimal'] and not r['info']['proven_infeasible']
    assert input_assignment(p) == original


def test_joint_iteration_limited_search_is_reproducible_and_history_is_monotone():
    p = tiny_problem(4, 2)
    a = solve_alns(p, max_time=20, max_iterations=160, seed=42)
    b = solve_alns(p, max_time=20, max_iterations=160, seed=42)
    assert a['e1_trips'] == b['e1_trips'] and a['e2_trips'] == b['e2_trips']
    assert a['assignment'] == b['assignment']
    assert a['info']['iterations'] == b['info']['iterations'] == 160
    costs = [x['objective'] for x in a['info']['incumbent_history']]
    assert costs and costs == sorted(set(costs), reverse=True)
    assert costs[-1] == a['info']['objective']
    assert a['info']['time_to_first_feasible'] <= a['info']['time_to_best']


@pytest.mark.parametrize('model', [1, 3])
def test_single_batch_descent_improves_directed_warm_route_and_preserves_seed(model, monkeypatch):
    from models.routing.model_3.alns.search.single_batch import solve_alns as solve_fixed
    base = tiny_problem(model, 1)
    p = assigned_problem(base, {o.customer: 'S' for o in base.orders})
    state = initial_state(p, random.Random(0))
    feasible = []
    for order in permutations(o.id for o in p.orders):
        state['e2'] = [dict(satellite='S', vehicle=0,
                            tasks=[(o, k) for k in p.task_kinds for o in order])]
        _, decoded, issues = evaluate(p, state)
        if not issues and audit_solution(p, decoded)['valid']:
            feasible.append(decoded)
    warm = max(feasible, key=lambda r: r['info']['objective'])
    saved = deepcopy(warm)
    # Control construction so this regression exercises incumbent descent,
    # rather than a better packed initial solution replacing the warm route.
    from models.routing.model_3.alns.search import single_batch
    monkeypatch.setattr(single_batch, 'initial_state',
        lambda problem, rng, **kwargs: neighborhoods.state_from_result(problem, warm))
    monkeypatch.setattr(single_batch, '_move', lambda problem, state, rng, op: deepcopy(state))
    monkeypatch.setattr(single_batch, 'destroy_repair',
        lambda problem, state, rng, mode, deadline, **kwargs: deepcopy(state))
    result = solve_fixed(p, max_time=10, max_iterations=40, initial_result=warm)
    assert warm == saved
    assert result['info']['operator_improvements']['descent'] > 0
    assert all(item['iteration'] >= 32 for item in result['info']['incumbent_history'][1:])
    assert result['info']['objective'] < warm['info']['objective']
    assert result['audit']['valid'] and audit_solution(p, result)['periodic_replay']['valid']
    assert not result['info']['proven_optimal'] and result['info']['best_bound'] is None


@pytest.mark.parametrize('model', [1, 3])
def test_single_batch_descent_respects_zero_iterations_and_tiny_budget(model):
    from models.routing.model_3.alns.search.single_batch import solve_alns as solve_fixed
    p = tiny_problem(model, 2)
    warm = solve_fixed(p, max_time=10, max_iterations=0)
    assert warm['audit']['valid']
    assert warm['info']['descent_candidates'] == 0
    assert warm['info']['operator_attempts']['descent'] == 0
    result = solve_fixed(p, max_time=.001, max_iterations=1000000, initial_result=warm)
    assert result['info']['objective'] <= warm['info']['objective']
    assert result['audit']['valid'] and result['info']['runtime'] < .5


def test_descent_site_cache_reindexes_after_route_removal_and_does_not_alias_proposals():
    from models.routing.model_3.alns.accelerators.single_batch_descent import SingleBatchDescent
    base = tiny_problem(3, 1)
    orders = [replace(o, id=f'{o.id}_{i}', delivery_window=(0, 70), pickup_window=(0, 80))
              for i in range(2) for o in base.orders]
    p = MultiBatchInstance(base.network, orders,
        replace(base.operating_policy, container_pool_size=6, e2_max_trips_per_vehicle=8), model=3)
    state = chronological_state(p, random.Random(2))
    assert len(state['e2']) > 2
    descent = SingleBatchDescent(p)
    descent._e2_candidates(state, time.perf_counter()+5)
    changed = deepcopy(state)
    # Preserve complete coverage but remove a route at the first satellite,
    # shifting all subsequent global indices at the other satellite.
    site = changed['e2'][0]['satellite']
    indices = [i for i, r in enumerate(changed['e2']) if r['satellite'] == site]
    a, b = indices[:2]
    changed['e2'][b]['tasks'] = changed['e2'][a]['tasks']+changed['e2'][b]['tasks']
    del changed['e2'][a]
    cached = descent._e2_candidates(changed, time.perf_counter()+5)
    fresh = SingleBatchDescent(p)._e2_candidates(changed, time.perf_counter()+5)
    assert cached == fresh and descent.site_cache_hits > 0
    saved = deepcopy(changed)
    _, decoded, issues = evaluate(p, changed)
    assert not issues
    candidate = descent.propose(changed, decoded, time.perf_counter()+5)
    assert candidate is not None
    candidate['e2'][0]['tasks'].clear()
    assert changed == saved
    assert descent.propose(changed, decoded, 0) is None


def test_delivery_exchange_crosses_capacity_barrier_with_repeated_customer_orders():
    from models.common.domain.instance import ProblemInstance
    from models.routing.model_3.alns.accelerators.single_batch_descent import SingleBatchDescent
    base = tiny_problem(1, 1)
    nodes = base.network.nodes
    positions = dict(D=0, S=0, T=0, C0=1, C1=9, C2=2)
    distances = {a: {b: abs(positions[a]-positions[b]) for b in nodes} for a in nodes}
    durations = {a: {b: 0 for b in nodes} for a in nodes}
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=1, E1_Qk=20, E1_Ck=20,
                 E2_Qv=4, E2_Cv=2, container_weight=1, container_volume=1)
    orders = [replace(o, satellite='S') for o in base.orders]
    orders.append(replace(orders[1], id='o3'))
    p = MultiBatchInstance(ProblemInstance(nodes, fleet, distance_matrix=distances,
        travel_time_matrix=durations), orders,
        replace(base.operating_policy, e2_max_trips_per_vehicle=2), model=1)
    state = dict(deliveries={'b1': [['S']]}, pickup_order={'b1': ['S']},
        pickup_vehicle={'b1': {}}, e2=[
            dict(satellite='S', vehicle=0, tasks=[('o0', 'd'), ('o1', 'd')]),
            dict(satellite='S', vehicle=0, tasks=[('o2', 'd'), ('o3', 'd')])])
    score, decoded, issues = evaluate(p, state)
    assert not issues
    relocated = deepcopy(state)
    relocated['e2'][1]['tasks'].append(relocated['e2'][0]['tasks'].pop())
    assert 'E2_CAPACITY' in evaluate(p, relocated)[2]
    descent = SingleBatchDescent(p)
    improved = False
    expected = sorted((o.id, 'd') for o in p.orders)
    while True:
        candidate = descent.propose(state, decoded, time.perf_counter()+2)
        if candidate is None:
            break
        assert sorted(t for r in candidate['e2'] for t in r['tasks']) == expected
        new_score, result, violations = evaluate(p, candidate)
        if not violations and new_score < score:
            assert audit_solution(p, result)['periodic_replay']['valid']
            improved = True
    assert improved
