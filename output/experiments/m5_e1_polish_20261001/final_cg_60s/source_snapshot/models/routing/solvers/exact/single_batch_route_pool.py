"""Audited M1/M2 incumbents from CG columns; no global proof from this pool.

The restricted integer master jointly selects E1 tours, assignments, E2
routes and physical vehicle schedules. Pool infeasibility and bounds are
discarded. Only independently audited improving incumbents are returned.
"""
from __future__ import annotations

from collections import defaultdict
import math
import time

from models.routing.common.cycle.assignment import assigned_problem, lift_result
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.state import empty_result


def replay_delivery(problem, site, tasks, departure):
    """Actual pure-delivery trip, without relaxed supply or tightened windows."""
    p, f, batch = problem.operating_policy, problem.fleet, problem.operating_policy.batches[0]
    if not tasks or len(set(tasks)) != len(tasks):
        return None
    orders = [problem.order_map[k] for k in tasks]
    if (math.fsum(o.goods_weight+o.count*f.container_weight for o in orders) > f.e2_weight_cap+1e-6 or
            sum(o.count for o in orders)*f.container_volume > f.e2_volume_cap+1e-6):
        return None
    now, previous, events = departure, site, []
    for o in orders:
        now = max(now+problem.travel(previous, o.customer), o.delivery_window[0])
        if now > min(o.delivery_window[1], batch.cutoff-o.service_time):
            return None
        events.append(dict(order_id=o.id, kind='d', start=now, quantity=o.count))
        now, previous = now+o.service_time, o.customer
    end = now+problem.travel(previous, site)+p.e2_return_unload_time
    if end+p.e2_turnaround_time > p.cycle_end:
        return None
    return dict(departure=departure, return_complete=end, events=events)


def route_profile(problem, site, tasks, supply):
    """For d >= release, earliest finish is max(d+duration, first_finish).

    Waiting at fixed window lower bounds gives this max-plus representation.
    Latest departure retains EVERY window upper bound and terminal turnaround.
    No triangle inequality or positive-duration assumption is used.
    """
    p, batch = problem.operating_policy, problem.operating_policy.batches[0]
    release = max(p.cycle_start, supply)+p.e2_dispatch_prep_time
    first = replay_delivery(problem, site, tasks, release)
    if first is None:
        return None
    duration, previous, latest = 0, site, p.cycle_end
    for key in tasks:
        o = problem.order_map[key]
        duration += problem.travel(previous, o.customer)
        latest = min(latest, o.delivery_window[1]-duration, batch.cutoff-o.service_time-duration)
        duration += o.service_time
        previous = o.customer
    duration += problem.travel(previous, site)+p.e2_return_unload_time
    latest = min(latest, p.cycle_end-p.e2_turnaround_time-duration)
    return dict(release=release, latest=latest, duration=duration, first_finish=first['return_complete'])


