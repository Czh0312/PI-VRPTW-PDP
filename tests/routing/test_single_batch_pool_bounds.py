"""Independent exhaustive route/vehicle/tour checks for primal time bounds."""
from copy import deepcopy
from dataclasses import replace
import itertools
import math
import time

import pytest

from models.common.cycle import MultiBatchInstance
from models.common.cycle.audit import audit_solution
from models.common.cycle.state import empty_result
from models.routing.model_1.exact.single_batch_columns import DeliveryMaster, solve_with_route_bound
from models.routing.model_1.exact.single_batch_pool_master import DeliveryPoolMaster
from models.routing.model_1.exact.single_batch_route_pool import _physical_slots, route_pool_incumbent
from tests.routing.test_single_batch_route_pool import full_pool, physical_case, raw_trip, schedule_oracle
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny


def build(p, pool, compact, tight, aggregate_time=False):
    klass = DeliveryPoolMaster if compact else DeliveryMaster
    master = klass(p, pool['domains'], pool['contexts'], pool['tours'])
    for site, column in pool['columns']:
        master.add(site, column)
    master.phase_two()
    for v in list(master.assignment.values())+list(master.active.values())+master.alpha+master.variables:
        v.VType, v.UB = 'B', 1
    slots = _physical_slots(master, pool, time.perf_counter()+10, p.operating_policy.e2_max_trips_per_vehicle,
                            tight=tight, aggregate_time=aggregate_time)
    master.model.update()
    master.intrinsically_blocked = {i for i, v in enumerate(master.variables) if v.UB == 0}
    return master, slots


def vehicle_orders(keys):
    # Physical vehicle labels canonicalized only by counts, as in the pool.
    for assignment in itertools.product(range(2), repeat=len(keys)):
        rows = [[key for key, v in zip(keys, assignment) if v == vehicle] for vehicle in range(2)]
        if len(rows[0]) < len(rows[1]) or len(rows[0]) > 2:
            continue
        yield from itertools.product(*(list(itertools.permutations(row)) for row in rows))


def raw_schedule(p, tour, schedules):
    """Raw E1 and all integer E2 departures; no bound/profile/replay helper."""
    policy, batch = p.operating_policy, p.operating_policy.batches[0]
    now, previous, supplies = batch.start+policy.e1_load_time, p.depot, {}
    for site in tour:
        now += p.travel(previous, site)+p.service(site)
        supplies[site], previous = now, site
    if now+p.travel(previous, p.depot)+policy.depot_return_unload_time > policy.cycle_end:
        return False
    for site, rows in schedules.items():
        for row in rows:
            ready = policy.cycle_start
            for key in row:
                for departure in range(policy.cycle_start, policy.cycle_end+1):
                    if departure-policy.e2_dispatch_prep_time < max(ready, supplies[site]):
                        continue
                    finish = raw_trip(p, site, (key,), departure)
                    if finish is not None:
                        ready = finish+policy.e2_turnaround_time
                        break
                else:
                    return False
    return True


@pytest.mark.parametrize('model', [1, 2])
@pytest.mark.parametrize('compact', [False, True])
@pytest.mark.parametrize('scenario', ['directed', 'waiting', 'zero'])
def test_every_fixed_assignment_tour_and_vehicle_sequence_keeps_feasibility(model, compact, scenario):
    gp = pytest.importorskip('gurobipy')
    base = tiny_problem(model, 5)
    base.fleet.e2_per_sat_count = 2
    base.fleet.e2_weight_cap = 2  # Exactly one order per trip, including packaging.
    policy = replace(base.operating_policy, e2_max_trips_per_vehicle=2,
                     e2_dispatch_prep_time=2, e2_turnaround_time=3)
    window = (18, 23) if scenario == 'waiting' else (0, 45)
    p = MultiBatchInstance(base.network, [replace(o, delivery_window=window) for o in base.orders], policy, model=model)
    if scenario == 'zero':
        for key in p._travel:
            p._travel[key] = 0
    pool = full_pool(p)
    masters = [build(p, pool, compact, tight, aggregate) for tight, aggregate in itertools.product((False, True), repeat=2)]
    checked = 0
    try:
        customers = [o.customer for o in p.orders]
        owners = itertools.product(p.satellites, repeat=len(customers)) if model == 2 else [tuple(o.satellite for o in p.orders)]
        for sites in owners:
            owner = dict(zip(customers, sites))
            active = sorted(set(sites))
            keys = {s: [o.id for o in p.orders if owner[o.customer] == s] for s in active}
            for tour in itertools.permutations(active):
                for schedules_tuple in itertools.product(*(list(vehicle_orders(keys[s])) for s in active)):
                    schedules = dict(zip(active, schedules_tuple))
                    oracle = raw_schedule(p, tour, schedules)
                    desired = {(s, (key,), v, k) for s, rows in schedules.items()
                               for v, row in enumerate(rows) for k, key in enumerate(row)}
                    for master, (placements, _, _, _) in masters:
                        selected = {i for i, (s, c) in enumerate(master.columns) if any(s == a and c['tasks'] == b for a, b, _, _ in desired)}
                        available = {(master.columns[i][0], master.columns[i][1]['tasks'], v, k) for i, v, k in placements}
                        if not desired <= available or not any(t['sites'] == tour for t in pool['tours']):
                            assert not oracle
                            continue
                        for (c, s), var in master.assignment.items():
                            var.LB = var.UB = int(owner[c] == s)
                        for s, var in master.active.items():
                            var.LB = var.UB = int(s in active)
                        for t, var in zip(pool['tours'], master.alpha):
                            var.LB = var.UB = int(t['sites'] == tour)
                        if selected & master.intrinsically_blocked:
                            assert not oracle
                            continue
                        for i, var in enumerate(master.variables):
                            var.LB = var.UB = int(i in selected)
                        for (i, v, k), var in placements.items():
                            s, c = master.columns[i]
                            var.LB = var.UB = int((s, c['tasks'], v, k) in desired)
                        master.model.optimize()
                        assert (master.model.Status == gp.GRB.OPTIMAL) == oracle, (owner, tour, schedules)
                    checked += 1
        assert checked == (8 if model == 1 else 60)
    finally:
        for master, _ in masters:
            master.dispose()


