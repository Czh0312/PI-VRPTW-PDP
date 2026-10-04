"""Audited M1/M2 incumbents from CG columns; no global proof from this pool.

The restricted integer master selects E1 tours, assignments and E2 routes.
Actual supply compatibility is enforced here, then selected E2 routes are
scheduled on the physical fleet. Pool infeasibility and bounds are discarded.
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


def schedule_routes(problem, site, columns, supply, *, deadline, seed=42, greedy=True):
    """Greedy insertion, then an exact small scheduling MIP if needed.

    Returns (trips or None, status). Even INFEASIBLE concerns only this fixed
    selected route set, never the original routing problem or the CG bound.
    """
    import gurobipy as gp
    from gurobipy import GRB
    p, f = problem.operating_policy, problem.fleet
    n, count, cap = len(columns), f.e2_per_sat_count, p.e2_max_trips_per_vehicle
    if not n:
        return [], 'EMPTY'
    if n > count*cap:
        return None, 'INFEASIBLE_TRIP_COUNT'
    profiles = [route_profile(problem, site, c['tasks'], supply) for c in columns]
    if any(row is None for row in profiles):
        return None, 'INFEASIBLE_SUPPLY'

    def rows_to_trips(rows, departures=None):
        trips = []
        for vehicle, row in enumerate(rows):
            ready = p.cycle_start
            for slot, j in enumerate(row):
                depart = max(ready, supply)+p.e2_dispatch_prep_time
                if departures is not None:
                    depart = max(depart, departures[j])
                timed = replay_delivery(problem, site, columns[j]['tasks'], depart)
                if timed is None:
                    return None
                trips.append(dict(trip_id=f'pool_e2_{site}_{vehicle}_{slot}', satellite_id=site,
                    vehicle_id=vehicle, trip_index=slot, **timed))
                ready = timed['return_complete']+p.e2_turnaround_time
        return trips

    # Inserting a route also reschedules all later preparations on that vehicle.
    rows = [[] for _ in range(min(count, n))]
    for j in sorted(range(n), key=lambda k: (profiles[k]['latest'], profiles[k]['first_finish'], k)):
        if not greedy:
            break
        best = None
        for vehicle, row in enumerate(rows):
            if len(row) >= cap:
                continue
            for position in range(len(row)+1):
                if time.perf_counter() >= deadline:
                    return None, 'TIME_LIMIT'
                trial = [list(r) for r in rows]
                trial[vehicle].insert(position, j)
                trips = rows_to_trips(trial)
                if trips is not None:
                    score = sum(t['return_complete'] for t in trips)
                    if best is None or score < best[0]:
                        best = score, trial
        if best is None:
            break
        rows = best[1]
    else:
        return rows_to_trips(rows), 'GREEDY_FEASIBLE'
    if time.perf_counter() >= deadline:
        return None, 'TIME_LIMIT'

    model = gp.Model('B1_selected_route_physical_schedule')
    try:
        model.Params.OutputFlag = 0
        model.Params.Threads = 1
        model.Params.Seed = seed
        model.Params.IntFeasTol = 1e-9
        model.Params.MIPGap = model.Params.MIPGapAbs = 0
        vehicles = range(min(count, n))
        depart = [model.addVar(vtype=GRB.INTEGER, lb=r['release'], ub=r['latest']) for r in profiles]
        end = [model.addVar(vtype=GRB.INTEGER, lb=r['first_finish'],
                           ub=p.cycle_end-p.e2_turnaround_time, obj=1) for r in profiles]
        assigned = {(j, v): model.addVar(vtype=GRB.BINARY) for j in range(n) for v in vehicles}
        for j, r in enumerate(profiles):
            model.addConstr(end[j] >= depart[j]+r['duration'])
            model.addConstr(gp.quicksum(assigned[j, v] for v in vehicles) == 1)
        for v in vehicles:
            model.addConstr(gp.quicksum(assigned[j, v] for j in range(n)) <= cap)
        model.addConstr(assigned[0, 0] == 1)  # Identical physical vehicles.
        gap = p.e2_turnaround_time+p.e2_dispatch_prep_time
        big = p.cycle_end-p.cycle_start+gap+1
        for i in range(n):
            for j in range(i+1, n):
                if time.perf_counter() >= deadline:
                    return None, 'TIME_LIMIT_BUILD'
                before = model.addVar(vtype=GRB.BINARY)
                for v in vehicles:
                    absent = 2-assigned[i, v]-assigned[j, v]
                    model.addConstr(depart[j] >= end[i]+gap-big*(1-before+absent))
                    model.addConstr(depart[i] >= end[j]+gap-big*(before+absent))
        model.Params.TimeLimit = max(0.0, deadline-time.perf_counter())
        model.optimize()
        status = 'INFEASIBLE_SCHEDULE' if model.Status == GRB.INFEASIBLE else 'TIME_LIMIT_SCHEDULE'
        if not model.SolCount:
            return None, status
        departures = [int(round(v.X)) for v in depart]
        rows = [[j for j in range(n) if assigned[j, v].X > .5] for v in vehicles]
        for row in rows:
            row.sort(key=lambda j: (departures[j], int(round(end[j].X)), j))
        trips = rows_to_trips(rows, departures)
        return trips, 'MIP_FEASIBLE' if trips is not None else 'NUMERICAL_SCHEDULE_REJECTED'
    finally:
        model.dispose()


def route_pool_incumbent(problem, pool, *, initial_result=None, deadline, seed=42, attempts=12):
    """Select, physically schedule and independently audit pool incumbents.

    Safe supply exclusions and no-good rows apply ONLY to this primal pool.
    The integer pool objective and status are never returned as global bounds.
    """
    import gurobipy as gp
    from gurobipy import GRB
    from .single_batch_columns import DeliveryMaster
    if problem.model_number not in (1, 2) or problem.reusable or len(problem.operating_policy.batches) != 1:
        raise ValueError('The delivery primal pool supports only single-batch M1/M2')
    begun, master, best = time.perf_counter(), None, None
    initial_audit = audit_solution(problem, initial_result) if initial_result is not None else None
    ceiling = initial_audit['objective'] if initial_audit and initial_audit['valid'] else math.inf
    stats = dict(status='TIME_LIMIT', proof_scope='restricted_route_pool_primal_only',
        global_proof=False, attempts=0, audited_candidates=0, rejected_schedules=0,
        supply_exclusions=0, initial_objective=ceiling if math.isfinite(ceiling) else None,
        objective=None, improved=False, scheduling=[])
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
        for i, (site, column) in enumerate(master.columns):
            for t, tour in enumerate(pool['tours']):
                if time.perf_counter() >= deadline:
                    raise TimeoutError('Supply compatibility reached its budget')
                if site in tour['supply'] and route_profile(problem, site, column['tasks'], tour['supply'][site]) is None:
                    m.addConstr(master.variables[i]+master.alpha[t] <= 1)
                    stats['supply_exclusions'] += 1
        if math.isfinite(ceiling):
            # A strict improvement cutoff is confined to the primal model.
            m.addConstr(m.getObjective() <= ceiling-1e-5)
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
        m.update()
        stats['build_runtime'] = time.perf_counter()-begun
        for _ in range(attempts):
            left = deadline-time.perf_counter()
            if left <= 0:
                break
            # Leave wall-clock time for physical scheduling and auditing.
            m.Params.TimeLimit = max(0.0, left*.65)
            m.optimize()
            stats['pool_status'] = int(m.Status)
            if not m.SolCount:
                stats['status'] = 'NO_IMPROVEMENT_IN_POOL' if m.Status == GRB.INFEASIBLE else 'TIME_LIMIT_POOL'
                break
            stats['attempts'] += 1
            selected = [i for i, v in enumerate(master.variables) if v.X > .5]
            inbound = [i for i, v in enumerate(master.alpha) if v.X > .5]
            owner = {c: s for (c, s), v in master.assignment.items() if v.X > .5}
            fixed = assigned_problem(problem, owner) if problem.free_assignment else problem
            result = empty_result(fixed, 'exact')
            supply, by_site = {}, defaultdict(list)
            p, batch = fixed.operating_policy, fixed.operating_policy.batches[0]
            for vehicle, i in enumerate(inbound):
                now, previous, events = batch.start+p.e1_load_time, fixed.depot, []
                for s in pool['tours'][i]['sites']:
                    now += fixed.travel(previous, s)
                    events.append(dict(kind='d', site_id=s, start=now, quantity=fixed.demands[s, batch.id]))
                    now += fixed.service(s)
                    supply[s], previous = now, s
                result['e1_trips'].append(dict(trip_id=f'pool_e1_{vehicle}', batch_id=batch.id,
                    vehicle_id=vehicle, departure=batch.start+p.e1_load_time,
                    return_complete=now+fixed.travel(previous, fixed.depot)+p.depot_return_unload_time, events=events))
            for i in selected:
                s, column = master.columns[i]
                by_site[s].append(column)
            valid_schedule = True
            for s, columns in by_site.items():
                trips, status = schedule_routes(fixed, s, columns, supply[s], deadline=deadline, seed=seed)
                stats['scheduling'].append(dict(attempt=stats['attempts'], site=s, status=status))
                if trips is None:
                    valid_schedule = False
                    break
                result['e2_trips'].extend(trips)
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
                    # One improved pool solution is enough for the full solver.
                    break
            else:
                stats['rejected_schedules'] += 1
            # Discard only this chosen combination inside the restricted pool.
            chosen = [master.variables[i] for i in selected]+[master.alpha[i] for i in inbound]
            m.addConstr(gp.quicksum(chosen) <= len(chosen)-1)
    except TimeoutError as error:
        stats.update(status='TIME_LIMIT_BUILD', limitation=str(error))
    finally:
        if master is not None:
            master.dispose()
    stats['runtime'] = time.perf_counter()-begun
    return best, stats
