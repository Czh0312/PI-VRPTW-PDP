"""Actual delivery/recovery batches, cutoff bins and independent replay."""
from copy import deepcopy
from dataclasses import replace
from importlib import import_module
import itertools
import random
import time
from types import SimpleNamespace

import pytest

from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import CalendarCycleInstance
from models.common.cycle.instance import Order
from models.common.cycle.joint import JointBatchInstance, lift_result
from models.common.cycle.policy import MultiBatchPolicy
from models.common.cycle.state import empty_result
from models.common.cycle.uniform import UniformCycleInstance
from models.common.domain.instance import ProblemInstance
from models.common.domain.node import Customer, Depot, Satellite
from models.routing.service import solve_routing
from tests.routing.test_model6_joint import exact_available
from tools.data.build_model6_demo_suite import synthetic_profile


def productive_fixture(number, *, count=2, orders=2, two_sites=False):
    nodes = {'D': Depot('D', 0, 0), 'S': Satellite('S', 0, 0)}
    if two_sites:
        nodes['T'] = Satellite('T', 0, 0)
    rows = []
    for i in range(orders):
        site = 'T' if two_sites and i % 2 else 'S'
        customer = f'C{i}'
        nodes[customer] = Customer(customer, 0, 0, 0, 0, (0, 132), (0, 132), site)
        rows.append(Order(f'o{i}', customer, site, 'b1', 1, 1, (0, 110), (0, 125),
                          0, 0, 0, ('b1', 'b2')))
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=orders, E1_Qk=100, E1_Ck=100,
                 E2_Qv=100, E2_Cv=100, container_weight=1, container_volume=1)
    policy = MultiBatchPolicy.from_config(dict(
        policy_version='multi_batch_cutoff_v1', cycle_start=0, cycle_end=132,
        batches=[dict(id='b1', delivery_duration=33, return_duration=33),
                 dict(id='b2', delivery_duration=33, return_duration=33)],
        container_pool_size=orders, cleaning_duration=5,
        e2_max_trips_per_vehicle=4, time_tick_seconds=60,
    ))
    options = dict(batch_count_candidates=[count])
    if number == 6:
        return UniformCycleInstance(ProblemInstance(nodes, fleet), rows, policy,
                                    dict(options, schedule_mode='uniform_cycle'), synthetic_profile())
    return CalendarCycleInstance(ProblemInstance(nodes, fleet), rows, policy,
                                 options, synthetic_profile(), model=number)


def fixed_child(problem, count=2):
    return problem.for_count(count) if problem.model_number == 6 else problem.fixed_calendar(count)


def explicit_plan(child, assignment, pickup_times, *, pool=None):
    """Hand-built zero-travel witness; no search or decoder determines events."""
    fixed = child.project(assignment, pool=pool)
    raw = empty_result(fixed, 'alns')
    policy = fixed.operating_policy
    returned = {(s, b.id): 0 for s in fixed.satellites for b in policy.batches}
    for i, order in enumerate(fixed.orders):
        delivery = fixed.batch_map[order.batch].start
        pickup = pickup_times[order.id]
        raw['e2_trips'].append(dict(
            trip_id=f'e2_{i}', satellite_id=order.satellite, vehicle_id=i, trip_index=0,
            departure=delivery, return_complete=pickup,
            events=[dict(order_id=order.id, kind='d', start=delivery, quantity=order.count),
                    dict(order_id=order.id, kind='p', start=pickup, quantity=order.count)],
        ))
        batch = next(b for b in policy.batches if pickup <= b.cutoff)
        returned[order.satellite, batch.id] += order.count
    for batch in policy.batches:
        deliveries = [dict(kind='d', site_id=s, start=batch.start, quantity=fixed.demands[s, batch.id])
                      for s in fixed.satellites if fixed.demands[s, batch.id]]
        pickups = [dict(kind='p', site_id=s, start=batch.cutoff, quantity=returned[s, batch.id])
                   for s in fixed.satellites if returned[s, batch.id]]
        if deliveries or pickups:
            raw['e1_trips'].append(dict(
                trip_id=f'e1_{batch.id}', batch_id=batch.id, vehicle_id=0,
                departure=batch.start if deliveries else batch.cutoff,
                return_complete=batch.cutoff if pickups else batch.start,
                events=deliveries + pickups,
            ))
    return fixed, raw


