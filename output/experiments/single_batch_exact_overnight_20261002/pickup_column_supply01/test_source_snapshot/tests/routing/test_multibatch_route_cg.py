"""Compare pricing and column generation with an independent trip enumeration."""
from itertools import permutations
import math
import random
import time

import pytest

from models.routing.common.cycle import MultiBatchInstance, Order
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Depot, Satellite, Customer
from models.routing.solvers.exact.multibatch_route_cg import dual_certificate, route_cover_certificate, site_route_cover
from models.routing.solvers.exact.multibatch_route_pricing import RoutePricer, TripPricingModel, evaluate_column, route_context
from tests.routing.test_multibatch_cycle import fixture, hand_solution
from tests.routing.test_multibatch_departure_loads import nonmetric_problem


def enumerate_trips(problem, site, supply_conditions=None):
    """Brute-force every elementary task sequence, with independent replay.

    Floyd-Warshall computes necessary time bounds independently of the
    Dijkstra helper used by pricing. Earliest scheduling is exact for a fixed
    sequence in this relaxed model, whose time requirements are lower bounds.
    """
    p, f = problem.operating_policy, problem.fleet
    locations = list(problem.network.nodes)
    shortest = {(a, b): problem.travel(a, b) for a in locations for b in locations}
    for mid in locations:
        for a in locations:
            for b in locations:
                shortest[a, b] = min(shortest[a, b], shortest[a, mid]+shortest[mid, b])
    orders = [o for o in problem.orders if o.satellite == site]
    tasks = [(o.id, kind) for o in orders for kind in ('d', 'p')]
    releases = {b.id: b.start+p.e1_load_time+shortest[problem.depot, site]+problem.service(site)
                +p.e2_dispatch_prep_time for b in p.batches}
    for condition in supply_conditions or []:
        bid = condition['batch']
        releases[bid] = max(releases[bid], condition['threshold']+p.e2_dispatch_prep_time)
    results = []
    for length in range(1, len(tasks)+1):
        for sequence in permutations(tasks, length):
            batch = {problem.order_map[oid].batch for oid, kind in sequence if kind == 'd'}
            if len(batch) > 1:
                continue
            clock = max(p.cycle_start, max((releases[b] for b in batch), default=p.cycle_start))
            load = [sum(problem.task(oid, kind)[i] for oid, kind in sequence if kind == 'd') for i in (1, 2)]
            capacity = [f.e2_weight_cap, f.e2_volume_cap]
            if any(v > c+1e-6 for v, c in zip(load, capacity)):
                continue
            previous, visited_d, cost, good = site, {}, 0.0, True
            for oid, kind in sequence:
                order, w, v = problem.task(oid, kind)
                earliest_d = max(order.delivery_window[0], releases[order.batch]+shortest[site, order.customer])
                low, high = order.delivery_window if kind == 'd' else order.pickup_window
                low = max(low, earliest_d if kind == 'd' else earliest_d+order.service_time+order.min_use_time)
                if kind == 'p':
                    high = min(high, p.batches[-1].cutoff-order.service_time-shortest[order.customer, site]-p.e2_return_unload_time)
                clock = max(clock+problem.travel(previous, order.customer), low)
                if kind == 'p' and (oid, 'd') in sequence:
                    if oid not in visited_d:
                        good = False
                        break
                    clock = max(clock, visited_d[oid]+order.service_time+order.min_use_time)
                if clock > high:
                    good = False
                    break
                if kind == 'd':
                    visited_d[oid] = clock
                for i, amount in enumerate((w, v)):
                    load[i] += amount*(1 if kind == 'p' else -1)
                if any(v < -1e-6 or v > c+1e-6 for v, c in zip(load, capacity)):
                    good = False
                    break
                cost += problem.distance(previous, order.customer)
                clock += order.service_time
                previous = order.customer
            if not good:
                continue
            returned = clock+problem.travel(previous, site)+p.e2_return_unload_time
            if returned+p.e2_turnaround_time > p.cycle_end:
                continue
            if any(kind == 'p' for _, kind in sequence) and returned > p.batches[-1].cutoff:
                continue
            results.append((sequence, cost+problem.distance(previous, site)))
    return results


