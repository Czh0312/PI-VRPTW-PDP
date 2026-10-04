"""Whole-request swaps preserve task ownership and cross a capacity barrier."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
import random
import time

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.domain.instance import ProblemInstance
from models.routing.service import solve_routing
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.single_batch_cache import SingleBatchCache
from models.routing.solvers.alns.single_batch_exchange import exchange_pairs
from tests.routing.test_single_batch_solver_audit import tiny_problem


class ChosenSwap:
    """Deterministic pair selection, independent of stochastic solver policy."""
    def __init__(self, x='o1', y='o2'):
        self.x, self.y = x, y

    def sample(self, rows, size):
        assert size == 2
        return rows[:2]

    def choice(self, rows):
        if isinstance(rows[0], list):
            return rows[0]
        return self.x if self.x in rows else self.y


def fixture():
    base = tiny_problem(3, 1)
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
        replace(base.operating_policy, e2_max_trips_per_vehicle=2, container_pool_size=4), model=3)
    state = dict(deliveries={'b1': [['S']]}, pickup_order={'b1': ['S']},
        pickup_vehicle={'b1': {}}, e2=[
            dict(satellite='S', vehicle=0, tasks=[('o0', 'd'), ('o1', 'd'), ('o0', 'p'), ('o1', 'p')]),
            dict(satellite='S', vehicle=0, tasks=[('o2', 'd'), ('o3', 'd'), ('o2', 'p'), ('o3', 'p')])])
    return p, state


def test_exchange_improves_audited_route_when_pair_relocation_exceeds_capacity():
    p, state = fixture()
    original = deepcopy(state)
    score, result, issues = evaluate(p, state)
    assert not issues and audit_solution(p, result)['valid']
    relocation = deepcopy(state)
    relocation['e2'][0]['tasks'] = [t for t in relocation['e2'][0]['tasks'] if t[0] != 'o1']
    relocation['e2'][1]['tasks'] += [('o1', 'd'), ('o1', 'p')]
    assert 'E2_CAPACITY' in evaluate(p, relocation)[2]
    candidate = exchange_pairs(p, state, ChosenSwap(), time.perf_counter()+2)
    new_score, new_result, violations = evaluate(p, candidate)
    assert not violations and new_score < score
    audit = audit_solution(p, new_result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert Counter(t for r in candidate['e2'] for t in r['tasks']) == Counter(t for r in state['e2'] for t in r['tasks'])
    assert state == original


@pytest.mark.parametrize('seed', range(6))
def test_prepared_and_reference_geometry_agree_for_directed_nonmetric_matrix(seed):
    p, state = fixture()
    rng = random.Random(seed)
    matrix = {a: {b: 0 if a == b else rng.randrange(20) for b in p.network.nodes} for a in p.network.nodes}
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=1, E1_Qk=20, E1_Ck=20,
                 E2_Qv=4, E2_Cv=2, container_weight=1, container_volume=1)
    p = MultiBatchInstance(ProblemInstance(p.network.nodes, fleet,
        distance_matrix=matrix, travel_time_matrix={a: {b: 0 for b in matrix} for a in matrix}),
        p.orders, p.operating_policy, model=3)
    cache = SingleBatchCache(p)
    a = exchange_pairs(p, state, random.Random(seed), time.perf_counter()+2)
    b = exchange_pairs(p, state, random.Random(seed), time.perf_counter()+2,
                       distance=cache.route_scorer.distance, task=cache.evaluator.task)
    assert a == b
    old_score = evaluate(p, state)[0]
    new_score, decoded, issues = evaluate(p, a)
    assert not issues and new_score <= old_score
    assert audit_solution(p, decoded)['valid']


def test_route_metadata_is_preserved_and_proposal_does_not_alias_input():
    p, state = fixture()
    state['e2'][0].update(depart_after=0, return_after=12)
    state['e2'][1].update(depart_after=13, return_after=20)
    before = deepcopy(state)
    candidate = exchange_pairs(p, state, ChosenSwap(), time.perf_counter()+2)
    for old, new in zip(state['e2'], candidate['e2']):
        assert {k: v for k, v in old.items() if k != 'tasks'} == {k: v for k, v in new.items() if k != 'tasks'}
    assert candidate['deliveries'] == state['deliveries']
    candidate['e2'][0]['tasks'].clear()
    candidate['deliveries']['b1'][0].clear()
    candidate['pickup_order']['b1'].clear()
    candidate['pickup_vehicle']['b1']['S'] = 9
    assert state == before


def test_filter_checks_pickup_load_even_when_both_initial_loads_fit():
    p, state = fixture()
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=2, E1_Qk=20, E1_Ck=20,
                 E2_Qv=3, E2_Cv=4, container_weight=1, container_volume=1)
    orders = [replace(o, count=2, goods_weight=0) if o.id in ('o0', 'o2') else o
              for o in p.orders if o.id != 'o3']
    p = MultiBatchInstance(ProblemInstance(p.network.nodes, fleet,
        distance_matrix={a: {b: p.distance(a, b) for b in p.network.nodes} for a in p.network.nodes},
        travel_time_matrix={a: {b: 0 for b in p.network.nodes} for a in p.network.nodes}),
        orders, replace(p.operating_policy, container_pool_size=5, e2_max_trips_per_vehicle=3), model=3)
    state['e2'] = [dict(satellite='S', vehicle=0, tasks=[('o1', 'd'), ('o1', 'p'), ('o0', 'p')]),
                   dict(satellite='S', vehicle=0, tasks=[('o2', 'd'), ('o2', 'p')]),
                   dict(satellite='S', vehicle=1, tasks=[('o0', 'd')])]
    _, decoded, issues = evaluate(p, state)
    assert not issues and audit_solution(p, decoded)['valid']
    swapped = deepcopy(state)
    swapped['e2'][0]['tasks'] = [('o2', 'd'), ('o2', 'p'), ('o0', 'p')]
    swapped['e2'][1]['tasks'] = [('o1', 'd'), ('o1', 'p')]
    assert 'E2_CAPACITY' in evaluate(p, swapped)[2]
    def unused_distance(a, b):
        raise AssertionError('Capacity filter must reject before distance scoring')
    assert exchange_pairs(p, state, ChosenSwap(), time.perf_counter()+2,
                          distance=unused_distance) == state


@pytest.mark.parametrize('change', ['split', 'duplicate', 'reversed', 'other_site', 'too_large', 'expired'])
def test_ineligible_or_capacity_violating_swap_is_a_complete_noop(change):
    p, state = fixture()
    if change == 'split':
        state['e2'][0]['tasks'].remove(('o1', 'p'))
        state['e2'][1]['tasks'].append(('o1', 'p'))
    elif change == 'duplicate':
        state['e2'][0]['tasks'].append(('o1', 'p'))
    elif change == 'reversed':
        state['e2'][0]['tasks'] = [('o1', 'p'), ('o0', 'd'), ('o1', 'd'), ('o0', 'p')]
    elif change == 'other_site':
        state['e2'][1]['satellite'] = 'T'
    elif change == 'too_large':
        orders = [replace(o, goods_weight=5) if o.id == 'o1' else o for o in p.orders]
        p = MultiBatchInstance(p.network, orders, p.operating_policy, model=3)
    original = deepcopy(state)
    # Fix the selected pair where possible; no alternative selection is allowed.
    if change in ('split', 'duplicate', 'reversed'):
        state['e2'][0]['tasks'] = [t for t in state['e2'][0]['tasks'] if t[0] != 'o0']
        original = deepcopy(state)
    candidate = exchange_pairs(p, state, ChosenSwap(), 0 if change == 'expired' else time.perf_counter()+2)
    assert candidate == original and state == original


@pytest.mark.parametrize('model', [1, 2, 3, 4])
def test_public_operator_scope_is_only_fixed_recovery(model):
    result = solve_routing(model=model, method='alns', problem=tiny_problem(model, 1),
                           alns_time=5, alns_iter=50, seed=11)
    assert ('exchange_pairs' in result['info']['operator_attempts']) == (model == 3)
    assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']


def test_delivery_only_problem_rejects_pair_exchange():
    with pytest.raises(ValueError, match='reusable B=1'):
        exchange_pairs(tiny_problem(1, 1), {}, random.Random(1), time.perf_counter()+2)