@pytest.mark.parametrize('number', [6, 7, 8])
def test_independent_audit_rejects_a_completely_idle_calendar_batch(number):
    child = fixed_child(productive_fixture(number))
    assignment = {'o0': 'b2', 'o1': 'b2'}
    fixed, raw = explicit_plan(child, assignment, {'o0': 80, 'o1': 85})
    audited = audit_solution(fixed, raw)
    assert not audited['valid']
    assert {e['code'] for e in audited['errors']} == {'EMPTY_BATCH_DELIVERY', 'EMPTY_BATCH_RECOVERY'}
    lifted = lift_result(child, raw, assignment, child.pool_max, 'alns')
    assert lifted['info']['status'] == 'AUDIT_FAILED'


@pytest.mark.parametrize('number', [6, 7, 8])
def test_delivery_without_any_recovery_does_not_count_as_a_productive_batch(number):
    child = fixed_child(productive_fixture(number))
    fixed, raw = explicit_plan(child, {'o0': 'b1', 'o1': 'b2'}, {'o0': 80, 'o1': 85})
    audited = audit_solution(fixed, raw)
    assert not audited['valid']
    assert audited['errors'] == [dict(code='EMPTY_BATCH_RECOVERY', batch='b1')]


@pytest.mark.parametrize('number', [6, 7, 8])
def test_decoder_penalizes_empty_delivery_and_recovery_batches(number):
    child = fixed_child(productive_fixture(number))
    fixed, _ = explicit_plan(child, {'o0': 'b2', 'o1': 'b2'}, {'o0': 80, 'o1': 85})
    module = import_module(f'models.routing.model_{number}.alns.evaluator')
    _, _, issues = module.evaluate(fixed, module.initial_state(fixed, random.Random(42)))
    assert 'EMPTY_BATCH_DELIVERY' in issues and 'EMPTY_BATCH_RECOVERY' in issues


@pytest.mark.parametrize('number', [6, 7, 8])
def test_cross_batch_recovery_is_allowed_when_every_batch_performs_both_tasks(number):
    child = fixed_child(productive_fixture(number, orders=3))
    assignment = {'o0': 'b1', 'o1': 'b1', 'o2': 'b2'}
    fixed, raw = explicit_plan(child, assignment, {'o0': 10, 'o1': 80, 'o2': 85}, pool=2)
    audited = audit_solution(fixed, raw)
    assert audited['valid'], audited['errors']
    assert audited['periodic_replay']['valid']
    assert [r['quantity'] for r in audited['cutoff_snapshots']] == [1, 2]
    assert [r['demand'] for r in audited['clean_stock']] == [2, 1]


@pytest.mark.parametrize('number', [6, 7, 8])
def test_cutoff_equality_and_next_cutoff_assignment_are_preserved(number):
    child = fixed_child(productive_fixture(number, orders=3))
    assignment = {'o0': 'b1', 'o1': 'b1', 'o2': 'b2'}
    fixed, raw = explicit_plan(child, assignment, {'o0': 33, 'o1': 34, 'o2': 80})
    audited = audit_solution(fixed, raw)
    assert audited['valid'], audited['errors']
    assert [r['quantity'] for r in audited['cutoff_snapshots']] == [1, 2]


@pytest.mark.parametrize('number', [6, 7, 8])
def test_covering_assignment_matches_independent_exhaustive_domain_check(number):
    cover = import_module(f'models.routing.model_{number}.alns.construction').cover_delivery_batches
    batches = ('b1', 'b2', 'b3')
    domains = [tuple(b for i, b in enumerate(batches) if mask & (1 << i)) for mask in range(1, 8)]
    for choices in itertools.product(domains, repeat=3):
        legal = dict(zip(('a', 'b', 'c'), choices))
        problem = SimpleNamespace(batch_map=dict.fromkeys(batches), legal_batches=legal)
        possible = any(set(candidate) == set(batches) for candidate in itertools.product(*choices))
        repaired = cover(problem, {oid: values[0] for oid, values in legal.items()})
        assert (repaired is not None) == possible, legal
        if repaired is not None:
            assert set(repaired.values()) == set(batches)
            assert all(bid in legal[oid] for oid, bid in repaired.items())