def enumerated_lp(problem, site, columns):
    gp = pytest.importorskip('gurobipy')
    model = gp.Model()
    model.Params.OutputFlag = 0
    try:
        x = model.addVars(len(columns), obj={i: c for i, (_, c) in enumerate(columns)})
        for order in problem.orders:
            if order.satellite == site:
                for kind in ('d', 'p'):
                    model.addConstr(gp.quicksum(x[i] for i, (seq, _) in enumerate(columns) if (order.id, kind) in seq) == 1)
        model.addConstr(x.sum() <= problem.fleet.e2_per_sat_count*problem.operating_policy.e2_max_trips_per_vehicle)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        return model.ObjVal
    finally:
        model.dispose()


@pytest.mark.parametrize('seed', range(6))
@pytest.mark.parametrize('phase_one', [False, True])
@pytest.mark.parametrize('linear', [False, True])
def test_exact_trip_pricing_matches_all_elementary_sequences(seed, phase_one, linear):
    pytest.importorskip('gurobipy')
    problem = nonmetric_problem(seed)
    rng = random.Random(100+seed)
    for site in problem.satellites:
        context = route_context(problem, site)
        if not context['nodes']:
            continue
        oracle = enumerate_trips(problem, site)
        duals = {node['key']: rng.uniform(-5, 20) for node in context['nodes']}
        nu = -2.5
        expected = min((0 if phase_one else cost)-sum(duals[key] for key in sequence)-nu for sequence, cost in oracle)
        pricer = RoutePricer(problem, context, linear_links=linear)
        try:
            result = pricer.price(duals, nu, time_limit=5, phase_one=phase_one)
            assert result['optimal'] and result['reduced_cost'] == pytest.approx(expected, abs=1e-6)
            assert result['lower_bound'] <= expected+1e-7
            assert result['column']['tasks'] in {sequence for sequence, _ in oracle}
        finally:
            pricer.dispose()


@pytest.mark.parametrize('seed', range(6))
def test_column_generation_matches_fully_enumerated_relaxation_and_bounds_integer_m5(seed):
    pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.solver import solve_exact
    problem = nonmetric_problem(seed)
    result = route_cover_certificate(problem, time_limit=5)
    assert result['status'] == 'LP_CONVERGED'
    for row in result['sites']:
        oracle = enumerated_lp(problem, row['satellite'], enumerate_trips(problem, row['satellite']))
        assert oracle-1e-3 <= row['bound'] <= oracle+1e-6
        assert row['lp_converged']
    full = solve_exact(problem, time_limit=5)
    assert full['info']['proven_optimal'] and audit_solution(problem, full)['valid']
    assert result['bound'] <= full['info']['objective']+1e-6
    assert not result['proven_optimal'] and not result['proven_infeasible']


def detour_problem():
    base = fixture(pool=4)
    nodes = {'D': Depot('D', 0, 0), 'S': Satellite('S', 0, 0),
             'A': Customer('A', 1, 0, 0, 0, (0, 125), (0, 125), 'S'),
             'B': Customer('B', 2, 0, 0, 0, (0, 125), (0, 125), 'S')}
    arcs = {a: {b: (0 if a == b else 1) for b in nodes} for a in nodes}
    arcs['S']['A'] = arcs['A']['S'] = 30
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=1, E1_Qk=500, E1_Ck=500,
                 E2_Qv=4, E2_Cv=4, container_weight=1, container_volume=1)
    network = ProblemInstance(nodes, fleet, distance_matrix=arcs, travel_time_matrix=arcs)
    orders = [Order(oid, customer, 'S', 'b1', 1, 1, (0, 10), (0, 25), 0)
              for oid, customer in [('a', 'A'), ('b', 'B')]]
    return MultiBatchInstance(network, orders, base.operating_policy)