def _physical_slots(master, pool, deadline, slot_limit):
    """Choose a route for each finite vehicle slot, with exact route timing.

    The optional smaller slot limit restricts ONLY this primal search. The
    complete original MILP retains the policy's full trip cap.
    """
    import gurobipy as gp
    from gurobipy import GRB
    p, f, m = master.problem.operating_policy, master.problem.fleet, master.model
    by_site = defaultdict(list)
    for i, (s, c) in enumerate(master.columns):
        by_site[s].append(i)
    placement, departures, ends, used = {}, {}, {}, {}
    by_column = defaultdict(list)
    end_lower, end_upper = p.cycle_start, p.cycle_end-p.e2_turnaround_time
    depart_lower, depart_upper = p.cycle_start+p.e2_dispatch_prep_time, end_upper
    for site, indices in by_site.items():
        supply = gp.quicksum(t['supply'][site]*master.alpha[i] for i, t in enumerate(pool['tours']) if site in t['supply'])
        rows = {i: route_profile(master.problem, site, master.columns[i][1]['tasks'], p.cycle_start) for i in indices}
        slots = range(min(p.e2_max_trips_per_vehicle, slot_limit, len(indices)))
        vehicles = range(min(f.e2_per_sat_count, len(indices)))
        for v in vehicles:
            for k in slots:
                if time.perf_counter() >= deadline:
                    raise TimeoutError('Physical route-pool slots reached their budget')
                key = site, v, k
                departures[key] = d = m.addVar(vtype=GRB.INTEGER, lb=depart_lower, ub=depart_upper)
                ends[key] = e = m.addVar(vtype=GRB.INTEGER, lb=end_lower, ub=end_upper)
                used[key] = u = m.addVar(vtype=GRB.BINARY)
                m.addConstr(u <= master.active[site])
                m.addGenConstrIndicator(u, True, d-supply >= p.e2_dispatch_prep_time)
                if k:
                    m.addConstr(u <= used[site, v, k-1])
                    m.addGenConstrIndicator(u, True, d-ends[site, v, k-1]
                        >= p.e2_turnaround_time+p.e2_dispatch_prep_time)
                choices = []
                for i in indices:
                    r = rows[i]
                    if r is None:
                        master.variables[i].UB = 0
                        continue
                    y = placement[i, v, k] = m.addVar(vtype=GRB.BINARY)
                    choices.append(y)
                    by_column[i].append(y)
                    m.addConstr(d <= r['latest']+max(0, depart_upper-r['latest'])*(1-y))
                    m.addConstr(e >= d+r['duration']-max(0, depart_upper+r['duration']-end_lower)*(1-y))
                    m.addConstr(e >= r['first_finish']-max(0, r['first_finish']-end_lower)*(1-y))
                m.addConstr(gp.quicksum(choices) == u)
            if v:
                m.addConstr(gp.quicksum(used[site, v, k] for k in slots)
                    <= gp.quicksum(used[site, v-1, k] for k in slots))
        for i in indices:
            m.addConstr(gp.quicksum(by_column[i]) == master.variables[i])
    return placement, departures, ends, used


