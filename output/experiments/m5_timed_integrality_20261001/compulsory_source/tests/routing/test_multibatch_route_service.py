"""All-path service extrema, including paths removed by cost dominance."""
from collections import defaultdict
from dataclasses import replace
import gzip
import math
import time

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_joint_inventory import (
    build_joint_inventory, set_joint_projection, solve_joint_inventory, joint_route_inventory_certificate)
from models.routing.solvers.exact.multibatch_route_timing import enumerate_timed_route_universe
from models.routing.solvers.exact.multibatch_route_pricing import route_context
from tests.routing.test_multibatch_route_timing import independent_windows, tradeoff_problem
from tests.routing.test_multibatch_route_cg import enumerate_trips
from tests.routing.test_multibatch_departure_loads import nonmetric_problem
from tests.routing.test_multibatch_cycle import fixture, hand_solution


def replay(problem, site, sequence, departure, windows, forced=None):
    now, previous, delivered, starts = departure, site, {}, {}
    for key in sequence:
        oid, kind = key
        order = problem.order_map[oid]
        low, high = windows[key]
        now = max(now+problem.travel(previous, order.customer), low)
        if kind == 'p' and (oid, 'd') in sequence:
            if oid not in delivered: return None
            now = max(now, delivered[oid]+order.service_time+order.min_use_time)
        if forced and forced[0] == key:
            if now > forced[1]: return None
            now = forced[1]
        if now > high: return None
        starts[key] = now
        if kind == 'd': delivered[oid] = now
        now += order.service_time
        previous = order.customer
    return starts, now+problem.travel(previous, site)+problem.operating_policy.e2_return_unload_time


def oracle(problem, site):
    release, windows = independent_windows(problem, site)
    p = problem.operating_policy
    zero = {key: (0, math.inf) for key in windows}
    records = {}
    for sequence, _ in enumerate_trips(problem, site):
        delivery = {problem.order_map[oid].batch for oid, kind in sequence if kind == 'd'}
        start = max([p.cycle_start]+[release[b] for b in delivery])
        earliest, returned = replay(problem, site, sequence, start, windows)
        offsets, _ = replay(problem, site, sequence, 0, zero)
        cutoffs = [(b.id, min(b.cutoff, p.cycle_end-p.e2_turnaround_time)) for b in p.batches] \
            if any(k == 'p' for _, k in sequence) else [(None, p.cycle_end-p.e2_turnaround_time)]
        for bid, horizon in cutoffs:
            if returned > horizon: continue
            lo, hi = start, p.cycle_end
            while lo < hi:
                mid = (lo+hi+1)//2
                state = replay(problem, site, sequence, mid, windows)
                if state is not None and state[1] <= horizon: lo = mid
                else: hi = mid-1
            key = frozenset(sequence), bid
            r = records.setdefault(key, dict(delivery_offsets={}, delivery_earliest={}, pickup_latest={},
                                            return_earliest=returned, latest_start_max=lo))
            r['return_earliest'] = min(r['return_earliest'], returned)
            r['latest_start_max'] = max(r['latest_start_max'], lo)
            for task in sequence:
                oid, kind = task
                if kind == 'd':
                    for field, value in [('delivery_offsets', offsets[task]), ('delivery_earliest', earliest[task])]:
                        r[field][oid] = min(r[field].get(oid, value), value)
                else:
                    lo, hi = earliest[task], windows[task][1]
                    while lo < hi:
                        mid = (lo+hi+1)//2
                        state = replay(problem, site, sequence, start, windows, forced=(task, mid))
                        if state is not None and state[1] <= horizon: lo = mid
                        else: hi = mid-1
                    r['pickup_latest'][oid] = max(r['pickup_latest'].get(oid, lo), lo)
    return records


def domains(problem):
    return {s: (c, enumerate_timed_route_universe(problem, c, service_envelopes=True))
            for s in problem.satellites for c in [route_context(problem, s)] if c['nodes']}


@pytest.mark.parametrize('seed', range(6))
def test_service_extrema_match_all_paths_and_independent_forced_time_search(seed):
    problem = nonmetric_problem(seed)
    for site, (_, universe) in domains(problem).items():
        assert universe['complete']
        observed = {}
        for group in universe['clock_groups']:
            assert group['complete']
            for r in group['columns']:
                observed[frozenset(r['tasks']), r['cutoff']] = {k: r[k] for k in (
                    'delivery_offsets', 'delivery_earliest', 'pickup_latest', 'return_earliest', 'latest_start_max')}
        assert observed == oracle(problem, site)


@pytest.mark.parametrize('seed', range(6))
def test_known_full_optimum_projects_to_service_model_and_bounds_are_safe(seed):
    gp = pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.solver import solve_exact
    problem = nonmetric_problem(seed)
    initial = solve_exact(problem, time_limit=5)
    assert initial['info']['proven_optimal'] and audit_solution(problem, initial)['valid']
    d = domains(problem)
    model, context = build_joint_inventory(problem, d)
    try:
        set_joint_projection(problem, context, initial, fix=True)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL and model.ObjVal <= initial['info']['objective']+1e-6
    finally: model.dispose()
    result = solve_joint_inventory(problem, d, deadline=time.perf_counter()+5, initial_result=initial)
    assert result['service_envelopes'] and not result['proven_optimal']
    assert result['bound'] <= initial['info']['objective']+1e-6


