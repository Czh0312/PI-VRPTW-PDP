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
from .single_batch_route_pricing import evaluate_delivery_column, insertion_columns


def enrich_route_pool(problem, pool, *, deadline, limit=32):
    """Unweighted-distance alternatives for primal diversity, never a proof.

    The time-cut duals can favor routes unsuitable for integer scheduling.
    Independently replay insertions and reordered routes into a PRIVATE pool;
    these extra routes do not modify the solved LP or its certificate.
    """
    begun, additions = time.perf_counter(), []
    known = {(s, c['tasks']) for s, c in pool['columns']}
    for site, context in pool['contexts'].items():
        if time.perf_counter() >= deadline:
            break
        local = min(deadline, time.perf_counter()+.12)
        columns = [c for s, c in pool['columns'] if s == site]
        prices = pool.get('prices', {}).get(site, {})
        candidates = {}
        static = {n['key']: problem.distance(site, n['order'].customer)+problem.distance(n['order'].customer, site)
                  for n in context['nodes']}
        for rewards in (prices, static):
            for c in insertion_columns(problem, context, rewards, columns, limit=16,
                deadline=min(local, time.perf_counter()+.04)):
                if (site, c['tasks']) not in known:
                    candidates[c['tasks']] = c
        for c in sorted(columns, key=lambda c: c['cost']-sum(prices.get(k, 0) for k in c['tasks'])):
            if time.perf_counter() >= local:
                break
            sequence = c['tasks']
            for i in range(len(sequence)):
                for j in range(i+1, len(sequence)):
                    if time.perf_counter() >= local:
                        break
                    proposed = sequence[:i]+tuple(reversed(sequence[i:j+1]))+sequence[j+1:]
                    if (site, proposed) in known or proposed in candidates:
                        continue
                    alternative = evaluate_delivery_column(problem, context, proposed)
                    if alternative is not None and alternative['cost'] <= c['cost']+1e-7:
                        candidates[proposed] = alternative
        selected = sorted(candidates.values(), key=lambda c: (
            c['cost']-sum(prices.get(k, 0) for k in c['tasks']), c['cost'], c['tasks']))[:limit]
        additions.extend((site, c) for c in selected)
    return additions, dict(columns_added=len(additions), runtime=time.perf_counter()-begun,
                           proof_scope='primal_discovery_only')


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


def _tight_slot_bounds(policy, rows, release, count):
    """Bounds for schedules with earliest returns, retaining every route choice.

    At slot k, each used prefix finishes at least at the smallest F_r(d_lb).
    Ignoring coverage and repeated routes here is a relaxation. Upper bounds
    use F_r(latest_r); arbitrary return slack can always be removed without
    delaying the next departure. No restricted-pool bound is exported.
    """
    bounds, lower = [], math.ceil(release)
    for _ in range(count):
        eligible = {i: r for i, r in rows.items() if r is not None and r['latest'] >= lower}
        if not eligible:
            break
        depart_upper = math.floor(max(r['latest'] for r in eligible.values()))
        end_lower = math.ceil(min(max(lower+r['duration'], r['first_finish']) for r in eligible.values()))
        end_upper = math.floor(max(max(r['latest']+r['duration'], r['first_finish']) for r in eligible.values()))
        if lower > depart_upper or end_lower > end_upper:
            break
        bounds.append((lower, depart_upper, end_lower, end_upper, eligible))
        lower = max(math.ceil(release), end_lower+policy.e2_turnaround_time+policy.e2_dispatch_prep_time)
    return bounds


