"""Deterministic full-cycle seeds for M5, without importing another solver.

Restricted paired E2 tours may span cutoffs but carry deliveries from only one
batch. After the original portfolio, try all pair insertion positions and
partial E1 tour merges. E1 pickups are reconstructed from actual E2 unload times. Construction
failure is not an infeasibility certificate; only independently audited full
schedules are returned. All search and simulation use the caller's deadline.
"""
from bisect import bisect_left
import time

from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.state import empty_result


class _BudgetExpired(Exception):
    pass


def construct_multibatch_incumbent(problem, deadline, *, improve=True):
    p, f = problem.operating_policy, problem.fleet
    if len(p.batches) < 2 or problem.free_assignment or not problem.reusable:
        return None

    def check_budget():
        if time.perf_counter() >= deadline:
            raise _BudgetExpired

    def e1_schedule(batch, deliveries, pickups, quantities, available):
        check_budget()
        if not deliveries and not pickups:
            return None
        if deliveries and available > batch.start:
            return None
        weight = sum(problem.weights[s, batch.id] for s in deliveries)
        volume = sum(problem.demands[s, batch.id]*f.container_volume for s in deliveries)
        if weight > f.e1_weight_cap+1e-6 or volume > f.e1_volume_cap+1e-6:
            return None
        departure = batch.start+p.e1_load_time if deliveries else max(batch.cutoff, available)
        now, previous, events, distance, supply = departure, problem.depot, [], 0.0, {}
        for kind, sites in (('d', deliveries), ('p', pickups)):
            if kind == 'p' and sites:
                now = max(now, batch.cutoff)
            for site in sites:
                check_budget()
                now += problem.travel(previous, site)
                distance += problem.distance(previous, site)
                count = problem.demands[site, batch.id] if kind == 'd' else quantities[site]
                events.append(dict(kind=kind, site_id=site, quantity=count, start=now))
                now += problem.service(site)
                if kind == 'd':
                    if now > batch.cutoff:
                        return None
                    supply[site] = now
                previous = site
        # All deliveries precede all pickups; the largest reverse load is at
        # the last pickup. Forward and reverse peaks are checked separately.
        picked = sum(quantities[s] for s in pickups)
        if picked*f.container_weight > f.e1_weight_cap+1e-6 or picked*f.container_volume > f.e1_volume_cap+1e-6:
            return None
        end = now+problem.travel(previous, problem.depot)+p.depot_return_unload_time
        if (end > batch.end or end+p.e1_turnaround_time > p.cycle_end
                or (picked and end+p.cleaning_duration > p.cycle_end)):
            return None
        return (dict(departure=departure, return_complete=end, events=events), supply,
                distance+problem.distance(previous, problem.depot))

    def plan_deliveries(priority):
        plans, supply = {}, {}
        for batch in p.batches:
            rows = [[] for _ in range(f.e1_count)]
            sites = [s for s in problem.satellites if problem.demands[s, batch.id]]
            sites.sort(key=lambda s: (min(o.delivery_window[1] for o in problem.orders
                                         if o.satellite == s and o.batch == batch.id), s))
            for site in sites:
                choices = []
                for vehicle, row in enumerate(rows):
                    old = e1_schedule(batch, row, [], {}, p.cycle_start) if row else None
                    for position in range(len(row)+1):
                        candidate = row[:position]+[site]+row[position:]
                        timed = e1_schedule(batch, candidate, [], {}, p.cycle_start)
                        if timed is None:
                            continue
                        early = sum(timed[1].values())-(sum(old[1].values()) if old else 0)
                        distance = timed[2]-(old[2] if old else 0)
                        score = (early, distance) if priority == 'supply' else (distance, early)
                        choices.append((*score, vehicle, position))
                if not choices:
                    return None
                _, _, vehicle, position = min(choices)
                rows[vehicle].insert(position, site)
            for row in rows:
                if row:
                    timed = e1_schedule(batch, row, [], {}, p.cycle_start)
                    supply.update({(s, batch.id): t for s, t in timed[1].items()})
            plans[batch.id] = rows
        return plans, supply

    def e2_schedule(site, departure, tasks):
        check_budget()
        weight = sum(problem.task(oid, kind)[1] for oid, kind in tasks if kind == 'd')
        volume = sum(problem.task(oid, kind)[2] for oid, kind in tasks if kind == 'd')
        if weight > f.e2_weight_cap+1e-6 or volume > f.e2_volume_cap+1e-6:
            return None
        now, previous, events, delivered, distance = departure, site, [], {}, 0.0
        for oid, kind in tasks:
            check_budget()
            order, w, vol = problem.task(oid, kind)
            window = order.delivery_window if kind == 'd' else order.pickup_window
            now = max(now+problem.travel(previous, order.customer), window[0])
            if kind == 'p':
                now = max(now, delivered[oid]+order.min_use_time)
            if now > window[1]:
                return None
            events.append(dict(kind=kind, order_id=oid, start=now, quantity=order.count))
            now += order.service_time
            if kind == 'd':
                delivered[oid] = now
            weight += w if kind == 'p' else -w
            volume += vol if kind == 'p' else -vol
            if weight > f.e2_weight_cap+1e-6 or volume > f.e2_volume_cap+1e-6:
                return None
            distance += problem.distance(previous, order.customer)
            previous = order.customer
        end = now+problem.travel(previous, site)+p.e2_return_unload_time
        if end > p.batches[-1].cutoff or end+p.e2_turnaround_time > p.cycle_end:
            return None
        return end, events, distance+problem.distance(previous, site)

    def plan_e2(supply, merge, sort_key, insertion=False):
        trips = []
        for site in problem.satellites:
            ready = [p.cycle_start]*f.e2_per_sat_count
            slots = [0]*f.e2_per_sat_count
            last = {}
            for order in sorted((o for o in problem.orders if o.satellite == site), key=sort_key):
                check_budget()
                tasks = [(order.id, 'd'), (order.id, 'p')]
                choices = []
                for vehicle in range(f.e2_per_sat_count):
                    proposals = []
                    if merge and vehicle in last:
                        index, old_tasks, old_distance, bid = last[vehicle]
                        if bid == order.batch:
                            departure = trips[index]['departure']
                            if insertion:
                                for dpos in range(len(old_tasks)+1):
                                    combined = old_tasks[:dpos]+tasks[:1]+old_tasks[dpos:]
                                    for ppos in range(dpos+1, len(combined)+1):
                                        check_budget()
                                        candidate = combined[:ppos]+tasks[1:]+combined[ppos:]
                                        proposals.append((index, departure, candidate, old_distance))
                            else:
                                proposals.append((index, departure, old_tasks+tasks, old_distance))
                                first_pickup = next(i for i, (_, k) in enumerate(old_tasks) if k == 'p')
                                combined = old_tasks[:first_pickup]+tasks[:1]+old_tasks[first_pickup:]+tasks[1:]
                                proposals.append((index, departure, combined, old_distance))
                    if slots[vehicle] < p.e2_max_trips_per_vehicle:
                        departure = max(ready[vehicle], supply[site, order.batch])+p.e2_dispatch_prep_time
                        proposals.append((None, departure, tasks, 0.0))
                    for index, departure, candidate, old_distance in proposals:
                        timed = e2_schedule(site, departure, candidate)
                        if timed is not None:
                            end, events, distance = timed
                            choices.append((distance-old_distance, end, vehicle, departure,
                                            index, candidate, events, distance))
                if not choices:
                    return None
                _, end, vehicle, departure, index, tasks, events, distance = min(choices, key=lambda c: c[:4])
                if index is None:
                    index = len(trips)
                    trips.append(dict(trip_id=f'primal_e2_{site}_{vehicle}_{slots[vehicle]}',
                                      satellite_id=site, vehicle_id=vehicle, trip_index=slots[vehicle],
                                      departure=departure, return_complete=end, events=events))
                    slots[vehicle] += 1
                else:
                    trips[index].update(return_complete=end, events=events)
                ready[vehicle] = end+p.e2_turnaround_time
                last[vehicle] = index, tasks, distance, order.batch
        return trips

    def finish_e1(plans, e2, priority='early'):
        cutoffs = [b.cutoff for b in p.batches]
        quantities = {(s, b.id): 0 for s in problem.satellites for b in p.batches}
        for trip in e2:
            # Equality belongs to the current cutoff, including unload time.
            batch = p.batches[bisect_left(cutoffs, trip['return_complete'])]
            quantities[trip['satellite_id'], batch.id] += sum(e['quantity'] for e in trip['events'] if e['kind'] == 'p')
        ready, trips = [p.cycle_start]*f.e1_count, []
        releases, issued = [], 0
        for batch in p.batches:
            check_budget()
            issued += problem.batch_demands[batch.id]
            if p.container_pool_size+sum(n for t, n in releases if t <= batch.start) < issued:
                return None
            # Vehicle identities can change across batches, but supply times
            # cannot. Assign intact delivery tours to currently available cars.
            deliveries = [[] for _ in range(f.e1_count)]
            available = [v for v in range(f.e1_count) if ready[v] <= batch.start]
            for row in (row for row in plans[batch.id] if row):
                if not available:
                    return None
                deliveries[available.pop(0)] = row
            pickups = [[] for _ in range(f.e1_count)]
            q = {s: quantities[s, batch.id] for s in problem.satellites}
            for site in sorted((s for s in q if q[s]), key=lambda s: (-q[s], s)):
                choices = []
                for vehicle, row in enumerate(pickups):
                    old = (e1_schedule(batch, deliveries[vehicle], row, q, ready[vehicle])
                           if priority == 'distance' else None)
                    for position in range(len(row)+1):
                        candidate = row[:position]+[site]+row[position:]
                        timed = e1_schedule(batch, deliveries[vehicle], candidate, q, ready[vehicle])
                        if timed is not None:
                            # Keep an early-return variant for tight cleaning
                            # or vehicle reuse; also try incremental distance.
                            end = timed[0]['return_complete']
                            score = ((timed[2]-(old[2] if old else 0), end) if priority == 'distance'
                                     else (end, timed[2]))
                            choices.append((*score, vehicle, position))
                if not choices:
                    return None
                _, _, vehicle, position = min(choices)
                pickups[vehicle].insert(position, site)
            for vehicle, row in enumerate(deliveries):
                if not row and not pickups[vehicle]:
                    continue
                timed = e1_schedule(batch, row, pickups[vehicle], q, ready[vehicle])
                if timed is None:
                    return None
                trip = dict(timed[0], trip_id=f'primal_e1_{batch.id}_{vehicle}',
                            batch_id=batch.id, vehicle_id=vehicle)
                trips.append(trip)
                ready[vehicle] = trip['return_complete']+p.e1_turnaround_time
                releases.append((trip['return_complete']+p.cleaning_duration,
                                 sum(q[s] for s in pickups[vehicle])))
        return trips

    best, best_plans = None, None

    def consider(plans, e2, strategy, pickup_priority='early'):
        nonlocal best, best_plans
        e1 = finish_e1(plans, e2, pickup_priority)
        if e1 is None:
            return
        check_budget()
        result = empty_result(problem, 'exact')
        result.update(e1_trips=e1, e2_trips=e2)
        audit = audit_solution(problem, result)
        if not audit['valid']:
            return
        result.update(audit=audit, cutoff_snapshots=audit['cutoff_snapshots'],
                      depot_releases=audit['depot_releases'])
        result['info'].update(status='FEASIBLE', objective=audit['objective'],
            incumbent_source='deterministic_multibatch_primal_seed', primal_strategy=strategy)
        if best is None or audit['objective'] < best['info']['objective']:
            best = result
            best_plans = plans
            return True
        return False

    def merged_delivery_plans(plans):
        """Rank bounded, order-preserving route merges by forward distance.

        These are proposals only: later E2 windows, E1 reverse loads, cleaning
        and vehicle availability still have to pass construction and audit.
        """
        choices = []
        for batch in p.batches:
            rows = plans[batch.id]
            for left, row in enumerate(rows):
                if not row:
                    continue
                old_left = e1_schedule(batch, row, [], {}, p.cycle_start)
                for right, other in enumerate(rows):
                    if left == right or not other:
                        continue
                    old_right = e1_schedule(batch, other, [], {}, p.cycle_start)
                    for position in range(len(row)+1):
                        combined = row[:position]+other+row[position:]
                        timed = e1_schedule(batch, combined, [], {}, p.cycle_start)
                        if timed is not None:
                            delta = timed[2]-old_left[2]-old_right[2]
                            choices.append((delta, batch.id, left, right, position, combined))
        for _, bid, left, right, _, combined in sorted(choices):
            check_budget()
            candidate = {b: [list(row) for row in rows] for b, rows in plans.items()}
            candidate[bid][left], candidate[bid][right] = combined, []
            supply = {}
            for batch in p.batches:
                for row in candidate[batch.id]:
                    if row:
                        timed = e1_schedule(batch, row, [], {}, p.cycle_start)
                        supply.update({(s, batch.id): t for s, t in timed[1].items()})
            yield candidate, supply

    sort_keys = (
        ('pickup_ready', lambda o: (o.pickup_window[0], o.delivery_window[1], o.id)),
        ('delivery_due', lambda o: (o.delivery_window[1], o.pickup_window[1], o.id)),
    )
    try:
        planned_variants = []
        for priority in ('supply', 'distance'):
            planned = plan_deliveries(priority)
            if planned is None:
                continue
            plans, supply = planned
            planned_variants.append((priority, plans, supply))
            for merge in (True, False):
                for name, sort_key in sort_keys:
                    e2 = plan_e2(supply, merge, sort_key)
                    if e2 is None:
                        continue
                    consider(plans, e2, f'{priority}_{name}_'+('merged' if merge else 'paired'))
        # Preserve the original audited portfolio before spending the remaining
        # seed budget on more expensive delivery/pickup insertion positions.
        if improve:
            for priority, plans, supply in planned_variants:
                for name, sort_key in sort_keys:
                    e2 = plan_e2(supply, True, sort_key, insertion=True)
                    if e2 is not None:
                        for pickup_priority in ('distance', 'early'):
                            consider(plans, e2, f'{priority}_{name}_insert_{pickup_priority}', pickup_priority)
            # Each accepted step removes an E1 delivery tour. Rebuild E2 from
            # the resulting supply times instead of retaining stale departures.
            changed = best is not None
            while changed:
                changed = False
                for plans, supply in merged_delivery_plans(best_plans):
                    for name, sort_key in sort_keys:
                        e2 = plan_e2(supply, True, sort_key, insertion=True)
                        if e2 is None:
                            continue
                        for pickup_priority in ('distance', 'early'):
                            if consider(plans, e2, f'merge_e1_{name}_insert_{pickup_priority}', pickup_priority):
                                changed = True
                    if changed:
                        break
    except _BudgetExpired:
        pass
    return best
