"""Independent cost/latest-departure frontiers and full-M5 projections."""
from collections import defaultdict
from dataclasses import replace
import gzip
import math
import json
import time

import pytest

from models.routing.common.cycle import MultiBatchInstance, Order
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Depot, Satellite, Customer
from models.routing.solvers.exact.multibatch_route_pricing import route_context
from models.routing.solvers.exact.multibatch_route_timing import enumerate_timed_route_universe
from models.routing.solvers.exact.multibatch_joint_inventory import (
    build_joint_inventory, set_joint_projection, solve_joint_inventory, joint_route_inventory_certificate)
from tests.routing.test_multibatch_cycle import fixture, hand_solution
from tests.routing.test_multibatch_departure_loads import nonmetric_problem
from tests.routing.test_multibatch_route_cg import enumerate_trips, detour_problem


def independent_windows(problem, site):
    places = list(problem.network.nodes)
    shortest = {(a, b): problem.travel(a, b) for a in places for b in places}
    for mid in places:
        for a in places:
            for b in places:
                shortest[a, b] = min(shortest[a, b], shortest[a, mid]+shortest[mid, b])
    policy = problem.operating_policy
    release = {b.id: b.start+policy.e1_load_time+shortest[problem.depot, site]
        +problem.service(site)+policy.e2_dispatch_prep_time for b in policy.batches}
    windows = {}
    for o in problem.orders:
        if o.satellite != site: continue
        d = max(o.delivery_window[0], release[o.batch]+shortest[site, o.customer])
        windows[o.id, 'd'] = d, o.delivery_window[1]
        windows[o.id, 'p'] = (max(o.pickup_window[0], d+o.service_time+o.min_use_time),
            min(o.pickup_window[1], policy.batches[-1].cutoff-o.service_time
                -shortest[o.customer, site]-policy.e2_return_unload_time))
    return release, windows


def forward_return(problem, site, sequence, departure, windows):
    """Forward replay, independent of production's backward recurrence."""
    previous, now, delivered = site, departure, {}
    for oid, kind in sequence:
        order = problem.order_map[oid]
        lo, hi = windows[oid, kind]
        now = max(now+problem.travel(previous, order.customer), lo)
        if kind == 'p' and (oid, 'd') in sequence:
            if oid not in delivered: return math.inf
            now = max(now, delivered[oid]+order.service_time+order.min_use_time)
        if now > hi: return math.inf
        if kind == 'd': delivered[oid] = now
        now += order.service_time
        previous = order.customer
    return now+problem.travel(previous, site)+problem.operating_policy.e2_return_unload_time


def oracle_frontiers(problem, site):
    release, windows = independent_windows(problem, site)
    policy = problem.operating_policy
    records = defaultdict(set)
    for sequence, cost in enumerate_trips(problem, site):
        ds = {problem.order_map[oid].batch for oid, kind in sequence if kind == 'd'}
        pickups = any(kind == 'p' for _, kind in sequence)
        start = max([policy.cycle_start]+[release[b] for b in ds])
        cutoffs = [(b.id, min(b.cutoff, policy.cycle_end-policy.e2_turnaround_time))
                   for b in policy.batches] if pickups else [(None, policy.cycle_end-policy.e2_turnaround_time)]
        for bid, horizon in cutoffs:
            if forward_return(problem, site, sequence, start, windows) > horizon: continue
            lo, hi = start, policy.cycle_end
            while lo < hi:
                mid = (lo+hi+1)//2
                if forward_return(problem, site, sequence, mid, windows) <= horizon: lo = mid
                else: hi = mid-1
            records[frozenset(sequence), bid].add((round(cost, 9), lo if ds else None))
    return {key: {r for r in rows if not any(other != r and other[0] <= r[0]
              and (r[1] is None or other[1] >= r[1]) for other in rows)} for key, rows in records.items()}


def domains(problem):
    return {s: (c, enumerate_timed_route_universe(problem, c)) for s in problem.satellites
            for c in [route_context(problem, s)] if c['nodes']}


@pytest.mark.parametrize('seed', range(6))
def test_frontiers_match_all_paths_and_independent_forward_binary_search(seed):
    problem = nonmetric_problem(seed)
    for site, (_, universe) in domains(problem).items():
        assert universe['complete']
        observed = defaultdict(set)
        for group in universe['timing_groups']:
            assert group['complete']
            for r in group['columns']:
                observed[frozenset(r['column']['tasks']), r['cutoff']].add(
                    (round(r['column']['cost'], 9), r['latest_departure']))
        assert dict(observed) == oracle_frontiers(problem, site)
        ordinary = {frozenset(c['tasks']): c['cost'] for g in universe['groups'] for c in g['columns']}
        oracle = enumerate_trips(problem, site)
        assert set(ordinary) == {frozenset(seq) for seq, _ in oracle}
        for keys, value in ordinary.items():
            assert value == pytest.approx(min(cost for seq, cost in oracle if frozenset(seq) == keys))


@pytest.mark.parametrize('seed', range(6))
def test_known_full_optimum_projects_to_complete_timed_joint_model(seed):
    gp = pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.solver import solve_exact
    problem = nonmetric_problem(seed)
    initial = solve_exact(problem, time_limit=5)
    assert initial['info']['proven_optimal'] and initial['audit']['valid']
    before = json.dumps(initial, sort_keys=True)
    d = domains(problem)
    model, context = build_joint_inventory(problem, d)
    try:
        set_joint_projection(problem, context, initial, fix=True)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL and model.ObjVal <= initial['info']['objective']+1e-6
    finally: model.dispose()
    certificate = solve_joint_inventory(problem, d, deadline=time.perf_counter()+5, initial_result=initial)
    assert certificate['timing_envelopes'] and certificate['bound'] <= initial['info']['objective']+1e-6
    assert not certificate['proven_optimal'] and json.dumps(initial, sort_keys=True) == before