def _physical_slots(master, pool, deadline, slot_limit, *, tight=False, aggregate_time=False,
                    vehicle_order=False):
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
    diagnostics = dict(enabled=tight, aggregate_time=aggregate_time, vehicle_ordering=vehicle_order,
                       vehicle_order_rows=0, infeasible_columns=0, omitted_placements=0,
                       removed_slots=0, largest_big_m=0, sum_big_m=0, sites={})
    for site, indices in by_site.items():
        inbound = [(i, t['supply'][site]) for i, t in enumerate(pool['tours']) if site in t['supply']]
        supply = gp.quicksum(at*master.alpha[i] for i, at in inbound)
        earliest_supply = min((at for _, at in inbound), default=p.cycle_end+1) if tight else p.cycle_start
        rows = {}
        for i in indices:
            if time.perf_counter() >= deadline:
                raise TimeoutError('Physical route-pool profiles reached their budget')
            rows[i] = route_profile(master.problem, site, master.columns[i][1]['tasks'], earliest_supply)
            if rows[i] is None:
                master.variables[i].UB = 0
                diagnostics['infeasible_columns'] += 1
        count = min(p.e2_max_trips_per_vehicle, slot_limit, len(indices))
        bounds = (_tight_slot_bounds(p, rows, max(p.cycle_start, earliest_supply)+p.e2_dispatch_prep_time, count)
                  if tight else [(depart_lower, depart_upper, end_lower, end_upper, rows)]*count)
        slots = range(len(bounds))
        vehicles = range(min(f.e2_per_sat_count, len(indices)))
        diagnostics['removed_slots'] += (count-len(bounds))*len(vehicles)
        diagnostics['sites'][site] = dict(earliest_supply=earliest_supply,
            bounds=[dict(departure=[dl, du], return_complete=[el, eu], eligible_routes=sum(r is not None for r in eligible.values()))
                    for dl, du, el, eu, eligible in bounds])
        for v in vehicles:
            for k in slots:
                if time.perf_counter() >= deadline:
                    raise TimeoutError('Physical route-pool slots reached their budget')
                key = site, v, k
                dl, du, el, eu, eligible = bounds[k]
                departures[key] = d = m.addVar(vtype=GRB.INTEGER, lb=dl, ub=du)
                ends[key] = e = m.addVar(vtype=GRB.INTEGER, lb=el, ub=eu)
                used[key] = u = m.addVar(vtype=GRB.BINARY)
                m.addConstr(u <= master.active[site])
                m.addGenConstrIndicator(u, True, d-supply >= p.e2_dispatch_prep_time)
                if k:
                    m.addConstr(u <= used[site, v, k-1])
                    m.addGenConstrIndicator(u, True, d-ends[site, v, k-1]
                        >= p.e2_turnaround_time+p.e2_dispatch_prep_time)
                choices = []
                diagnostics['omitted_placements'] += sum(rows[i] is not None and i not in eligible for i in indices)
                for i, r in eligible.items():
                    if r is None:
                        continue
                    y = placement[i, v, k] = m.addVar(vtype=GRB.BINARY)
                    choices.append(y)
                    by_column[i].append(y)
                    if not aggregate_time:
                        big_m = (max(0, du-r['latest']), max(0, du+r['duration']-el), max(0, r['first_finish']-el))
                        diagnostics['largest_big_m'] = max(diagnostics['largest_big_m'], *big_m)
                        diagnostics['sum_big_m'] += sum(big_m)
                        m.addConstr(d <= r['latest']+big_m[0]*(1-y))
                        m.addConstr(e >= d+r['duration']-big_m[1]*(1-y))
                        m.addConstr(e >= r['first_finish']-big_m[2]*(1-y))
                m.addConstr(gp.quicksum(choices) == u)
                if aggregate_time:
                    # Exactly one binary route when used. Three weighted rows
                    # retain its latest departure, duration and waiting floor.
                    # When unused, all three rows reduce to variable bounds.
                    routed = [(r, placement[i, v, k]) for i, r in eligible.items() if r is not None]
                    idle_m = max(0, du-el)
                    diagnostics['largest_big_m'] = max(diagnostics['largest_big_m'], idle_m)
                    diagnostics['sum_big_m'] += idle_m
                    m.addConstr(d <= du-gp.quicksum((du-r['latest'])*y for r, y in routed))
                    m.addConstr(e >= d+gp.quicksum(r['duration']*y for r, y in routed)-idle_m*(1-u))
                    m.addConstr(e >= el+gp.quicksum((r['first_finish']-el)*y for r, y in routed))
            if v:
                counts = [gp.quicksum(used[site, a, k] for k in slots) for a in (v-1,v)]
                # Keep the original row: the weighted tie-break alone need
                # not imply count ordering at fractional solutions.
                m.addConstr(counts[1] <= counts[0])
                if vehicle_order and slots:
                    # Homogeneous vehicles may be sorted by decreasing trip
                    # count, then increasing FIRST route ID at equal counts.
                    # A count difference of one exceeds every possible ID
                    # difference, so this preserves the old count ordering.
                    # No within-vehicle trip or temporal order is changed.
                    first = [gp.quicksum((rank+1)*placement[i,a,0]
                        for rank,i in enumerate(indices) if (i,a,0) in placement) for a in (v-1,v)]
                    scale = len(indices)+1
                    m.addConstr(scale*counts[1]-first[1] <= scale*counts[0]-first[0],
                                name=f'pool_vehicle_order_{site}_{v}')
                    diagnostics['vehicle_order_rows'] += 1
        for i in indices:
            m.addConstr(gp.quicksum(by_column[i]) == master.variables[i])
    master.slot_bound_stats = diagnostics
    return placement, departures, ends, used


