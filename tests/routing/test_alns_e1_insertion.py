"""Compare optimized E1 insertion with the original complete-trip oracle."""
from copy import deepcopy
from importlib import import_module
import random

import pytest

from models.common.cycle import MultiBatchInstance, Order
from models.common.domain.instance import ProblemInstance
from models.common.domain.node import Depot, Satellite, Customer


def full_returns(module, problem, state, quantities):
    p, f = problem.operating_policy, problem.fleet
    ready = [p.cycle_start] * f.e1_count
    trips, violations, releases = [], [], []
    for batch in p.batches:
        picked = [[] for _ in range(f.e1_count)]
        q = quantities[batch.id]
        for site in state['pickup_order'][batch.id]:
            if not q[site]:
                continue
            hint = state['pickup_vehicle'][batch.id].get(site)
            options = []
            for vehicle in range(f.e1_count):
                for pos in range(len(picked[vehicle]) + 1):
                    proposed = picked[vehicle][:pos] + [site] + picked[vehicle][pos:]
                    trip, issues = module._e1_trip(problem, batch, vehicle,
                        state['deliveries'][batch.id][vehicle], proposed, q, ready[vehicle])
                    options.append((len(issues), int(hint is not None and hint != vehicle),
                                    module.route_distance(problem, trip, 'e1'), vehicle, proposed))
            _, _, _, vehicle, picked[vehicle] = min(options, key=lambda item: item[:4])
        for vehicle in range(f.e1_count):
            trip, issues = module._e1_trip(problem, batch, vehicle,
                state['deliveries'][batch.id][vehicle], picked[vehicle], q, ready[vehicle])
            violations.extend(issues)
            if trip:
                trips.append(trip)
                ready[vehicle] = trip['return_complete'] + p.e1_turnaround_time
                quantity = sum(q[site] for site in picked[vehicle])
                if quantity:
                    releases.append((trip['return_complete'] + p.cleaning_duration, quantity))
    return trips, violations, releases


def random_input(seed):
    rng = random.Random(seed)
    sites = [f'S{i}' for i in range(1 + seed % 5)]
    nodes = {'D': Depot('D', 0, 0)}
    nodes.update({s: Satellite(s, 0, 0, rng.randrange(5)) for s in sites})
    nodes.update({f'C{i}': Customer(f'C{i}', 0, 0, 0, 0, (0, 125), (0, 125), s)
                  for i, s in enumerate(sites)})
    # Directed, nonmetric travel; equal and fractional distances exercise ties.
    distances = {a: {b: 0 if a == b else (1 if seed % 4 == 0 else rng.random() * 20)
                     for b in nodes} for a in nodes}
    durations = {a: {b: 0 if a == b else rng.randrange(1, 24) for b in nodes} for a in nodes}
    fleet = dict(E1_vehicle_count=1 + seed % 3, vehicles_per_satellite=1,
                 E1_Qk=rng.randrange(2, 30), E1_Ck=rng.randrange(1, 10),
                 E2_Qv=30, E2_Cv=30, container_weight=1, container_volume=1)
    policy = dict(policy_version='multi_batch_cutoff_v1', cycle_start=0, cycle_end=125,
                  batches=[dict(id='b1', delivery_duration=30, return_duration=30),
                           dict(id='b2', delivery_duration=30, return_duration=30)],
                  container_pool_size=30, cleaning_duration=rng.randrange(20), time_tick_seconds=60,
                  e1_load_time=rng.randrange(5), e1_turnaround_time=rng.randrange(8),
                  depot_return_unload_time=rng.randrange(5), e2_max_trips_per_vehicle=4)
    orders = [Order(f'{bid}_{i}', f'C{i}', s, bid, 1, rng.randrange(1, 8),
                    (0, 120), (0, 120), 0, 1) for bid in ('b1', 'b2') for i, s in enumerate(sites)]
    problem = MultiBatchInstance(ProblemInstance(nodes, fleet, distance_matrix=distances,
                                travel_time_matrix=durations), orders, policy, model=5)
    state = dict(deliveries={}, pickup_order={}, pickup_vehicle={})
    quantities = {}
    for batch in problem.operating_policy.batches:
        rows = [[] for _ in range(fleet['E1_vehicle_count'])]
        for site in rng.sample(sites, len(sites)):
            rows[rng.randrange(len(rows))].append(site)
        state['deliveries'][batch.id] = rows
        state['pickup_order'][batch.id] = rng.sample(sites, len(sites))
        state['pickup_vehicle'][batch.id] = {s: rng.randrange(len(rows)) for s in sites if rng.random() < .5}
        quantities[batch.id] = {s: rng.randrange(7) for s in sites}
    return problem, state, quantities


@pytest.mark.parametrize('number', [5, 6, 7, 8])
def test_insertion_agrees_with_full_decode_including_violations_and_ties(number):
    module = import_module(f'models.routing.model_{number}.alns.evaluator')
    violations = set()
    for seed in range(400):
        problem, state, quantities = random_input(seed)
        saved = deepcopy(state)
        actual = module._e1_returns(problem, state, quantities)
        assert actual == full_returns(module, problem, state, quantities), (number, seed)
        assert state == saved
        violations.update(actual[1])
    assert {'E1_WEIGHT_CAPACITY', 'E1_VOLUME_CAPACITY', 'E1_DELIVERY_DEADLINE',
            'E1_RETURN_DEADLINE', 'E1_VEHICLE_OVERLAP', 'E1_TERMINAL_VEHICLE',
            'TERMINAL_DIRTY'} <= violations


def test_lift_uses_two_replays_and_public_audit_still_distrusts_results(monkeypatch):
    from models.common.cycle import audit as physical
    from models.common.cycle.joint import audit_joint_solution
    from models.routing.model_6.alns.evaluator import JointEvaluator, initial_state
    from tests.routing.test_model6_joint import joint_fixture

    problem = joint_fixture()
    assignment = {'o1': 'b1', 'o2': 'b2'}
    evaluator = JointEvaluator(problem)
    state = initial_state(evaluator.fixed(assignment), random.Random(42))
    original = physical._audit
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(physical, '_audit', counted)
    _, candidate, issues = evaluator(assignment, state)
    assert not issues and candidate['audit']['periodic_replay']['valid']
    assert len(calls) == 2
    assert audit_joint_solution(problem, candidate) == candidate['audit']
    assert len(calls) == 3
    for change in ('event', 'cost', 'pool', 'batch', 'distance'):
        bad = deepcopy(candidate)
        if change == 'event':
            bad['e2_trips'][0]['events'][0]['start'] = -1
        elif change == 'cost':
            bad['info']['objective'] += 1
        elif change == 'pool':
            bad['container_pool_size'] = problem.pool_max + 1
        elif change == 'batch':
            bad['delivery_batch_count'] += 1
        else:
            bad['total_distance'] += 1
        assert not audit_joint_solution(problem, bad)['valid'], change
