"""Independent departure/vehicle enumeration for CG primal scheduling."""
from dataclasses import replace
import itertools
import math
import time

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.single_batch_columns import e1_delivery_tours, root_route_bound, solve_with_route_bound
from models.routing.solvers.exact.single_batch_route_pool import route_pool_incumbent, route_profile
from tests.routing.test_single_batch_columns import contexts, independently_enumerated_trips
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny
from tests.routing.test_single_batch_comparison import problem as one_customer


def full_pool(p):
    domains, travel, by_site = contexts(p)
    return dict(domains=domains, contexts=by_site, tours=e1_delivery_tours(p, domains, travel),
        columns=[(s, c) for s, ctx in by_site.items() for c in independently_enumerated_trips(p, ctx)])


def raw_trip(p, site, tasks, departure):
    # Does not call the production replay, profile, scheduling or normalization.
    policy, previous, now = p.operating_policy, site, departure
    for key in tasks:
        o = p.order_map[key]
        now = max(now+p.travel(previous, o.customer), o.delivery_window[0])
        if now > o.delivery_window[1] or now+o.service_time > policy.batches[0].cutoff:
            return None
        now, previous = now+o.service_time, o.customer
    now += p.travel(previous, site)+policy.e2_return_unload_time
    return now if now+policy.e2_turnaround_time <= policy.cycle_end else None


@pytest.mark.parametrize('seed', range(8))
def test_max_plus_profile_matches_every_integer_departure(seed):
    p = tiny_problem(2, seed)
    for site, ctx in contexts(p)[2].items():
        for c in independently_enumerated_trips(p, ctx):
            profile = route_profile(p, site, c['tasks'], 3)
            if profile is None:
                assert raw_trip(p, site, c['tasks'], 3+p.operating_policy.e2_dispatch_prep_time) is None
                continue
            for d in range(profile['release'], p.operating_policy.cycle_end+1):
                end = raw_trip(p, site, c['tasks'], d)
                if d > profile['latest']:
                    assert end is None
                else:
                    assert end == max(d+profile['duration'], profile['first_finish'])


@pytest.mark.parametrize('model', [1, 2])
@pytest.mark.parametrize('seed', range(8))
def test_full_tiny_pool_matches_independent_integer_oracle_without_global_proof(model, seed):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model, seed)
    oracle = enumerate_tiny(p)
    result, stats = route_pool_incumbent(p, full_pool(p), deadline=time.perf_counter()+2)
    assert stats['global_proof'] is False
    if math.isfinite(oracle):
        assert result is not None
        assert result['info']['objective'] == pytest.approx(oracle)
        assert audit_solution(p, result)['valid'] and result['audit']['periodic_replay']['valid']
        assert not result['info']['proven_optimal'] and not result['info']['proven_infeasible']
        assert result['info']['best_bound'] is None
    else:
        assert result is None


def physical_case(vehicles=1, service=12, window=(0, 30), prep=3, turn=4):
    original = one_customer(1, duplicate=True)
    # Zero directed travel isolates preparation, service and physical fleet.
    original.fleet.e2_per_sat_count = vehicles
    original.fleet.e2_weight_cap = 2
    policy = replace(original.operating_policy, e2_dispatch_prep_time=prep, e2_turnaround_time=turn)
    orders = [replace(o, service_time=service, delivery_window=window) for o in original.orders]
    p = MultiBatchInstance(original.network, orders, policy, model=1)
    for key in p._travel:
        p._travel[key] = 0
    return p


def schedule_oracle(p, tasks, supply):
    policy, fleet = p.operating_policy, p.fleet
    for assignment in itertools.product(range(fleet.e2_per_sat_count), repeat=len(tasks)):
        rows = [[i for i, v in enumerate(assignment) if v == vehicle] for vehicle in range(fleet.e2_per_sat_count)]
        if any(len(row) > policy.e2_max_trips_per_vehicle for row in rows):
            continue
        for permutations in itertools.product(*(list(itertools.permutations(row)) for row in rows)):
            feasible = True
            for row in permutations:
                ready = policy.cycle_start
                for i in row:
                    depart = max(ready, supply)+policy.e2_dispatch_prep_time
                    finish = raw_trip(p, 'S', tasks[i], depart)
                    if finish is None:
                        feasible = False
                        break
                    ready = finish+policy.e2_turnaround_time
                if not feasible:
                    break
            if feasible:
                return True
    return False


@pytest.mark.parametrize('vehicles,service,window,prep,turn', [
    (1, 12, (0, 30), 3, 4), (1, 20, (0, 30), 3, 4),
    (2, 20, (0, 30), 3, 4), (1, 0, (0, 0), 0, 0),
    (1, 0, (0, 30), 3, 4), (1, 12, (15, 30), 0, 0)])
