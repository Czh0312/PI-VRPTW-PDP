"""Facility closure needs coordinated moves and complete physical replay."""
from copy import deepcopy
from importlib import import_module

import pytest

from models.common.cycle import MultiBatchInstance, MultiBatchPolicy, Order
from models.common.cycle.assignment import input_assignment
from models.common.cycle.audit import audit_solution
from models.common.domain.instance import ProblemInstance
from models.common.domain.node import Customer, Depot, Satellite
from models.routing.service import solve_routing


def consolidation_problem(number):
    policy = MultiBatchPolicy.from_config(dict(policy_version='multi_batch_cutoff_v1',
        cycle_start=0, cycle_end=100, batches=[dict(id='b1', delivery_duration=65, return_duration=30)],
        container_pool_size=4, cleaning_duration=3, time_tick_seconds=60,
        e2_max_trips_per_vehicle=4))
    nodes = {'D': Depot('D', 0, 0), 'S': Satellite('S', 0, 0), 'T': Satellite('T', 0, 0)}
    orders = []
    for i in range(4):
        name, site = f'C{i}', 'S' if i < 2 else 'T'
        nodes[name] = Customer(name, 0, 0, 0, 0, (0, 50), (60, 85), site)
        orders.append(Order(f'o{i}', name, site, 'b1', 1, 1, (0, 50), (60, 85), 0))
    distances = {a: {b: (0 if a == b else 10 if 'D' in (a, b) else
                        5 if {a, b} == {'S', 'T'} else 1) for b in nodes} for a in nodes}
    travel = {a: {b: 0 if a == b else 1 for b in nodes} for a in nodes}
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=2, E1_Qk=50, E1_Ck=50,
                 E2_Qv=50, E2_Cv=50, container_weight=1, container_volume=1)
    return MultiBatchInstance(ProblemInstance(nodes, fleet, distance_matrix=distances,
        travel_time_matrix=travel), orders, policy, model=number)


@pytest.mark.parametrize('number', [2, 4])
def test_coordinated_proposals_close_sites_without_mutating_input(number):
    p = consolidation_problem(number)
    current = input_assignment(p)
    saved = deepcopy(current)
    moves = import_module(f'models.routing.model_{number}.alns.assignment.moves')
    candidates = moves.consolidation_candidates(p, current)
    assert current == saved
    assert {tuple(sorted(c.items())) for c in candidates} == {
        tuple((c, s) for c in sorted(current)) for s in p.satellites}
    assert all(sum(c[k] != current[k] for k in current) == 2 for c in candidates)


@pytest.mark.parametrize('number', [2, 4])
def test_search_crosses_coordinated_assignment_barrier_with_valid_schedule(number):
    p = consolidation_problem(number)
    saved = input_assignment(p)
    r = solve_routing(model=number, method='alns', problem=p,
                      alns_time=10, alns_iter=2000, seed=42)
    assert len(set(r['assignment'].values())) == 1
    assert r['audit']['valid'] and audit_solution(p, r)['valid']
    assert r['audit']['periodic_replay']['valid']
    assert r['info']['iterations'] <= 2000
    assert not r['info']['proven_optimal'] and not r['info']['proven_infeasible']
    assert input_assignment(p) == saved