@pytest.mark.parametrize('number', [6, 7, 8])
def test_last_order_can_swap_batches_without_creating_an_empty_batch(number):
    child = fixed_child(productive_fixture(number))
    evaluator = import_module(f'models.routing.model_{number}.alns.evaluator').JointEvaluator(child)
    move = import_module(f'models.routing.model_{number}.alns.neighborhoods')._batch_move
    assignment, state = move(child, evaluator, {'o0': 'b1', 'o1': 'b2'}, random.Random(42),
                             'split_batch', time.perf_counter() + 1)
    assert assignment == {'o0': 'b2', 'o1': 'b1'}
    _, candidate, issues = evaluator(assignment, state)
    assert candidate is not None and not issues
    assert candidate['audit']['valid']


@pytest.mark.parametrize('number', [6, 7, 8])
@pytest.mark.parametrize('count', [1, 2])
def test_alns_returns_only_productive_batches(number, count):
    problem = productive_fixture(number, count=count)
    result = solve_routing(model=number, method='alns', problem=problem, alns_time=.35, alns_iter=60)
    assert result['audit']['valid'], result['info']
    assert result['cycle_batch_count'] == result['delivery_batch_count'] == result['actual_recovery_round_count'] == count
    assert all(b['container_demand'] > 0 for b in result['batches'])
    assert audit_solution(problem, result)['valid']


@pytest.mark.parametrize('number', [6, 7, 8])
@pytest.mark.parametrize('method', ['alns', 'exact'])
def test_one_order_cannot_populate_two_actual_batches(number, method):
    problem = productive_fixture(number, orders=1)
    result = solve_routing(model=number, method=method, problem=problem,
                           alns_time=.1, alns_iter=10, exact_time=1)
    assert result['info']['proven_infeasible']
    assert result['info']['objective'] is None


@pytest.mark.parametrize('number', [6, 7, 8])
def test_multiple_satellites_in_one_round_are_counted_once(number):
    problem = productive_fixture(number, count=1, two_sites=True)
    result = solve_routing(model=number, method='alns', problem=problem, alns_time=.2, alns_iter=20)
    assert result['audit']['valid'], result['info']
    assert len([r for r in result['recovery_rounds'] if r['quantity']]) == 2
    assert result['actual_recovery_round_count'] == 1
    forged = deepcopy(result)
    forged['actual_recovery_round_count'] = 2
    assert not audit_solution(problem, forged)['valid']


@pytest.mark.parametrize('number', [6, 7, 8])
def test_exact_solver_enforces_positive_delivery_and_recovery(number, exact_available):
    problem = productive_fixture(number)
    result = solve_routing(model=number, method='exact', problem=problem, exact_time=5)
    assert result['audit']['valid'] and result['info']['proven_optimal']
    assert result['delivery_batch_count'] == result['actual_recovery_round_count'] == 2
    assert audit_solution(problem, result)['valid']


@pytest.mark.parametrize('number', [6, 7, 8])
def test_exact_solver_rejects_a_delivery_batch_with_no_possible_return(number, exact_available):
    problem = productive_fixture(number)
    problem.orders = tuple(replace(o, pickup_window=(80, 90)) for o in problem.orders)
    problem.order_map = {o.id: o for o in problem.orders}
    # Rebuild rather than alter a cached child or physical travel data.
    factory = UniformCycleInstance if number == 6 else CalendarCycleInstance
    kwargs = {} if number == 6 else dict(model=number)
    problem = factory(problem.network, problem.orders, problem.operating_policy,
                      problem.options, problem.cost_profile.config, **kwargs)
    child = fixed_child(problem)
    module = import_module(f'models.routing.model_{number}.exact.' + 'enhanced_solver')
    result = module.solve_exact(child, time_limit=5)
    assert result['info']['proven_infeasible'] and result['info']['objective'] is None