def _pool_start_trips(master, initial_result, vehicle_order):
    """Relabel audited homogeneous vehicles to match the private pool order.

    Trip contents, departure times, returns and within-vehicle order remain
    unchanged. Missing catalog routes already prevent a complete MIP start;
    their fallback rank only makes this partial hint deterministic.
    """
    trips = initial_result['e2_trips']
    if not vehicle_order:
        return {(t['satellite_id'],t['vehicle_id'],t['trip_index']):t for t in trips}, 0
    rank = {(s,c['tasks']):i for i,(s,c) in enumerate(master.columns)}
    grouped = defaultdict(lambda:defaultdict(list))
    for trip in trips:
        grouped[trip['satellite_id']][trip['vehicle_id']].append(trip)
    mapped, relabeled = {}, 0
    for site, vehicles in grouped.items():
        rows = [(old,sorted(row,key=lambda t:t['trip_index'])) for old,row in vehicles.items()]
        rows.sort(key=lambda item:(-len(item[1]),
            rank.get((site,tuple(e['order_id'] for e in item[1][0]['events'])),len(master.columns)),item[0]))
        for new,(old,row) in enumerate(rows):
            relabeled += old != new
            for trip in row:
                mapped[site,new,trip['trip_index']] = trip
    return mapped, relabeled


def route_pool_incumbent(problem, pool, *, initial_result=None, deadline, seed=42, slot_limit=2,
                         enrich=False, compact=False, canonical=False, tight=False, aggregate_time=False,
                         vehicle_order=False):
    """Select, physically schedule and independently audit pool incumbents.

    Physical route slots apply ONLY to this primal pool.
    The integer pool objective and status are never returned as global bounds.
    """
    from gurobipy import GRB
    from .single_batch_columns import DeliveryMaster
    from .single_batch_pool_master import DeliveryPoolMaster
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
        formulation='compact_integer_cargo' if compact else 'disaggregated_cargo',
        slots_per_vehicle=min(slot_limit, problem.operating_policy.e2_max_trips_per_vehicle))
    try:
        if time.perf_counter() >= deadline:
            return None, {**stats, 'runtime': time.perf_counter()-begun}
        if canonical and initial_audit and initial_audit['valid']:
            from .single_batch_schedule_start import canonical_delivery_start
            normalized, stats['input_normalization'] = canonical_delivery_start(problem, initial_result, deadline=deadline)
            if normalized is not None:
                initial_result = normalized
        columns = list(pool['columns'])
        if enrich:
            extra, stats['enrichment'] = enrich_route_pool(problem, pool,
                deadline=min(deadline, time.perf_counter()+max(0, deadline-time.perf_counter())*.15))
            columns.extend(extra)
        master_class = DeliveryPoolMaster if compact else DeliveryMaster
        master = master_class(problem, pool['domains'], pool['contexts'], pool['tours'], deadline=deadline)
        for site, c in columns:
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
        placement, departures, ends, used = _physical_slots(master, pool, deadline, slot_limit,
            tight=tight, aggregate_time=aggregate_time, vehicle_order=vehicle_order)
        stats['slot_bounds'] = master.slot_bound_stats
        stats['start_returns_repaired'] = 0
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
            initial_trips, stats['start_vehicles_relabelled'] = _pool_start_trips(master,initial_result,vehicle_order)
            for (i, vehicle, slot), y in placement.items():
                s, c = master.columns[i]
                t = initial_trips.get((s, vehicle, slot))
                y.Start = int(t is not None and tuple(e['order_id'] for e in t['events']) == c['tasks'])
            for key, u in used.items():
                t = initial_trips.get(key)
                u.Start = int(t is not None)
                if t is not None:
                    finish = t['return_complete']
                    if tight:
                        replay = replay_delivery(problem, key[0], tuple(e['order_id'] for e in t['events']), t['departure'])
                        if replay is not None:
                            finish = replay['return_complete']
                            stats['start_returns_repaired'] += finish != t['return_complete']
                    departures[key].Start, ends[key].Start = t['departure'], finish
        m.update()
        stats['build_runtime'] = time.perf_counter()-begun
        stats.update(placement_variables=len(placement), physical_slots=len(used),
                     variables=m.NumVars, constraints=m.NumConstrs, nonzeros=m.NumNZs,
                     capacity_site_sets=getattr(master, 'capacity_site_sets', None))
        if time.perf_counter() < deadline:
            left = deadline-time.perf_counter()
            # Physical scheduling is already part of this MIP. Reserve replay.
            m.Params.TimeLimit = max(0.0, left-.02)
            m.optimize()
            stats['pool_status'] = int(m.Status)
            stats['solve_runtime'], stats['solve_work'], stats['nodes'] = m.Runtime, m.Work, m.NodeCount
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
                if canonical:
                    from .single_batch_schedule_start import canonical_delivery_start
                    normalized, stats['output_normalization'] = canonical_delivery_start(problem, result, deadline=deadline)
                    if normalized is not None:
                        result = normalized
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
