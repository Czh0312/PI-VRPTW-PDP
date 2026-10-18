"""Independent segment occupation, weighted pricing and original-model oracles."""
from dataclasses import replace
import itertools
import math
import random
import time

import pytest

from models.routing.model_1.exact.accelerators.single_batch.columns import DeliveryMaster, e1_delivery_tours, root_route_bound, short_route_catalog, task_price_floor
from models.routing.model_1.exact.accelerators.single_batch.delivery_pricer import DeliveryMipPricer
from models.routing.model_1.exact.accelerators.single_batch.route_pricing import price_delivery_routes
from models.routing.model_1.exact.accelerators.single_batch.time_budget import route_work, time_deadlines, time_windows
from models.routing.model_1.exact.accelerators.single_batch.route_pool import enrich_route_pool, route_pool_incumbent
from models.common.cycle.audit import audit_solution
from copy import deepcopy
from tests.routing.test_single_batch_columns import contexts, independently_enumerated_trips
from tests.routing.test_single_batch_route_pool import physical_case, raw_trip
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny


def manual_work(p, site, tasks, deadline):
    # Independent endpoint enumeration for every guaranteed operation span.
    policy, previous, total = p.operating_policy, None, 0
    a, b = deadline if isinstance(deadline, tuple) else (policy.cycle_start, deadline)
    def bounds(o):
        return max(policy.cycle_start, o.delivery_window[0]), min(o.delivery_window[1], policy.batches[0].cutoff-o.service_time)
    def charge(low, high, duration):
        if low > high:
            return 0
        return min(max(0, min(t+duration,b)-max(t,a)) for t in (low,high))
    for key in tasks:
        o = p.order_map[key]
        low, high = bounds(o)
        total += charge(low, high, o.service_time)
        travel = p.travel(site if previous is None else previous.customer, o.customer)
        if previous is None:
            duration = travel+policy.e2_dispatch_prep_time
            total += charge(policy.cycle_start, high-duration, duration)
        else:
            prev_low, prev_high = bounds(previous)
            total += charge(prev_low+previous.service_time, min(prev_high+previous.service_time, high-travel), travel)
        previous = o
    low, high = bounds(previous)
    tail = p.travel(previous.customer,site)+policy.e2_return_unload_time+policy.e2_turnaround_time
    return total+charge(low+previous.service_time, min(high+previous.service_time,policy.cycle_end-tail),tail)


def raw_segments(p, site, tasks, departure):
    policy = p.operating_policy
    rows = [(departure-policy.e2_dispatch_prep_time, departure)]
    previous, now = site, departure
    for key in tasks:
        o = p.order_map[key]
        arrival = now+p.travel(previous, o.customer)
        rows.append((now, arrival))
        now = max(arrival, o.delivery_window[0])
        if now > o.delivery_window[1] or now+o.service_time > policy.batches[0].cutoff:
            return None
        rows.append((now, now+o.service_time))
        now, previous = now+o.service_time, o.customer
    end = now+p.travel(previous, site)+policy.e2_return_unload_time+policy.e2_turnaround_time
    if end > policy.cycle_end:
        return None
    rows.append((now, end))
    return rows


@pytest.mark.parametrize('seed', range(8))
def test_charged_work_fits_actual_occupation_at_every_departure_and_deadline(seed):
    p = tiny_problem(2, seed)
    for site, ctx in contexts(p)[2].items():
        for c in independently_enumerated_trips(p, ctx):
            for d in range(ctx['release'], p.operating_policy.cycle_end+1):
                segments = raw_segments(p, site, c['tasks'], d)
                if segments is None:
                    continue
                deadlines = time_deadlines(p, ctx)+[b-1 for b in time_deadlines(p, ctx)]+time_windows(p, ctx)
                for b in deadlines:
                    work = route_work(p, site, c['tasks'], b)
                    assert work == manual_work(p, site, c['tasks'], b)
                    a, end_time = b if isinstance(b, tuple) else (p.operating_policy.cycle_start, b)
                    occupied = sum(max(0, min(end, end_time)-max(start, a)) for start, end in segments)
                    assert work <= occupied
                    if b == p.operating_policy.cycle_end:
                        assert work == sum(end-start for start, end in segments)


