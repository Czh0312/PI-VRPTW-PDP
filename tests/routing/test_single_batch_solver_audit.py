"""Independent tiny enumeration and counterexamples from the 2026-09-30 audit."""
from copy import deepcopy
from dataclasses import replace
import itertools
import math
import random

import pytest

from models.common.cycle import MultiBatchInstance, MultiBatchPolicy, Order
from models.common.cycle.assignment import assigned_problem
from models.common.cycle.audit import audit_solution
from models.common.cycle.state import empty_result
from models.common.domain.instance import ProblemInstance
from models.common.domain.node import Customer, Depot, Satellite
from models.routing.service import solve_routing
from models.routing.model_3.alns.evaluator import evaluate, initial_state
from models.routing.model_3.alns.neighborhoods import EvaluationCache, clone_state, destroy_repair, state_key


def nonmetric_problem():
    policy = MultiBatchPolicy.from_config(dict(policy_version='multi_batch_cutoff_v1', cycle_start=0,
        cycle_end=65, batches=[dict(id='b1', delivery_duration=30, return_duration=30)],
        container_pool_size=2, cleaning_duration=5, time_tick_seconds=60, e2_max_trips_per_vehicle=1))
    nodes = {'D': Depot('D', 0, 0), 'S': Satellite('S', 0, 0), 'T': Satellite('T', 0, 0),
             'A': Customer('A', 0, 0, 0, 0, (0, 65), (0, 65), 'S'),
             'B': Customer('B', 0, 0, 0, 0, (0, 65), (0, 65), 'T')}
    matrix = {a: {b: 0 if a == b else (100 if (a, b) == ('S', 'D') else 1)
                  for b in nodes} for a in nodes}
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=1, E1_Qk=50, E1_Ck=50,
                 E2_Qv=50, E2_Cv=50, container_weight=1, container_volume=1)
    orders = [Order('a', 'A', 'S', 'b1', 1, 1, (0, 25), (0, 25), 0),
              Order('b', 'B', 'T', 'b1', 1, 1, (0, 25), (0, 25), 0)]
    return MultiBatchInstance(ProblemInstance(nodes, fleet, distance_matrix=matrix,
                                               travel_time_matrix=matrix), orders, policy)


def test_nonmetric_provisional_return_cannot_reject_valid_mixed_trip():
    p = nonmetric_problem()
    state = dict(deliveries={'b1': [['T', 'S']]}, pickup_order={'b1': ['S', 'T']},
                 pickup_vehicle={'b1': {}}, e2=[
                     dict(satellite='S', vehicle=0, tasks=[('a', 'd'), ('a', 'p')]),
                     dict(satellite='T', vehicle=0, tasks=[('b', 'd'), ('b', 'p')])])
    score, result, violations = evaluate(p, state)
    assert audit_solution(p, result)['valid']
    assert violations == []
    assert score == 8


def test_assignment_copy_preserves_network_but_not_order_aggregates():
    p = nonmetric_problem()
    before = deepcopy(p.demands)
    fixed = assigned_problem(p, {'A': 'T', 'B': 'T'})
    assert fixed._travel is p._travel and fixed.network is p.network
    assert fixed.demands['S', 'b1'] == 0 and fixed.demands['T', 'b1'] == 2
    assert p.demands == before
    assert fixed.order_map['a'].satellite == 'T' and p.order_map['a'].satellite == 'S'
    with pytest.raises(ValueError, match='exactly'):
        assigned_problem(p, {'A': 'T'})


@pytest.mark.parametrize('method', ['alns', 'exact'])
@pytest.mark.parametrize('model', [1, 2, 3, 4])
def test_integral_float_count_is_canonicalized_for_both_solvers(model, method):
    if method == 'exact':
        pytest.importorskip('gurobipy')
    from tests.routing.test_single_batch_comparison import problem
    original = problem(model)
    p = MultiBatchInstance(original.network, [replace(o, count=1.0) for o in original.orders],
                           original.operating_policy, model=model)
    assert all(type(o.count) is int for o in p.orders)
    result = solve_routing(model=model, method=method, problem=p,
                           alns_time=.1, alns_iter=0, exact_time=2)
    assert result['audit']['valid']
    assert result['audit']['periodic_replay']['valid']


def test_cache_key_covers_waits_vehicle_order_and_pickup_hints():
    p = nonmetric_problem()
    state = initial_state(p, random.Random(1))
    cache = EvaluationCache(p, evaluate)
    a = cache(state)
    assert cache(clone_state(state)) is a and cache.hits == 1
    changed = clone_state(state)
    changed['e2'][0]['return_after'] = 31
    assert state_key(changed) != state_key(state)
    b = cache(changed)
    assert 'TERMINAL_OUTSTANDING' in b[2]
    assert cache.misses == 2
    changed = clone_state(state)
    changed['pickup_vehicle']['b1']['S'] = 0
    assert state_key(changed) != state_key(state)


def test_destroy_repair_preserves_complete_task_multiset_and_input():
    import time
    p = nonmetric_problem()
    state = initial_state(p, random.Random(1))
    before = deepcopy(state)
    candidate = destroy_repair(p, state, random.Random(2), 'related', time.perf_counter()+1)
    assert sorted(t for r in candidate['e2'] for t in r['tasks']) == [('a', 'd'), ('a', 'p'), ('b', 'd'), ('b', 'p')]
    assert state == before
    assert destroy_repair(p, state, random.Random(2), 'random', 0) == state