@pytest.mark.parametrize('model', [1, 2])
@pytest.mark.parametrize('seed', range(8))
@pytest.mark.parametrize('compact', [False, True])
@pytest.mark.parametrize('aggregate_time', [False, True])
def test_tight_full_pool_matches_independent_original_integer_optimum(model, seed, compact, aggregate_time):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model, seed)
    oracle = enumerate_tiny(p)
    candidate, stats = route_pool_incumbent(p, full_pool(p), deadline=time.perf_counter()+2,
        compact=compact, tight=True, aggregate_time=aggregate_time)
    assert stats['slot_bounds']['enabled'] and not stats['global_proof']
    assert stats['slot_bounds']['aggregate_time'] is aggregate_time
    assert (candidate is not None) == math.isfinite(oracle)
    if candidate:
        assert candidate['info']['objective'] == pytest.approx(oracle)
        assert audit_solution(p, candidate)['valid'] and candidate['audit']['periodic_replay']['valid']
        assert candidate['info']['best_bound'] is None and not candidate['info']['proven_optimal']


@pytest.mark.parametrize('service,window,prep,turn', [(12,(0,30),3,4), (20,(0,30),3,4), (0,(0,0),0,0), (12,(15,30),0,0)])
@pytest.mark.parametrize('vehicles', [1, 2])
def test_tight_multi_trip_duplicate_address_and_zero_times(service, window, prep, turn, vehicles):
    pytest.importorskip('gurobipy')
    p = physical_case(vehicles=vehicles, service=service, window=window, prep=prep, turn=turn)
    result, stats = route_pool_incumbent(p, full_pool(p), deadline=time.perf_counter()+2, tight=True)
    assert (result is not None) == schedule_oracle(p, [('a',),('b',)], 0), stats


def test_impossible_later_slot_is_removed_without_disabling_its_first_slot_route():
    pytest.importorskip('gurobipy')
    p = physical_case(vehicles=2, service=20)
    pool = full_pool(p)
    master, (placements, _, _, used) = build(p, pool, True, True)
    try:
        assert set(k for _, _, k in used) == {0}
        assert all(v.UB == 1 for v in master.variables)
        assert len(placements) == 4 and master.slot_bound_stats['removed_slots'] == 2
    finally:
        master.dispose()
    result, _ = route_pool_incumbent(p, pool, deadline=time.perf_counter()+2, tight=True)
    assert result is not None and audit_solution(p, result)['valid']


@pytest.mark.parametrize('compact', [False, True])
@pytest.mark.parametrize('aggregate_time', [False, True])
def test_actual_start_return_slack_is_repaired_without_changing_departures(monkeypatch, compact, aggregate_time):
    gp = pytest.importorskip('gurobipy')
    p = physical_case(vehicles=1, service=1, prep=2, turn=1)
    result = empty_result(p, 'independent_start')
    result['e1_trips'] = [dict(trip_id='in', vehicle_id=0, batch_id='b1', departure=0,
        return_complete=0, events=[dict(site_id='S', kind='d', quantity=2, start=0)])]
    for key, slot, departure, finish in [('a',0,3,6), ('b',1,12,55)]:
        result['e2_trips'].append(dict(trip_id=key, satellite_id='S', vehicle_id=0,
            trip_index=slot, departure=departure, return_complete=finish,
            events=[dict(order_id=key, kind='d', quantity=1, start=departure)]))
    assert audit_solution(p, result)['valid']
    before, captured = deepcopy(result), []
    monkeypatch.setattr(DeliveryPoolMaster if compact else DeliveryMaster, 'dispose', lambda master: captured.append(master))
    try:
        _, stats = route_pool_incumbent(p, full_pool(p), initial_result=result,
            deadline=time.perf_counter()+2, compact=compact, tight=True, aggregate_time=aggregate_time)
        assert stats['start_returns_repaired'] == 2 and result == before
        master = captured[0]
        starts = [v.Start for v in master.model.getVars() if v.VType == 'I' and abs(v.Start) < 1e80]
        assert sorted(starts) == [3,4,12,13]
        for var in master.model.getVars():
            if abs(var.Start) < 1e80:
                assert var.LB <= var.Start <= var.UB
                var.LB = var.UB = var.Start
        master.model.optimize()
        assert master.model.Status == gp.GRB.OPTIMAL
    finally:
        for master in captured:
            master.model.dispose()