@pytest.mark.parametrize('method', ['label', 'mip'])
@pytest.mark.parametrize('window_mode', ['prefix', 'interval'])
@pytest.mark.parametrize('phase_one', [False, True])
@pytest.mark.parametrize('seed', range(8))
def test_weighted_pricing_matches_independent_route_permutations(method, window_mode, phase_one, seed):
    pytest.importorskip('gurobipy')
    p = tiny_problem(2, seed)
    for site, ctx in contexts(p)[2].items():
        rng = random.Random(seed+19)
        prices = {n['key']: rng.uniform(5, 40) for n in ctx['nodes']}
        weights = [(b, rng.uniform(.01, .3)) for b in (time_deadlines(p, ctx) if window_mode == 'prefix' else time_windows(p, ctx))]
        costs = [(0 if phase_one else c['cost'])+sum(v*manual_work(p, site, c['tasks'], b) for b, v in weights)
            -sum(prices[k] for k in c['tasks'])+2 for c in independently_enumerated_trips(p, ctx)]
        expected = min(costs, default=math.inf)
        if method == 'label':
            result = price_delivery_routes(p, ctx, prices, -2, phase_one=phase_one, work_prices=weights)
        else:
            pricer = DeliveryMipPricer(p, ctx)
            try:
                result = pricer.price(prices, -2, phase_one=phase_one, work_prices=weights)
            finally:
                pricer.dispose()
        assert result['complete']
        if math.isfinite(expected):
            assert result['minimum_reduced_cost'] == pytest.approx(expected)
            assert result['lower_bound'] <= expected+1e-9
        else:
            assert result['infeasible']


@pytest.mark.parametrize('phase_one', [False, True])
@pytest.mark.parametrize('window_mode', ['prefix', 'interval'])
@pytest.mark.parametrize('limit', [1, 3, 10])
def test_interrupted_time_weighted_label_bound_remains_conservative(phase_one, window_mode, limit):
    p = tiny_problem(2, 1)
    for site, ctx in contexts(p)[2].items():
        prices = {o.id: 50 for o in p.orders}
        weights = [(b, .2) for b in (time_deadlines(p, ctx) if window_mode == 'prefix' else time_windows(p, ctx))]
        expected = min((0 if phase_one else c['cost'])+sum(v*manual_work(p, site, c['tasks'], b) for b, v in weights)
            -sum(prices[k] for k in c['tasks']) for c in independently_enumerated_trips(p, ctx))
        result = price_delivery_routes(p, ctx, prices, phase_one=phase_one, work_prices=weights, label_limit=limit)
        assert result['lower_bound'] <= expected+1e-9


@pytest.mark.parametrize('model', [1, 2])
@pytest.mark.parametrize('seed', range(8))
def test_time_cut_certificate_never_exceeds_original_integer_optimum(model, seed):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model, seed)
    expected = enumerate_tiny(p)
    result = root_route_bound(p, time_limit=3, time_cuts=True)
    assert result['proven_optimal'] is False
    if math.isfinite(expected):
        assert not result['proven_infeasible']
        assert result['bound'] <= expected+1e-8
        assert result['relaxation_complete']


@pytest.mark.parametrize('seed', [0, 1, 4, 5])
def test_column_generation_matches_fully_enumerated_time_cut_lp(seed):
    gp = pytest.importorskip('gurobipy')
    p = tiny_problem(2, seed)
    domains, travel, sites = contexts(p)
    tours = e1_delivery_tours(p, domains, travel)
    master = DeliveryMaster(p, domains, sites, tours, time_cuts=True)
    try:
        for s, ctx in sites.items():
            for c in independently_enumerated_trips(p, ctx):
                master.add(s, c)
        master.phase_two()
        master.model.optimize()
        assert master.model.Status == gp.GRB.OPTIMAL
        for method in ['label', 'mip']:
            r = root_route_bound(p, time_limit=3, time_cuts=True, pricing_method=method)
            assert r['relaxation_complete']
            assert r['restricted_objective'] == pytest.approx(master.model.ObjVal)
    finally:
        master.dispose()


def test_time_cut_certifies_physical_infeasibility_missed_by_trip_count_relaxation():
    pytest.importorskip('gurobipy')
    p = physical_case(service=20, prep=0, turn=0)
    old = root_route_bound(p, time_limit=2)
    new = root_route_bound(p, time_limit=2, time_cuts=True)
    assert old['relaxation_complete'] and not old['proven_infeasible']
    assert new['proven_infeasible'] and not new['proven_optimal']
    assert new['status'] == 'INFEASIBLE_PHASE_ONE'
    assert math.isinf(enumerate_tiny(p))


@pytest.mark.parametrize('vehicles,service,window,prep,turn', [
    (1, 9, (0, 30), 3, 4), (2, 20, (0, 30), 3, 4), (1, 4, (15, 30), 3, 4)])
