"""Canonical earliest schedule for fixed B=1 routes (not a cost optimizer).

Remove arbitrary MILP time slack before pricing, without altering route order,
assignments, vehicle usage, distance or any independently audited requirement.
"""
from collections import defaultdict
from copy import deepcopy

from models.common.cycle.audit import audit_solution


def normalize_schedule(problem, result):
    if len(problem.operating_policy.batches) != 1:
        raise ValueError('Timing normalization is defined only for B=1')
    if not audit_solution(problem, result)['valid']:
        raise ValueError('Cannot normalize an invalid route')
    root = problem
    if problem.free_assignment:
        from models.common.cycle.assignment import assigned_problem
        problem = assigned_problem(problem, result['assignment'])
    p = problem.operating_policy
    batch = p.batches[0]
    updated = deepcopy(result)
    bounds, children, indegree = {}, defaultdict(list), defaultdict(int)
    tasks, supply, vehicles = {}, {}, defaultdict(list)

    def node(key, lower):
        bounds[key] = max(bounds.get(key, p.cycle_start), lower)
        return key

    def edge(left, right, lag):
        children[left].append((right, lag))
        indegree[right] += 1

    for layer in ('e1', 'e2'):
        for trip in updated[layer+'_trips']:
            rid = trip['trip_id']
            delivery = any(e['kind'] == 'd' for e in trip['events'])
            prep = (p.e1_load_time if layer == 'e1' else p.e2_dispatch_prep_time) if delivery else 0
            earliest = (batch.start+prep if delivery else batch.cutoff) if layer == 'e1' else p.cycle_start+prep
            begin = node((rid, 'start'), earliest)
            end = node((rid, 'end'), p.cycle_start)
            base = problem.depot if layer == 'e1' else trip['satellite_id']
            key = (layer, base, trip['vehicle_id'])
            slot = 0 if layer == 'e1' else trip['trip_index']
            vehicles[key].append((slot, begin, end, prep))
            previous, site, service, kind_before = begin, base, 0, None
            for i, event in enumerate(trip['events']):
                kind = event['kind']
                if layer == 'e1':
                    target = event['site_id']
                    lower = batch.start if kind == 'd' else batch.cutoff
                    if kind == 'p' and kind_before != 'p':
                        lower += problem.travel(site, target)
                    duration = problem.service(target)
                else:
                    order = problem.order_map[event['order_id']]
                    target, duration = order.customer, order.service_time
                    lower = (order.delivery_window if kind == 'd' else order.pickup_window)[0]
                    tasks[order.id, kind] = (rid, i)
                current = node((rid, i), lower)
                edge(previous, current, service+problem.travel(site, target))
                if layer == 'e1' and kind == 'd':
                    supply[target] = (current, duration)
                previous, site, service, kind_before = current, target, duration, kind
            unload = p.depot_return_unload_time if layer == 'e1' else p.e2_return_unload_time
            edge(previous, end, service+problem.travel(site, base)+unload)
    for trip in updated['e2_trips']:
        if any(e['kind'] == 'd' for e in trip['events']):
            source, service = supply[trip['satellite_id']]
            edge(source, (trip['trip_id'], 'start'), service+p.e2_dispatch_prep_time)
    for order in problem.orders if problem.reusable else ():
        edge(tasks[order.id, 'd'], tasks[order.id, 'p'], order.service_time+order.min_use_time)
    for (layer, _, _), trips in vehicles.items():
        trips.sort()
        turnaround = p.e1_turnaround_time if layer == 'e1' else p.e2_turnaround_time
        for previous, following in zip(trips, trips[1:]):
            edge(previous[2], following[1], turnaround+following[3])
    queue = [key for key in bounds if not indegree[key]]
    visited = 0
    while queue:
        current = queue.pop()
        visited += 1
        for target, lag in children[current]:
            bounds[target] = max(bounds[target], bounds[current]+lag)
            indegree[target] -= 1
            if not indegree[target]:
                queue.append(target)
    if visited != len(bounds):
        raise ValueError('Timing dependency cycle')
    for layer in ('e1', 'e2'):
        for trip in updated[layer+'_trips']:
            rid = trip['trip_id']
            trip['departure'], trip['return_complete'] = bounds[rid, 'start'], bounds[rid, 'end']
            for i, event in enumerate(trip['events']):
                event['start'] = bounds[rid, i]
    for field in ('audit', 'cutoff_snapshots', 'depot_releases'):
        updated.pop(field, None)
    updated['schedule_convention'] = 'earliest_feasible_fixed_route_B1'
    updated['audit'] = audit_solution(root, updated)
    if not updated['audit']['valid']:
        raise ValueError(f'Normalized schedule failed independent audit: {updated["audit"]["errors"]}')
    return updated