def tiny_problem(model, seed):
    rng = random.Random(seed)
    policy = MultiBatchPolicy.from_config(dict(policy_version='multi_batch_cutoff_v1', cycle_start=0,
        cycle_end=100, batches=[dict(id='b1', delivery_duration=65, return_duration=30)],
        container_pool_size=3, cleaning_duration=3, time_tick_seconds=60, e2_max_trips_per_vehicle=1,
        e1_load_time=1, e2_dispatch_prep_time=1, e2_return_unload_time=1, depot_return_unload_time=1))
    nodes = {'D': Depot('D', 0, 0), 'S': Satellite('S', 0, 0), 'T': Satellite('T', 0, 0)}
    orders = []
    for i in range(2+(seed % 2)):
        cid = f'C{i}'
        nodes[cid] = Customer(cid, 0, 0, 0, 0, (0, 100), (0, 100), 'S' if i % 2 else 'T')
        orders.append(Order(f'o{i}', cid, 'S' if i % 2 else 'T', 'b1', 1, 1,
                            (0, 8 if seed == 3 else 45), (8, 60), 1, 2))
    # Directed and nonmetric; seed 0 is a zero-distance/zero-travel case.
    distance = {a: {b: 0 if a == b or seed == 0 else rng.randint(1, 12) for b in nodes} for a in nodes}
    duration = {a: {b: 0 if a == b or seed == 0 else rng.randint(1, 7) for b in nodes} for a in nodes}
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=1, E1_Qk=20, E1_Ck=20,
                 E2_Qv=4 if seed == 4 else 20, E2_Cv=2 if seed == 4 else 20,
                 container_weight=1, container_volume=1)
    return MultiBatchInstance(ProblemInstance(nodes, fleet, distance_matrix=distance,
                                               travel_time_matrix=duration), orders, policy, model=model)


def enumerate_tiny(problem):
    """One E1 vehicle and one E2 trip per site, explicitly enumerated.

    No MILP, ALNS evaluator, normalization, or search helpers are used. Build
    earliest schedules directly for all permissible task permutations; then
    replay them with the independent auditor, including load and inventory.
    """
    customers = sorted({o.customer for o in problem.orders})
    assignments = itertools.product(problem.satellites, repeat=len(customers)) if problem.free_assignment else [None]
    best = math.inf
    for values in assignments:
        assignment = dict(zip(customers, values)) if values is not None else {o.customer: o.satellite for o in problem.orders}
        fixed = MultiBatchInstance(problem.network,
            [replace(o, satellite=assignment[o.customer]) for o in problem.orders],
            problem.operating_policy, model=3 if problem.reusable else 1)
        p = fixed.operating_policy
        b = p.batches[0]
        sites = sorted({o.satellite for o in fixed.orders})
        choices = []
        for s in sites:
            tasks = [(o.id, k) for o in fixed.orders if o.satellite == s for k in fixed.task_kinds]
            choices.append([seq for seq in itertools.permutations(tasks) if all(
                seq.index((o.id, 'd')) < seq.index((o.id, 'p')) for o in fixed.orders
                if fixed.reusable and o.satellite == s)])
        for delivery in itertools.permutations(sites):
            for pickup in (itertools.permutations(sites) if fixed.reusable else [()]):
                e1events, ready = [], {}
                now, previous = b.start+p.e1_load_time, fixed.depot
                for s in delivery:
                    now += fixed.travel(previous, s)
                    e1events.append(dict(site_id=s, kind='d', start=now, quantity=fixed.demands[s, b.id]))
                    now += fixed.service(s)
                    ready[s] = now
                    previous = s
                if pickup:
                    now = max(now, b.cutoff)
                for s in pickup:
                    now += fixed.travel(previous, s)
                    e1events.append(dict(site_id=s, kind='p', start=now, quantity=fixed.demands[s, b.id]))
                    now += fixed.service(s)
                    previous = s
                returned = now+fixed.travel(previous, fixed.depot)+p.depot_return_unload_time
                for sequences in itertools.product(*choices):
                    result = empty_result(fixed, 'oracle')
                    result['e1_trips'] = [dict(trip_id='e1', vehicle_id=0, batch_id=b.id,
                        departure=b.start+p.e1_load_time, return_complete=returned, events=e1events)]
                    for s, seq in zip(sites, sequences):
                        now, previous, done, events = ready[s]+p.e2_dispatch_prep_time, s, {}, []
                        for oid, kind in seq:
                            o = fixed.order_map[oid]
                            now = max(now+fixed.travel(previous, o.customer),
                                      (o.delivery_window if kind == 'd' else o.pickup_window)[0])
                            if kind == 'p':
                                now = max(now, done[oid]+o.min_use_time)
                            events.append(dict(order_id=oid, kind=kind, start=now, quantity=o.count))
                            now += o.service_time
                            if kind == 'd':
                                done[oid] = now
                            previous = o.customer
                        result['e2_trips'].append(dict(trip_id='e2_'+s, satellite_id=s, vehicle_id=0,
                            trip_index=0, departure=ready[s]+p.e2_dispatch_prep_time,
                            return_complete=now+fixed.travel(previous, s)+p.e2_return_unload_time, events=events))
                    audited = audit_solution(fixed, result)
                    if audited['valid']:
                        best = min(best, audited['objective'])
    return best




def test_same_seed_iteration_limited_alns_is_reproducible():
    p = tiny_problem(3, 2)
    a = solve_routing(model=3, method='alns', problem=p, alns_time=20, alns_iter=80, seed=17)
    b = solve_routing(model=3, method='alns', problem=p, alns_time=20, alns_iter=80, seed=17)
    assert a['info']['iterations'] == b['info']['iterations'] == 80
    assert a['e1_trips'] == b['e1_trips'] and a['e2_trips'] == b['e2_trips']
    assert a['info']['objective'] == b['info']['objective']