def test_all_integer_multi_trip_schedules_project_into_the_time_rows(vehicles, service, window, prep, turn):
    p = physical_case(vehicles, service, window, prep, turn)
    ctx = contexts(p)[2]['S']
    policy = p.operating_policy
    checked = 0
    tasks = [('a',), ('b',)]
    for supply in range(0, 8):
        for allocation in itertools.product(range(vehicles), repeat=2):
            for departures in itertools.product(range(supply+prep, policy.cycle_end+1), repeat=2):
                ends = [raw_trip(p, 'S', t, d) for t, d in zip(tasks, departures)]
                if any(e is None for e in ends):
                    continue
                valid = True
                for v in range(vehicles):
                    trips = sorted((d, e) for owner, d, e in zip(allocation, departures, ends) if owner == v)
                    if any(d-prep < previous_end+turn for (previous_d, previous_end), (d, e) in zip(trips, trips[1:])):
                        valid = False
                        break
                if not valid:
                    continue
                for a, b in time_windows(p, ctx):
                    charged = sum(route_work(p, 'S', t, (a, b)) for t in tasks)
                    assert charged <= vehicles*max(0, b-max(a, supply))
                checked += 1
    assert checked > 0


def test_time_prices_require_ordered_short_routes_for_task_correction():
    p = physical_case(service=0, prep=3, turn=0)
    p.orders = (replace(p.orders[0], delivery_window=(0, 5)), p.orders[1])
    p.order_map = {o.id: o for o in p.orders}
    # Let two tasks share a trip; ordering changes the early prep charge.
    p.fleet.e2_weight_cap = 4
    ctx = contexts(p)[2]['S']
    prices, weights = {'a': 50, 'b': 50}, [(5, 10)]
    catalog = short_route_catalog(p, ctx, ordered=True)
    costs = [(c['cost']+sum(v*manual_work(p, 'S', c['tasks'], b) for b, v in weights)
        -sum(prices[k] for k in c['tasks']), len(c['tasks'])) for c in independently_enumerated_trips(p, ctx)]
    beta = task_price_floor(prices, 0, min(rc for rc, size in costs), catalog,
        work_prices=weights, problem=p, site='S')
    assert all(beta*size <= rc+1e-9 for rc, size in costs)
    with pytest.raises(ValueError, match='ordered'):
        task_price_floor(prices, 0, -100, short_route_catalog(p, ctx), work_prices=weights, problem=p, site='S')


@pytest.mark.parametrize('method', ['label', 'mip'])
def test_negative_time_penalty_cannot_invalidate_analytic_pricing_bound(method):
    p = tiny_problem(2, 1)
    ctx = contexts(p)[2]['S']
    if method == 'label':
        with pytest.raises(ValueError, match='nonnegative'):
            price_delivery_routes(p, ctx, {}, work_prices=[(45, -1)])
    else:
        pricer = DeliveryMipPricer(p, ctx)
        try:
            with pytest.raises(ValueError, match='nonnegative'):
                pricer.price({}, work_prices=[(45, -1)])
        finally:
            pricer.dispose()


def test_reused_mip_time_limit_zero_keeps_a_safe_bound_for_new_interval_duals():
    p = tiny_problem(2, 1)
    for site, ctx in contexts(p)[2].items():
        pricer = DeliveryMipPricer(p, ctx)
        try:
            pricer.price({o.id: 30 for o in p.orders}, work_prices=[((0, 45), .1)])
            prices = {o.id: 55 for o in p.orders}
            weights = [(b, .3) for b in time_windows(p, ctx)]
            expected = min(c['cost']+sum(v*manual_work(p, site, c['tasks'], b) for b, v in weights)
                -sum(prices[k] for k in c['tasks']) for c in independently_enumerated_trips(p, ctx))
            result = pricer.price(prices, work_prices=weights, deadline=time.perf_counter()-1)
            assert result['lower_bound'] <= expected+1e-9
        finally:
            pricer.dispose()


def test_primal_enrichment_is_replayed_and_does_not_mutate_the_certified_pool():
    p = tiny_problem(2, 1)
    domains, travel, sites = contexts(p)
    pool = dict(domains=domains, contexts=sites, tours=e1_delivery_tours(p, domains, travel),
        columns=[(s, c) for s, ctx in sites.items() for c in independently_enumerated_trips(p, ctx) if len(c['tasks']) == 1],
        prices={s: {o.id: 30 for o in p.orders} for s in sites})
    before = deepcopy(pool['columns'])
    additions, stats = enrich_route_pool(p, pool, deadline=time.perf_counter()+1)
    assert pool['columns'] == before and stats['proof_scope'] == 'primal_discovery_only'
    assert additions
    for s, c in additions:
        oracle = {r['tasks']: r for r in independently_enumerated_trips(p, sites[s])}
        assert c['tasks'] in oracle and c['cost'] == pytest.approx(oracle[c['tasks']]['cost'])
    result, stats = route_pool_incumbent(p, pool, deadline=time.perf_counter()+2, enrich=True)
    assert result is not None and audit_solution(p, result)['valid']
    assert not result['info']['proven_optimal'] and result['info']['best_bound'] is None
    assert stats['enrichment']['proof_scope'] == 'primal_discovery_only'
