"""Bounded insertion seeds with deliveries and pickups on separate trips.

This constructor only supplies an audited upper bound. Its restricted insertion
neighborhood never filters the exact model or proves infeasibility.
"""
import time

from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.state import empty_result


class _Expired(Exception):
    pass


def construct_split_incumbent(problem, e1, supply, deadline):
    p, f = problem.operating_policy, problem.fleet
    batch = p.batches[0]

    def budget():
        if time.perf_counter() >= deadline:
            raise _Expired

    def schedule(site, vehicle, rows, delivered):
        """Reschedule every affected later trip, including prep and turnaround."""
        ready, trips, distance = p.cycle_start, [], 0.0
        done = dict(delivered)
        for slot, tasks in enumerate(rows):
            budget()
            loaded = any(kind == 'd' for _, kind in tasks)
            departure = max(ready, supply[site])+p.e2_dispatch_prep_time if loaded else ready
            now, previous, events = departure, site, []
            weight = sum(problem.task(oid, kind)[1] for oid, kind in tasks if kind == 'd')
            volume = sum(problem.task(oid, kind)[2] for oid, kind in tasks if kind == 'd')
            if weight > f.e2_weight_cap+1e-6 or volume > f.e2_volume_cap+1e-6:
                return None
            for oid, kind in tasks:
                budget()
                order, w, vol = problem.task(oid, kind)
                lower, upper = order.delivery_window if kind == 'd' else order.pickup_window
                now = max(now+problem.travel(previous, order.customer), lower)
                if kind == 'd' and oid in delivered:
                    # Pickups may occupy gaps between delivery trips, but
                    # cannot move a delivery already used by another vehicle.
                    fixed_start = delivered[oid]-order.service_time
                    if now > fixed_start:
                        return None
                    now = fixed_start
                if kind == 'p':
                    if oid not in done:
                        return None
                    now = max(now, done[oid]+order.min_use_time)
                if now > upper or (kind == 'd' and now+order.service_time > batch.cutoff):
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
            if end+p.e2_turnaround_time > p.cycle_end or (
                    any(kind == 'p' for _, kind in tasks) and end > batch.cutoff):
                return None
            distance += problem.distance(previous, site)
            trips.append(dict(trip_id=f'split_e2_{site}_{vehicle}_{slot}', satellite_id=site,
                              vehicle_id=vehicle, trip_index=slot, departure=departure,
                              return_complete=end, events=events))
            ready = end+p.e2_turnaround_time
        return trips, done, distance, ready

    best = None
    try:
        # End-time priority supplies an alternative when shortest insertion
        # consumes the time needed by later deliveries or reverse trips.
        for early in (False, True):
            result = empty_result(problem, 'exact')
            result['e1_trips'] = e1
            failed = False
            for site in supply:
                budget()
                orders = [o for o in problem.orders if o.satellite == site]
                plans = [[] for _ in range(f.e2_per_sat_count)]
                timed = [([], {}, 0.0, p.cycle_start) for _ in plans]
                delivered = {}
                for kind in problem.task_kinds:
                    if kind == 'p':
                        # Pickup insertions leave all delivery times fixed.
                        delivered = {oid: end for row in timed for oid, end in row[1].items()}
                    ordered = sorted(orders, key=lambda o: (
                        (o.delivery_window if kind == 'd' else o.pickup_window)[1],
                        (o.delivery_window if kind == 'd' else o.pickup_window)[0], o.id))
                    for order in ordered:
                        budget()
                        best_choice = None
                        for vehicle, rows in enumerate(plans):
                            proposals = [(slot, False) for slot in range(len(rows))]
                            if len(rows) < p.e2_max_trips_per_vehicle:
                                proposals += [(slot, True) for slot in range(len(rows)+1)]
                            for slot, new in proposals:
                                row = [] if new else rows[slot]
                                for position in range(len(row)+1):
                                    budget()
                                    if kind == 'p' and (order.id, 'd') in row[position:]:
                                        continue
                                    trial = list(rows)
                                    inserted = row[:position]+[(order.id, kind)]+row[position:]
                                    if new:
                                        trial.insert(slot, inserted)
                                    else:
                                        trial[slot] = inserted
                                    candidate = schedule(site, vehicle, trial, delivered)
                                    if candidate is None:
                                        continue
                                    delta = candidate[2]-timed[vehicle][2]
                                    finish = candidate[3]
                                    score = (finish, delta) if early else (delta, finish)
                                    if best_choice is None or score < best_choice[0]:
                                        best_choice = score, vehicle, trial, candidate
                        if best_choice is None:
                            failed = True
                            break
                        _, vehicle, plans[vehicle], timed[vehicle] = best_choice
                    if failed:
                        break
                if failed:
                    break
                result['e2_trips'].extend(trip for row in timed for trip in row[0])
            if failed:
                continue
            audit = audit_solution(problem, result)
            if audit['valid']:
                result.update(audit=audit, cutoff_snapshots=audit['cutoff_snapshots'],
                              depot_releases=audit['depot_releases'])
                result['info'].update(objective=audit['objective'], status='FEASIBLE',
                                      incumbent_source='deterministic_primal_seed',
                                      primal_strategy='split_insertion_early' if early else 'split_insertion_distance')
                if best is None or result['info']['objective'] < best['info']['objective']:
                    best = result
    except _Expired:
        pass
    return best