def route_pool_incumbent(problem, pool, *, initial_result=None, deadline, seed=42, slot_limit=2):
    """Select, physically schedule and independently audit pool incumbents.

    Physical route slots apply ONLY to this primal pool.
    The integer pool objective and status are never returned as global bounds.
    """
    from gurobipy import GRB
    from .single_batch_columns import DeliveryMaster
    if problem.model_number not in (1, 2) or problem.reusable or len(problem.operating_policy.batches) != 1:
        raise ValueError('The delivery primal pool supports only single-batch M1/M2')
    if isinstance(slot_limit, bool) or not isinstance(slot_limit, int) or slot_limit < 1:
        raise ValueError('Primal slot limit must be a positive integer')
    begun, master, best = time.perf_counter(), None, None
    initial_audit = audit_solution(problem, initial_result) if initial_result is not None else None
    ceiling = initial_audit['objective'] if initial_audit and initial_audit['valid'] else math.inf
    stats = dict(status='TIME_LIMIT', proof_scope='restricted_route_pool_primal_only',
        global_proof=False, attempts=0, audited_candidates=0, rejected_schedules=0,
        initial_objective=ceiling if math.isfinite(ceiling) else None,
        objective=None, improved=False,
        slots_per_vehicle=min(slot_limit, problem.operating_policy.e2_max_trips_per_vehicle))
    try:
        if time.perf_counter() >= deadline:
            return None, {**stats, 'runtime': time.perf_counter()-begun}
        master = DeliveryMaster(problem, pool['domains'], pool['contexts'], pool['tours'], deadline=deadline)
        for site, c in pool['columns']:
            if time.perf_counter() >= deadline:
                raise TimeoutError('Primal pool construction reached its budget')
            master.add(site, c)
        master.phase_two()
        m = master.model
        binary = list(master.assignment.values())+list(master.active.values())+master.alpha+master.variables
        for variable in binary:
            variable.VType, variable.UB = GRB.BINARY, 1
        m.Params.Method = -1
        m.Params.IntFeasTol = 1e-9
        m.Params.MIPGap = m.Params.MIPGapAbs = 0
        m.Params.Seed = seed
        placement, departures, ends, used = _physical_slots(master, pool, deadline, slot_limit)
        if initial_audit and initial_audit['valid']:
            owner = initial_result.get('assignment', {o.customer: o.satellite for o in problem.orders})
            active = set(owner.values())
            routes = {(t['satellite_id'], tuple(e['order_id'] for e in t['events'])) for t in initial_result['e2_trips']}
            tours = {tuple(e['site_id'] for e in t['events']) for t in initial_result['e1_trips']}
            for (c, s), v in master.assignment.items():
                v.Start = int(owner[c] == s)
            for s, v in master.active.items():
                v.Start = int(s in active)
            for tour, v in zip(pool['tours'], master.alpha):
                v.Start = int(tour['sites'] in tours)
            for (s, c), v in zip(master.columns, master.variables):
                v.Start = int((s, c['tasks']) in routes)
            initial_trips = {(t['satellite_id'], t['vehicle_id'], t['trip_index']): t for t in initial_result['e2_trips']}
            for (i, vehicle, slot), y in placement.items():
                s, c = master.columns[i]
                t = initial_trips.get((s, vehicle, slot))
                y.Start = int(t is not None and tuple(e['order_id'] for e in t['events']) == c['tasks'])
            for key, u in used.items():
                t = initial_trips.get(key)
                u.Start = int(t is not None)
                if t is not None:
                    departures[key].Start, ends[key].Start = t['departure'], t['return_complete']
        m.update()
        stats['build_runtime'] = time.perf_counter()-begun
        stats.update(placement_variables=len(placement), physical_slots=len(used))
        if time.perf_counter() < deadline:
            left = deadline-time.perf_counter()
            # Physical scheduling is already part of this MIP. Reserve replay.
            m.Params.TimeLimit = max(0.0, left-.02)
            m.optimize()
            stats['pool_status'] = int(m.Status)
            if not m.SolCount:
                stats['status'] = 'NO_IMPROVEMENT_IN_POOL' if m.Status == GRB.INFEASIBLE else 'TIME_LIMIT_POOL'
                return None, {**stats, 'runtime': time.perf_counter()-begun}
            stats['attempts'] += 1
            inbound = [i for i, v in enumerate(master.alpha) if v.X > .5]
            owner = {c: s for (c, s), v in master.assignment.items() if v.X > .5}
            fixed = assigned_problem(problem, owner) if problem.free_assignment else problem
            result = empty_result(fixed, 'exact')
            p, batch = fixed.operating_policy, fixed.operating_policy.batches[0]
            for vehicle, i in enumerate(inbound):
                now, previous, events = batch.start+p.e1_load_time, fixed.depot, []
                for s in pool['tours'][i]['sites']:
                    now += fixed.travel(previous, s)
                    events.append(dict(kind='d', site_id=s, start=now, quantity=fixed.demands[s, batch.id]))
                    now += fixed.service(s)
                    previous = s
                result['e1_trips'].append(dict(trip_id=f'pool_e1_{vehicle}', batch_id=batch.id,
                    vehicle_id=vehicle, departure=batch.start+p.e1_load_time,
                    return_complete=now+fixed.travel(previous, fixed.depot)+p.depot_return_unload_time, events=events))
            valid_schedule = True
            for (i, vehicle, slot), y in placement.items():
                if y.X <= .5:
                    continue
                s, column = master.columns[i]
                timed = replay_delivery(fixed, s, column['tasks'], int(round(departures[s, vehicle, slot].X)))
                if timed is None:
                    valid_schedule = False
                    break
                result['e2_trips'].append(dict(trip_id=f'pool_e2_{s}_{vehicle}_{slot}',
                    satellite_id=s, vehicle_id=vehicle, trip_index=slot, **timed))
            if valid_schedule:
                if problem.free_assignment:
                    result = lift_result(problem, result, owner)
                audit = audit_solution(problem, result)
                if not audit['valid']:
                    raise RuntimeError('Physically scheduled pool candidate failed independent audit: '+str(audit['errors']))
                stats['audited_candidates'] += 1
                result.update(audit=audit, cutoff_snapshots=audit['cutoff_snapshots'], depot_releases=audit['depot_releases'])
                result['info'].update(status='FEASIBLE', objective=audit['objective'],
                    incumbent_source='audited_CG_route_pool', proof_scope='feasible_schedule_only')
                if audit['objective'] < ceiling-1e-6:
                    best, ceiling = result, audit['objective']
                    stats.update(status='IMPROVED', objective=ceiling, improved=True)
                else:
                    stats.update(status='UNCHANGED_INCUMBENT', objective=audit['objective'])
            else:
                stats['rejected_schedules'] += 1
    except TimeoutError as error:
        stats.update(status='TIME_LIMIT_BUILD', limitation=str(error))
    finally:
        if master is not None:
            master.dispose()
    stats['runtime'] = time.perf_counter()-begun
    return best, stats
