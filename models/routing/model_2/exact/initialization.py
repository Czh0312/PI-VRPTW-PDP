"""Bounded deterministic primal seeds, independent of the ALNS solver.

Only independently audited schedules become incumbents. Failure of this
restricted construction says nothing about feasibility of the full MILP.
"""
import time
from collections import defaultdict

from models.common.cycle.audit import audit_solution
from models.common.cycle.state import empty_result


def construct_incumbent(problem, deadline, *, split_trips=True):
    p, f = problem.operating_policy, problem.fleet
    if not problem.orders or time.perf_counter() >= deadline:
        return None
    if len(p.batches) > 1:
        raise ValueError('This initial solution requires B=1')
    batch = p.batches[0]
    sites = [s for s in problem.satellites if problem.demands[s, batch.id]]
    sites.sort(key=lambda s: (min(o.delivery_window[1] for o in problem.orders if o.satellite == s), s))

    def inbound(rows):
        trips, supply, distance, violations = [], {}, 0.0, 0
        for vehicle, row in enumerate(rows):
            if not row:
                continue
            weight = sum(problem.weights[s, batch.id] for s in row)
            volume = sum(problem.demands[s, batch.id]*f.container_volume for s in row)
            violations += int(weight > f.e1_weight_cap+1e-6 or volume > f.e1_volume_cap+1e-6)
            now, previous, events = batch.start+p.e1_load_time, problem.depot, []
            for kind in problem.task_kinds:
                if kind == 'p':
                    now = max(now, batch.cutoff)
                for site in row:
                    now += problem.travel(previous, site)
                    distance += problem.distance(previous, site)
                    events.append(dict(kind=kind, site_id=site, start=now, quantity=problem.demands[site, batch.id]))
                    now += problem.service(site)
                    if kind == 'd':
                        supply[site] = now
                        violations += int(now > batch.cutoff)
                    previous = site
            end = now+problem.travel(previous, problem.depot)+p.depot_return_unload_time
            distance += problem.distance(previous, problem.depot)
            violations += int(end > batch.end or end+p.e1_turnaround_time > p.cycle_end or
                              (problem.reusable and end+p.cleaning_duration > p.cycle_end))
            trips.append(dict(trip_id=f'seed_e1_{vehicle}', batch_id=batch.id, vehicle_id=vehicle,
                              departure=batch.start+p.e1_load_time, return_complete=end, events=events))
        return trips, supply, distance, violations

    rows = [[] for _ in range(f.e1_count)]
    for site in sites:
        if time.perf_counter() >= deadline:
            return None
        options = []
        for vehicle, row in enumerate(rows):
            trial = [list(r) for r in rows]
            trial[vehicle].append(site)
            _, supply, distance, violations = inbound(trial)
            # Earlier supplies improve the chance of meeting customer windows.
            options.append((violations, sum(supply.values()), distance, vehicle))
        rows[min(options)[-1]].append(site)
    e1, supply, _, violations = inbound(rows)
    if violations:
        return None
    # Reserve most of the remaining budget for insertions that can separate
    # delivery and pickup trips. Keep the original paired seeds as fallbacks.
    paired_deadline = (time.perf_counter()+max(0, deadline-time.perf_counter())*.3
                       if split_trips else deadline)
    def schedule(site, depart, tasks):
        now, previous, events, done, distance = depart, site, [], {}, 0.0
        weight = sum(problem.task(oid, kind)[1] for oid, kind in tasks if kind == 'd')
        volume = sum(problem.task(oid, kind)[2] for oid, kind in tasks if kind == 'd')
        if weight > f.e2_weight_cap+1e-6 or volume > f.e2_volume_cap+1e-6:
            return None
        for oid, kind in tasks:
            order, w, vol = problem.task(oid, kind)
            window = order.delivery_window if kind == 'd' else order.pickup_window
            now = max(now+problem.travel(previous, order.customer), window[0])
            if kind == 'p':
                now = max(now, done[oid]+order.min_use_time)
            if now > window[1] or (kind == 'd' and now+order.service_time > batch.cutoff):
                return None
            events.append(dict(order_id=oid, kind=kind, start=now, quantity=order.count))
            now += order.service_time
            if kind == 'd':
                done[oid] = now
            weight += w if kind == 'p' else -w
            volume += vol if kind == 'p' else -vol
            if weight > f.e2_weight_cap+1e-6 or volume > f.e2_volume_cap+1e-6:
                return None
            distance += problem.distance(previous, order.customer)
            previous = order.customer
        end = now+problem.travel(previous, site)+p.e2_return_unload_time
        if end+p.e2_turnaround_time > p.cycle_end or (problem.reusable and end > batch.cutoff):
            return None
        return end, events, distance+problem.distance(previous, site)

    best = None
    strategies = [(merge, key) for merge in (True, False) for key in (
        lambda o: (o.pickup_window[0] if problem.reusable else o.delivery_window[0], o.id),
        lambda o: (o.delivery_window[1], o.id))]
    for merge, sort_key in strategies:
        if time.perf_counter() >= paired_deadline:
            break
        result = empty_result(problem, 'exact')
        result['e1_trips'] = e1
        failed = False
        for site in sites:
            ready, slots = [p.cycle_start]*f.e2_per_sat_count, [0]*f.e2_per_sat_count
            last = {}
            for order in sorted((o for o in problem.orders if o.satellite == site), key=sort_key):
                if time.perf_counter() >= paired_deadline:
                    failed = True
                    break
                choices = []
                new_tasks = [(order.id, kind) for kind in problem.task_kinds]
                for vehicle in range(f.e2_per_sat_count):
                    proposals = []
                    if merge and vehicle in last:
                        old_index, old_tasks, old_distance = last[vehicle]
                        depart = result['e2_trips'][old_index]['departure']
                        proposals.append((old_index, depart, old_tasks+new_tasks, old_distance))
                        if problem.reusable:
                            before_pickups = next((i for i, (_, k) in enumerate(old_tasks) if k == 'p'), len(old_tasks))
                            combined = old_tasks[:before_pickups]+[(order.id, 'd')]+old_tasks[before_pickups:]+[(order.id, 'p')]
                            proposals.append((old_index, depart, combined, old_distance))
                    if slots[vehicle] < p.e2_max_trips_per_vehicle:
                        proposals.append((None, max(supply[site], ready[vehicle])+p.e2_dispatch_prep_time, new_tasks, 0.0))
                    for index, depart, tasks, old_distance in proposals:
                        timed = schedule(site, depart, tasks)
                        if timed is not None:
                            end, events, distance = timed
                            choices.append((distance-old_distance, end, vehicle, depart, events, index, tasks, distance))
                if not choices:
                    failed = True
                    break
                _, end, vehicle, depart, events, index, tasks, distance = min(choices, key=lambda x: x[:4])
                if index is None:
                    index = len(result['e2_trips'])
                    result['e2_trips'].append(dict(trip_id=f'seed_e2_{site}_{vehicle}_{slots[vehicle]}',
                        satellite_id=site, vehicle_id=vehicle, trip_index=slots[vehicle],
                        departure=depart, return_complete=end, events=events))
                    slots[vehicle] += 1
                else:
                    result['e2_trips'][index].update(return_complete=end, events=events)
                ready[vehicle], last[vehicle] = end+p.e2_turnaround_time, (index, tasks, distance)
            if failed:
                break
        if failed:
            continue
        audit = audit_solution(problem, result)
        if audit['valid']:
            result.update(audit=audit, cutoff_snapshots=audit['cutoff_snapshots'], depot_releases=audit['depot_releases'])
            result['info'].update(objective=audit['objective'], status='FEASIBLE', incumbent_source='deterministic_primal_seed')
            if best is None or result['info']['objective'] < best['info']['objective']:
                best = result
    if not split_trips:
        return best
    from models.routing.model_2.exact.primal.construction import construct_split_incumbent
    candidate = construct_split_incumbent(problem, e1, supply, deadline)
    if candidate is not None and (best is None or candidate['info']['objective'] < best['info']['objective']):
        best = candidate
    return best