def test_physical_scheduling_matches_permutation_oracle(vehicles, service, window, prep, turn):
    pytest.importorskip('gurobipy')
    p = physical_case(vehicles, service, window, prep, turn)
    tasks = [('a',), ('b',)]
    result, stats = route_pool_incumbent(p, full_pool(p), deadline=time.perf_counter()+2)
    assert (result is not None) == schedule_oracle(p, tasks, 0), stats
    if result:
        trips = result['e2_trips']
        assert audit_solution(p, result)['valid']
        for vehicle in range(vehicles):
            rows = sorted((t for t in trips if t['vehicle_id'] == vehicle), key=lambda t: t['trip_index'])
            assert len(rows) <= p.operating_policy.e2_max_trips_per_vehicle
            for previous, following in zip(rows, rows[1:]):
                assert following['departure']-prep >= previous['return_complete']+turn


def test_pool_rejects_relaxed_overlap_and_preserves_global_feasibility_unknown():
    pytest.importorskip('gurobipy')
    p = physical_case(service=20)
    result, stats = route_pool_incumbent(p, full_pool(p), deadline=time.perf_counter()+2)
    assert result is None and stats['status'] == 'NO_IMPROVEMENT_IN_POOL'
    assert not stats['global_proof']
    relaxed = root_route_bound(p, time_limit=1)
    assert relaxed['relaxation_complete'] and not relaxed['proven_infeasible']


def test_pool_handles_shared_customer_assignment_and_duplicate_physical_location():
    pytest.importorskip('gurobipy')
    p = one_customer(2, duplicate=True, window=(0, 5), pool=2)
    result, stats = route_pool_incumbent(p, full_pool(p), deadline=time.perf_counter()+2)
    assert result['assignment'] == {'C': 'T'} and result['info']['objective'] == pytest.approx(4)
    assert audit_solution(p, result)['valid']


def test_primal_slot_restriction_never_filters_the_complete_integer_solver():
    pytest.importorskip('gurobipy')
    base = physical_case(service=0, prep=0, turn=0)
    policy = replace(base.operating_policy, e2_max_trips_per_vehicle=3)
    orders = list(base.orders)+[replace(base.orders[0], id='c')]
    p = MultiBatchInstance(base.network, orders, policy, model=1)
    for key in p._travel:
        p._travel[key] = 0
    pool = full_pool(p)
    restricted, stats = route_pool_incumbent(p, pool, deadline=time.perf_counter()+2, slot_limit=2)
    assert restricted is None and stats['global_proof'] is False
    expanded, _ = route_pool_incumbent(p, pool, deadline=time.perf_counter()+2, slot_limit=3)
    assert expanded is not None and audit_solution(p, expanded)['valid']
    result = solve_with_route_bound(p, time_limit=3, root_time=1, pool_time=1)
    assert result['info']['proven_optimal'] and audit_solution(p, result)['valid']
    assert len(result['e2_trips']) == 3


def test_expired_primal_budget_returns_no_proof_and_no_candidate():
    p = tiny_problem(2, 1)
    result, stats = route_pool_incumbent(p, full_pool(p), deadline=time.perf_counter()-1)
    assert result is None and stats['status'] == 'TIME_LIMIT'
    assert stats['global_proof'] is False


def test_unimproved_pool_does_not_replace_audited_incumbent():
    pytest.importorskip('gurobipy')
    p = tiny_problem(2, 1)
    pool = full_pool(p)
    initial, _ = route_pool_incumbent(p, pool, deadline=time.perf_counter()+2)
    result, stats = route_pool_incumbent(p, pool, initial_result=initial, deadline=time.perf_counter()+2)
    assert result is None and stats['status'] == 'UNCHANGED_INCUMBENT'
    assert stats['initial_objective'] == initial['info']['objective']


def test_hybrid_transfers_only_audited_primal_and_certified_global_bound():
    pytest.importorskip('gurobipy')
    p = tiny_problem(2, 1)
    result = solve_with_route_bound(p, time_limit=3, root_time=1, pool_time=1, pricing_method='mip')
    assert result['audit']['valid'] and result['info']['proven_optimal']
    assert result['info']['objective'] == enumerate_tiny(p)
    assert result['info']['route_pool_primal']['improved']
    assert result['info']['route_pool_primal']['global_proof'] is False
    assert '_route_pool' not in result['info']['route_bound_certificate']


@pytest.mark.parametrize('model', [3, 4])
def test_primal_delivery_pool_cannot_silently_drop_pickups(model):
    with pytest.raises(ValueError, match='M1/M2'):
        route_pool_incumbent(tiny_problem(model, 1), {}, deadline=time.perf_counter()+1)


@pytest.mark.parametrize('budget', [-1, math.nan, math.inf])
def test_invalid_pool_budget_is_rejected(budget):
    with pytest.raises(ValueError, match='Pool budget'):
        solve_with_route_bound(tiny_problem(2, 1), pool_time=budget)


@pytest.mark.parametrize('limit', [0, True, 1.5])
def test_invalid_primal_slot_limit_is_rejected(limit):
    with pytest.raises(ValueError, match='slot limit'):
        route_pool_incumbent(tiny_problem(2, 1), {}, deadline=time.perf_counter()+1, slot_limit=limit)