@pytest.mark.parametrize('returned', [29, 30, 31])
@pytest.mark.parametrize('cleaning', [0, 20])
def test_cutoff_and_cleaning_boundaries_keep_original_projection(returned, cleaning):
    gp = pytest.importorskip('gurobipy')
    p = fixture(pool=2, cleaning=cleaning)
    model, context = build_joint_inventory(p, domains(p))
    try:
        set_joint_projection(p, context, hand_solution(p, returned), fix=True)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
    finally: model.dispose()


def tradeoff_problem():
    base = fixture(pool=4)
    nodes = {'D': Depot('D', 0, 0), 'S': Satellite('S', 0, 0),
             'A': Customer('A', 1, 0, 0, 0, (0, 125), (0, 125), 'S'),
             'B': Customer('B', 2, 0, 0, 0, (0, 125), (0, 125), 'S')}
    duration = {a: {b: (0 if a == b else 1) for b in nodes} for a in nodes}
    distance = {a: {b: (0 if a == b else 3) for b in nodes} for a in nodes}
    duration['B']['A'] = 20
    distance['S']['B'] = distance['B']['A'] = distance['A']['S'] = 1
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=1, E1_Qk=500, E1_Ck=500,
                 E2_Qv=500, E2_Cv=500, container_weight=1, container_volume=1)
    orders = [Order('a', 'A', 'S', 'b1', 1, 1, (0, 40), (0, 89), 0),
              Order('b', 'B', 'S', 'b1', 1, 1, (0, 80), (0, 89), 0)]
    return MultiBatchInstance(ProblemInstance(nodes, fleet, distance_matrix=distance, travel_time_matrix=duration),
                              orders, base.operating_policy)


def test_cost_only_dominance_cannot_remove_more_expensive_later_supply_path():
    problem = tradeoff_problem()
    universe = enumerate_timed_route_universe(problem, route_context(problem, 'S'))
    points = {(r['column']['cost'], r['latest_departure']) for g in universe['timing_groups'] for r in g['columns']
              if set(r['column']['tasks']) == {('a', 'd'), ('b', 'd')}}
    assert points == {(3, 19), (9, 39)}


def test_nonmetric_detour_and_delivery_only_after_last_cutoff_are_retained():
    problem = detour_problem()
    i = problem.network.node_index
    problem.network.time[i['S']][i['A']] = 1
    problem.network.time[i['A']][i['S']] = 110
    problem = MultiBatchInstance(problem.network, problem.orders, problem.operating_policy)
    universe = enumerate_timed_route_universe(problem, route_context(problem, 'S'))
    rows = [r for g in universe['timing_groups'] for r in g['columns']]
    assert any(r['column']['tasks'] == (('a', 'p'), ('b', 'p')) for r in rows)
    late = next(r for r in rows if r['column']['tasks'] == (('a', 'd'),))
    assert late['cutoff'] is None and late['column']['return_complete'] > problem.operating_policy.batches[-1].cutoff


def test_partial_frontier_cannot_supply_joint_lower_bound_and_parameter_guards():
    problem = fixture(pool=2)
    ctx = route_context(problem, 'S')
    for limit in [1, 2]:
        u = enumerate_timed_route_universe(problem, ctx, max_states=limit)
        assert not u['complete']
        with pytest.raises(ValueError, match='complete'): build_joint_inventory(problem, {'S': (ctx, u)})
    assert not enumerate_timed_route_universe(problem, ctx, deadline=0)['complete']
    out = joint_route_inventory_certificate(problem, time_limit=5, initial_result=hand_solution(problem),
        timing_envelopes=True, enumeration_max_states=1)
    assert out['joint_certificate'] is None and out['status'] == 'PARTIAL_ENUMERATION_SEPARATE_BOUND'
    for value in ['yes', 1, None]:
        with pytest.raises(ValueError, match='boolean'): joint_route_inventory_certificate(problem, timing_envelopes=value)


def test_public_export_separates_complete_ordinary_and_timing_universes(tmp_path):
    pytest.importorskip('gurobipy')
    from models.routing.variants.model_5.exact import joint_route_inventory_certificate as public
    problem = fixture(pool=2)
    out = public(problem, time_limit=5, joint_time_limit=1, recovery_time_limit=1,
        initial_result=hand_solution(problem), timing_envelopes=True, diagnostics_dir=tmp_path)
    with gzip.open(tmp_path/'site_0_best_pricing.json.gz', 'rt') as f: ordinary = json.load(f)
    with gzip.open(tmp_path/'site_0_best_pricing_timing.json.gz', 'rt') as f: timed = json.load(f)
    assert 'timing_groups' not in ordinary and timed['complete'] and timed['timing_groups']
    assert 'groups' not in timed and out['joint_certificate']['timing_envelopes']
    assert timed['timing_columns'] == sum(len(g['columns']) for g in timed['timing_groups'])
    assert 'timing_groups' not in out['reference_certificate']['sites'][0]['enumeration']
    assert out['bound'] <= out['upper_bound'] and out['runtime'] < 5.25