@pytest.mark.parametrize('returned', [29, 30, 31])
@pytest.mark.parametrize('cleaning', [0, 20])
def test_original_cutoff_cleaning_boundaries_remain_projectable(returned, cleaning):
    gp = pytest.importorskip('gurobipy')
    problem = fixture(pool=2, cleaning=cleaning)
    model, context = build_joint_inventory(problem, domains(problem))
    try:
        set_joint_projection(problem, context, hand_solution(problem, returned), fix=True)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
    finally: model.dispose()


def test_cost_dominated_path_still_contributes_better_delivery_clock():
    base = tradeoff_problem()
    orders = [replace(o, delivery_window=(0, 100 if o.id == 'a' else 39)) for o in base.orders]
    problem = MultiBatchInstance(base.network, orders, base.operating_policy)
    universe = enumerate_timed_route_universe(problem, route_context(problem, 'S'), service_envelopes=True)
    taskset = {('a', 'd'), ('b', 'd')}
    front = [r for g in universe['timing_groups'] for r in g['columns'] if set(r['column']['tasks']) == taskset]
    assert [(r['column']['cost'], r['latest_departure']) for r in front] == [(3, 38)]
    clock = next(r for g in universe['clock_groups'] for r in g['columns'] if set(r['tasks']) == taskset)
    assert clock['delivery_offsets']['a'] == 1
    assert clock['delivery_earliest']['a'] == 2
    assert front[0]['column']['starts'][1] == 22
    assert clock['return_earliest'] == 4


def test_service_guard_and_partial_enumeration():
    p = fixture(pool=2)
    for value in ['yes', 1, None]:
        with pytest.raises(ValueError, match='boolean'):
            enumerate_timed_route_universe(p, route_context(p, 'S'), service_envelopes=value)
        with pytest.raises(ValueError, match='boolean'):
            joint_route_inventory_certificate(p, service_envelopes=value)
    with pytest.raises(ValueError, match='requires'):
        joint_route_inventory_certificate(p, service_envelopes=True)
    u = enumerate_timed_route_universe(p, route_context(p, 'S'), service_envelopes=True, max_states=1)
    with pytest.raises(ValueError, match='complete'):
        build_joint_inventory(p, {'S': (route_context(p, 'S'), u)})


def test_public_service_export_is_separate_from_cost_and_time_columns(tmp_path):
    pytest.importorskip('gurobipy')
    import json
    p = fixture(pool=2)
    r = joint_route_inventory_certificate(p, initial_result=hand_solution(p), time_limit=5,
        joint_time_limit=1, recovery_time_limit=1, timing_envelopes=True, service_envelopes=True,
        diagnostics_dir=tmp_path)
    with gzip.open(tmp_path/'site_0_best_pricing_service.json.gz','rt') as stream: clock = json.load(stream)
    with gzip.open(tmp_path/'site_0_best_pricing.json.gz','rt') as stream: ordinary = json.load(stream)
    assert clock['complete'] and clock['clock_groups']
    assert 'clock_groups' not in ordinary and 'timing_groups' not in ordinary
    assert r['joint_certificate']['service_envelopes'] and r['bound'] <= r['upper_bound']


def congestion_problem():
    from models.routing.common.cycle import Order
    from models.routing.common.domain.instance import ProblemInstance
    from models.routing.common.domain.node import Depot, Satellite, Customer
    base = fixture(pool=2, cleaning=0)
    nodes = {'D': Depot('D', 0, 0), 'S': Satellite('S', 0, 0),
        'A': Customer('A',1,0,0,0,(0,125),(0,125),'S'),
        'B': Customer('B',2,0,0,0,(0,125),(0,125),'S')}
    travel = {a:{b:0 if a==b else 1 for b in nodes} for a in nodes}
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=1, E1_Qk=500, E1_Ck=500,
                 E2_Qv=1, E2_Cv=1, container_weight=1, container_volume=1)
    orders = [Order('a','A','S','b1',1,1,(0,10),(50,60),40),
              Order('b','B','S','b1',1,1,(0,10),(50,60),40)]
    return MultiBatchInstance(ProblemInstance(nodes,fleet,distance_matrix=travel,travel_time_matrix=travel),
                              orders,base.operating_policy)


def test_compulsory_intervals_raise_bound_when_cheap_long_trips_need_two_vehicles():
    gp = pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.solver import solve_exact
    problem = congestion_problem()
    initial = solve_exact(problem,time_limit=5)
    assert initial['info']['proven_optimal'] and initial['info']['objective'] == pytest.approx(10)
    plain = {s:(c,enumerate_timed_route_universe(problem,c)) for s in problem.satellites
             for c in [route_context(problem,s)] if c['nodes']}
    weak = solve_joint_inventory(problem,plain,deadline=time.perf_counter()+5,initial_result=initial)
    strong = solve_joint_inventory(problem,domains(problem),deadline=time.perf_counter()+5,initial_result=initial)
    assert weak['relaxation_objective'] == pytest.approx(8)
    assert strong['relaxation_objective'] == pytest.approx(10)
    assert strong['bound'] <= initial['info']['objective']