def test_phase_one_finds_detour_columns_without_a_feasible_singleton_cover():
    pytest.importorskip('gurobipy')
    problem = detour_problem()
    context = route_context(problem, 'S')
    assert evaluate_column(problem, context, [('a', 'd')]) is None
    assert evaluate_column(problem, context, [('a', 'p')]) is None
    result = route_cover_certificate(problem, time_limit=5)
    row = result['sites'][0]
    assert row['lp_converged'] and any(i['phase'] == 'feasibility' for i in row['iterations'])
    assert row['bound'] == pytest.approx(enumerated_lp(problem, 'S', enumerate_trips(problem, 'S')), abs=1e-3)


def test_dual_bound_formula_is_valid_for_arbitrary_multipliers():
    pytest.importorskip('gurobipy')
    problem, rng = detour_problem(), random.Random(87)
    columns = enumerate_trips(problem, 'S')
    optimal = enumerated_lp(problem, 'S', columns)
    keys = [n['key'] for n in route_context(problem, 'S')['nodes']]
    K = problem.fleet.e2_per_sat_count*problem.operating_policy.e2_max_trips_per_vehicle
    for _ in range(100):
        duals, nu = {key: rng.uniform(-30, 30) for key in keys}, rng.uniform(-10, 0)
        rho = min(cost-sum(duals[key] for key in seq)-nu for seq, cost in columns)
        assert dual_certificate(duals, nu, K, rho-rng.uniform(0, 5)) <= optimal+1e-6


def test_full_audited_trips_project_without_fixed_start_times_or_same_trip_pairing():
    pytest.importorskip('gurobipy')
    from dataclasses import replace
    from models.routing.common.cycle.state import empty_result
    base = fixture(pool=2)
    problem = MultiBatchInstance(base.network,
        [replace(base.orders[0], pickup_window=(71, 80)), base.orders[1]], base.operating_policy)
    problem.fleet.e2_weight_cap, problem.fleet.e2_volume_cap = 3, 2
    start = empty_result(problem, 'manual')
    start['e1_trips'] = [dict(trip_id='e1_b1', vehicle_id=0, batch_id='b1', departure=0,
        return_complete=20, events=[dict(kind='d', site_id='S', quantity=1, start=10)]),
        dict(trip_id='e1_b2', vehicle_id=0, batch_id='b2', departure=60, return_complete=100,
        events=[dict(kind='d', site_id='S', quantity=1, start=70), dict(kind='p', site_id='S', quantity=2, start=90)])]
    start['e2_trips'] = [dict(trip_id='first', vehicle_id=0, satellite_id='S', trip_index=0,
        departure=10, return_complete=12, events=[dict(kind='d', order_id='o1', quantity=1, start=11)]),
        dict(trip_id='second', vehicle_id=0, satellite_id='S', trip_index=1, departure=70,
        return_complete=74, events=[dict(kind='p', order_id='o1', quantity=1, start=71),
            dict(kind='d', order_id='o2', quantity=1, start=72), dict(kind='p', order_id='o2', quantity=1, start=73)])]
    assert audit_solution(problem, start)['valid']
    certificate = route_cover_certificate(problem, initial_result=start, time_limit=5)
    assert certificate['status'] == 'LP_CONVERGED'
    assert certificate['bound'] <= audit_solution(problem, start)['objective']+1e-6