def test_nonmetric_infeasible_singleton_does_not_remove_feasible_extension():
    pytest.importorskip('gurobipy')
    p = tiny_problem(1, 0)
    # Fixed assignments to the same site; S -> a -> S impossible, a -> b -> S feasible.
    p = MultiBatchInstance(p.network, [replace(o, satellite='S') for o in p.orders], p.operating_policy, model=1)
    p._travel[p.orders[0].customer, 'S'] = 1000
    result, _ = route_pool_incumbent(p, full_pool(p), deadline=time.perf_counter()+2, tight=True)
    assert result is not None and audit_solution(p, result)['valid']
    assert len(result['e2_trips']) == 1 and len(result['e2_trips'][0]['events']) == 2


@pytest.mark.parametrize('aggregate_time', [False, True])
def test_tight_restricted_pool_never_transfers_infeasibility_to_full_kernel(aggregate_time):
    pytest.importorskip('gurobipy')
    base = physical_case(service=0, prep=0, turn=0)
    p = MultiBatchInstance(base.network, list(base.orders)+[replace(base.orders[0], id='c')],
        replace(base.operating_policy, e2_max_trips_per_vehicle=3), model=1)
    for key in p._travel:
        p._travel[key] = 0
    result = solve_with_route_bound(p, time_limit=3, root_time=1, pool_time=1,
        pool_compact=True, pool_tight=True, pool_aggregate_time=aggregate_time)
    assert result['info']['proven_optimal'] and audit_solution(p, result)['valid']
    assert not result['info']['route_pool_primal']['global_proof'] and len(result['e2_trips']) == 3


@pytest.mark.parametrize('model', [3, 4])
def test_tight_delivery_pool_still_rejects_pickups(model):
    with pytest.raises(ValueError, match='M1/M2'):
        route_pool_incumbent(tiny_problem(model, 1), {}, deadline=time.perf_counter()+1, tight=True, aggregate_time=True)


@pytest.mark.parametrize('tight', [False, True])
@pytest.mark.parametrize('pool_cost', [0, 3, 20])
@pytest.mark.parametrize('aggregate_time', [False, True])
def test_tight_option_uses_only_remaining_total_budget(monkeypatch, tight, pool_cost, aggregate_time):
    from models.routing.model_1.exact import single_batch_columns as cg
    from models.routing.model_1.exact import single_batch_route_pool as primal
    from models.routing.model_1.exact import engine as solver
    from tests.routing.test_single_batch_schedule_start import delayed_three_trip_start
    p, initial = delayed_three_trip_start()
    now, seen = [0.0], {}
    def root(*args, **kwargs):
        assert 'tight' not in kwargs and 'pool_tight' not in kwargs
        assert 'aggregate_time' not in kwargs and 'pool_aggregate_time' not in kwargs
        now[0] = 10
        return dict(bound=0, proven_infeasible=False,
            proof_scope='B1_delivery_route_relaxation', _route_pool={})
    def pool(*args, **kwargs):
        assert kwargs['tight'] is tight and kwargs['deadline'] == 15
        assert kwargs['aggregate_time'] is aggregate_time
        now[0] += pool_cost
        return None, dict(global_proof=False)
    def full(*args, **kwargs):
        assert kwargs['initial_result'] == initial
        seen['full_budget'] = kwargs['time_limit']
        now[0] += kwargs['time_limit']
        return deepcopy(initial)
    monkeypatch.setattr(cg.time, 'perf_counter', lambda: now[0])
    monkeypatch.setattr(cg, 'root_route_bound', root)
    monkeypatch.setattr(primal, 'route_pool_incumbent', pool)
    monkeypatch.setattr(solver, 'solve_exact', full)
    result = cg.solve_with_route_bound(p, time_limit=30, root_time=10, pool_time=5,
        initial_result=initial, pool_tight=tight, pool_aggregate_time=aggregate_time)
    assert now[0] == 30 and result['info']['runtime'] == 30
    assert audit_solution(p, result)['valid'] and not result['info']['proven_optimal']
    if pool_cost == 20:
        assert 'full_budget' not in seen and result['e2_trips'] == initial['e2_trips']
    else:
        assert seen['full_budget'] == 20-pool_cost