def install_start(problem, model, ctx, result):
    """Audit, relabel identical vehicles to satisfy symmetry, then load a start."""
    if result is None or not audit_solution(problem, result)['valid']:
        return False
    if ctx.get('assignments'):
        assignment = result.get('assignment', {})
        if any((c, s) not in ctx['assignments'] for c, s in assignment.items()):
            return False
    scheduled = {}
    for layer in ('e1', 'e2'):
        groups = defaultdict(list)
        for trip in result[layer+'_trips']:
            groups[trip.get('satellite_id', problem.depot)].append(trip)
        trips = []
        for rows in groups.values():
            counts = defaultdict(int)
            for trip in rows:
                counts[trip['vehicle_id']] += 1
            order = sorted(counts, key=lambda v: (-counts[v], v)) if (
                layer == 'e2' and ctx.get('trip_count_symmetry')) else sorted(counts)
            vehicles = {v: i for i, v in enumerate(order)}
            slots = defaultdict(int)
            for trip in sorted(rows, key=lambda r: (r['vehicle_id'], r.get('trip_index', 0))):
                copied = {**trip, 'vehicle_id': vehicles[trip['vehicle_id']]}
                if layer == 'e2':
                    copied['trip_index'] = slots[copied['vehicle_id']]
                    slots[copied['vehicle_id']] += 1
                trips.append(copied)
        for trip in trips:
            matches = [r for r in ctx[layer] if r['vehicle'] == trip['vehicle_id'] and
                       (r['batch'].id == trip['batch_id'] if layer == 'e1' else
                        r['satellite'] == trip['satellite_id'] and r['slot'] == trip['trip_index'])]
            if not matches:
                return False
            r = matches[0]
            order = []
            for event in trip['events']:
                index = next((i for i, n in enumerate(r['nodes']) if n['kind'] == event['kind'] and
                             (n['site'] == event['site_id'] if layer == 'e1' else
                              n['order'].id == event['order_id'])), None)
                if index is None:
                    return False
                order.append(index)
            arcs = set(zip([-1]+order, order+[-2]))
            if not arcs.issubset(r['x']):
                return False
            scheduled[r['label']] = trip, order, arcs
    # Set absent visits and arcs too; a partial start should not ask the solver
    # to rediscover that every covered task excludes all other route copies.
    for (customer, site), variable in ctx.get('assignments', {}).items():
        variable.Start = int(result['assignment'][customer] == site)
    for variable in ctx.get('global_e2_arcs', {}).values():
        variable.Start = 0
    for layer in ('e1', 'e2'):
        for r in ctx[layer]:
            entry = scheduled.get(r['label'])
            trip, order, arcs = entry if entry else (None, [], set())
            r['used'].Start = int(entry is not None)
            r['start'].Start = trip['departure'] if trip else 0
            r['end'].Start = trip['return_complete'] if trip else 0
            for arc, variable in r['x'].items():
                if not (r.get('global_internal_arcs') and min(arc) >= 0) or arc in arcs:
                    variable.Start = int(arc in arcs)
            for i, variable in r['visit'].items():
                variable.Start = int(i in order)
            for resource, variable in zip(('w', 'v'), r.get('departure_loads', ())):
                variable.Start = sum(r['nodes'][i][resource] for i in order if r['nodes'][i]['kind'] == 'd')
            for index, event in zip(order, trip['events'] if trip else []):
                r['times'][index].Start = event['start']
                quantity = r['nodes'][index]['quantity']
                if layer == 'e1' and event['kind'] == 'p' and not isinstance(quantity, int):
                    quantity.Start = event['quantity']
                if layer == 'e2' and event['kind'] == 'p' and ctx.get('pickup_returns'):
                    ctx['pickup_returns'][event['order_id']].Start = trip['return_complete']
    return True
