"""Extra travel can buy earlier reuse and a lower total cost."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
import time

from models.common.cycle import Order
from models.common.cycle.audit import audit_solution
from models.common.cycle.fixed_pool import FixedBatchPoolInstance
from models.common.domain.instance import ProblemInstance
from models.common.domain.node import Customer, Depot, Satellite
from models.routing.model_5.alns.evaluator import evaluate
from models.routing.model_5.alns.pool_moves import PoolReuseDescent
from tests.routing.test_model5_pool_optimization import problem as pool_fixture
from tools.configuration.loader import load_json


def split_fixture():
    nodes = {'D': Depot('D', 0, 0), 'S': Satellite('S', 0, 0)}
    nodes.update({c: Customer(c, 0, 0, 0, 0, (0, 125), (0, 125), 'S') for c in 'ABC'})
    distance = {a: {b: int(a != b) for b in nodes} for a in nodes}
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=1,
                 E1_Qk=20, E1_Ck=20, E2_Qv=20, E2_Cv=20, container_weight=1, container_volume=1)
    policy = replace(pool_fixture().operating_policy, e2_max_trips_per_vehicle=4)
    orders = [Order('a', 'A', 'S', 'b1', 1, 1, (0, 25), (0, 29), 0, 1),
              Order('b', 'B', 'S', 'b1', 1, 1, (0, 25), (35, 50), 0, 1),
              Order('c', 'C', 'S', 'b2', 1, 1, (60, 85), (60, 89), 0, 1)]
    p = FixedBatchPoolInstance(ProblemInstance(nodes, fleet, distance_matrix=distance,
        travel_time_matrix=distance), orders, policy, {},
        load_json('config/experiments/benchmark27_v1/economic.json'))
    state = dict(deliveries={b: [['S']] for b in ('b1', 'b2')},
                 pickup_order={b: ['S'] for b in ('b1', 'b2')},
                 pickup_vehicle={b: {} for b in ('b1', 'b2')},
                 e2=[dict(satellite='S', vehicle=0, tasks=[('a', 'd'), ('a', 'p'), ('b', 'd'), ('b', 'p')]),
                     dict(satellite='S', vehicle=0, tasks=[('c', 'd'), ('c', 'p')])])
    return p, state


def test_split_reuses_box_despite_longer_distance_and_keeps_coverage():
    p, state = split_fixture()
    saved = deepcopy(state)
    score, decoded, issues = evaluate(p, state)
    assert not issues and audit_solution(p, decoded)['periodic_replay']['valid']
    assert decoded['container_pool_size'] == 3
    descent = PoolReuseDescent(p)
    candidate = descent.propose(state, decoded, time.perf_counter() + 1)
    assert candidate is not None
    assert Counter(t for r in candidate['e2'] for t in r['tasks']) == Counter(t for r in state['e2'] for t in r['tasks'])
    new_score, result, issues = evaluate(p, candidate)
    assert not issues and result['total_distance'] > decoded['total_distance']
    assert result['container_pool_size'] == 2 and new_score < score
    assert audit_solution(p, result)['periodic_replay']['valid']
    assert state == saved
    candidate['e2'][0]['tasks'].clear()
    assert state == saved
    assert descent.propose(state, decoded, 0) is None


def test_pool_moves_honor_trip_limit_and_do_not_search_when_pool_is_fixed():
    p, state = split_fixture()
    p.operating_policy = replace(p.operating_policy, e2_max_trips_per_vehicle=2)
    _, decoded, issues = evaluate(p, state)
    assert not issues
    assert PoolReuseDescent(p).propose(state, decoded, time.perf_counter() + 1) is None
    p.operating_policy = replace(p.operating_policy, e2_max_trips_per_vehicle=4)
    p.pool_min = decoded['container_pool_size']
    assert PoolReuseDescent(p).propose(state, decoded, time.perf_counter() + 1) is None


def test_split_without_a_clean_release_before_next_loading_is_not_proposed():
    p, state = split_fixture()
    p = FixedBatchPoolInstance(p.network, p.orders,
        replace(p.operating_policy, cleaning_duration=30), {}, p.cost_profile.config)
    _, decoded, issues = evaluate(p, state)
    assert not issues and audit_solution(p, decoded)['valid']
    assert decoded['container_pool_size'] > p.pool_min
    assert PoolReuseDescent(p).propose(state, decoded, time.perf_counter() + 1) is None


def test_nonmetric_return_bound_keeps_a_useful_split_via_another_satellite():
    base, state = split_fixture()
    nodes = dict(base.network.nodes)
    nodes['T'] = Satellite('T', 0, 0)
    for name, site in [('X', 'T'), ('F', 'S'), ('G', 'S')]:
        nodes[name] = Customer(name, 0, 0, 0, 0, (0, 125), (0, 125), site)
    distances = {a: {b: int(a != b) for b in nodes} for a in nodes}
    distances['S']['D'] = 30  # S -> T -> D is much faster than direct S -> D.
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=1, E1_Qk=20,
                 E1_Ck=20, E2_Qv=20, E2_Cv=20, container_weight=1, container_volume=1)
    orders = list(base.orders) + [
        Order('x', 'X', 'T', 'b1', 1, 1, (0, 25), (0, 29), 0, 1),
        Order('f', 'F', 'S', 'b2', 1, 1, (60, 85), (60, 89), 0, 1),
        Order('g', 'G', 'S', 'b2', 1, 1, (60, 85), (60, 89), 0, 1)]
    p = FixedBatchPoolInstance(ProblemInstance(nodes, fleet, distance_matrix=distances,
        travel_time_matrix=distances), orders, base.operating_policy, {}, base.cost_profile.config)
    state['deliveries']['b1'] = [['S', 'T']]
    for batch in ('b1', 'b2'):
        state['pickup_order'][batch] = ['S', 'T']
    state['e2'][1]['tasks'] += [('f', 'd'), ('g', 'd'), ('f', 'p'), ('g', 'p')]
    state['e2'].append(dict(satellite='T', vehicle=0, tasks=[('x', 'd'), ('x', 'p')]))
    score, decoded, issues = evaluate(p, state)
    assert not issues and audit_solution(p, decoded)['valid']
    descent = PoolReuseDescent(p)
    assert descent.travel_bounds['outward']['S']['D'] == 2
    candidate = descent.propose(state, decoded, time.perf_counter() + 1)
    assert candidate is not None
    new_score, result, issues = evaluate(p, candidate)
    assert not issues and audit_solution(p, result)['periodic_replay']['valid']
    assert result['container_pool_size'] == decoded['container_pool_size'] - 1
    assert new_score < score