def test_delivery_only_return_is_not_forced_before_final_pickup_cutoff():
    pytest.importorskip('gurobipy')
    from dataclasses import replace
    problem = detour_problem()
    indices = problem.network.node_index
    problem.network.time[indices['S']][indices['A']] = 1
    problem.network.time[indices['A']][indices['S']] = 110
    # Direct outbound arc is slow here; widen the delivery window to permit
    # a delivery-only trip that returns after cutoff, but before cycle end.
    problem = MultiBatchInstance(problem.network,
        [replace(o, delivery_window=(0, 40)) for o in problem.orders], problem.operating_policy)
    context = route_context(problem, 'S')
    column = evaluate_column(problem, context, [('a', 'd')])
    assert column is not None and column['return_complete'] > problem.operating_policy.batches[-1].cutoff
    pricer = TripPricingModel(problem, context)
    try:
        for i, node in enumerate(context['nodes']):
            pricer.model.addConstr(pricer.visit[i] == int(node['key'] == ('a', 'd')))
        priced = pricer.price({}, time_limit=5)
        assert priced['optimal'] and priced['column']['tasks'] == (('a', 'd'),)
    finally:
        pricer.dispose()


def test_phase_one_failure_does_not_claim_full_integer_infeasibility():
    pytest.importorskip('gurobipy')
    result = route_cover_certificate(fixture(pool=2, e2_max_trips_per_vehicle=1), time_limit=5)
    assert result['sites'][0]['status'] == 'RELAXATION_INFEASIBLE'
    assert not result['proven_infeasible'] and not result['proven_optimal']


def test_unfinished_master_objective_is_never_reported_as_a_bound(monkeypatch):
    pytest.importorskip('gurobipy')
    from types import SimpleNamespace
    from models.routing.solvers.exact import multibatch_route_cg as cg
    class Unresolved:
        def __init__(self, *args, **kwargs):
            self.model = SimpleNamespace(NumVars=1)
        def price(self, *args, **kwargs):
            time.sleep(.01)
            return dict(status=9, optimal=False, infeasible=False, lower_bound=-1000,
                        raw_lower_bound=-1000, margin=0, reduced_cost=None, column=None, runtime=.01)
        def dispose(self):
            pass
    monkeypatch.setattr(cg, 'RoutePricer', Unresolved)
    problem = fixture(pool=2)
    row = site_route_cover(problem, route_context(problem, 'S'), deadline=time.perf_counter()+.05,
                           initial_result=hand_solution(problem))
    assert not row['lp_converged'] and row['bound'] == 0
    assert row['iterations'] and row['iterations'][0]['master_objective'] > row['bound']


def test_total_budget_and_scope_validation():
    result = route_cover_certificate(fixture(pool=2), time_limit=1e-12)
    assert result['bound'] == 0 and result['status'] == 'TIME_LIMIT_CERTIFICATE'
    assert not result['proven_optimal']
    for value in (0, -1, math.inf, math.nan):
        with pytest.raises(ValueError, match='time_limit'):
            route_cover_certificate(fixture(pool=2), time_limit=value)
    with pytest.raises(ValueError, match='multibatch M5'):
        route_cover_certificate(fixture(one_batch=True))
    with pytest.raises(ValueError, match='independent audit'):
        route_cover_certificate(fixture(pool=2), initial_result={})


def test_numbered_bound_entry_exports_and_saves_replayable_pricing_model(tmp_path):
    gp = pytest.importorskip('gurobipy')
    from models.routing.variants.model_5.exact import route_cover_certificate as public_bound
    result = public_bound(fixture(pool=2), time_limit=5, diagnostics_dir=tmp_path)
    assert result['status'] == 'LP_CONVERGED' and not result['proven_optimal']
    site = result['sites'][0]
    witness = site['iterations'][site['best_witness']]
    paths = list(tmp_path.glob('site_0_best_pricing_group_*.mps'))
    assert paths
    for path in paths:
        replay = gp.read(str(path))
        try:
            replay.Params.OutputFlag = 0
            replay.optimize()
            if replay.Status == gp.GRB.OPTIMAL:
                assert replay.ObjVal >= witness['pricing']['lower_bound']-1e-6
            else:
                assert replay.Status == gp.GRB.INFEASIBLE
        finally:
            replay.dispose()
