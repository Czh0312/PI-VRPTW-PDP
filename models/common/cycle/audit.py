"""Independent replay of explicit schedules. Never replaces them by earliest times."""
from __future__ import annotations

from collections import Counter, defaultdict
import math

from models.common.cycle.policy import VERSION


def replay_ledger(events, pool, satellites, repetitions=1, period=0):
    """Topologically replay simultaneous transfers, carrying state across periods."""
    states = dict(clean=pool, forward=0, customer=0, e2_empty=0, e1_empty=0, dirty=0)
    states.update({f'sat:{s}': 0 for s in satellites})
    rows, failures = [], []
    expanded = defaultdict(list)
    for cycle in range(repetitions):
        for event in events:
            copied = {**event, 'time': event['time']+cycle*period, 'cycle': cycle+1}
            expanded[copied['time']].append(copied)
    for t in sorted(expanded):
        pending = list(expanded[t])
        # Releases may enable same-time transfers. A fixed alphabetical order
        # would reject valid zero-duration customer/vehicle operations.
        while pending:
            index = next((i for i, e in enumerate(pending)
                          if states[e['from']] >= e['quantity']), None)
            if index is None:
                failures.append(dict(code='CONTAINER_CONSERVATION', time=t, pending=pending))
                return states, rows, failures
            event = pending.pop(index)
            states[event['from']] -= event['quantity']
            states[event['to']] += event['quantity']
            if sum(states.values()) != pool:
                failures.append(dict(code='CONTAINER_CONSERVATION', time=t))
            rows.append({**event, 'states': dict(states)})
    return states, rows, failures


def audit_solution(problem, result, repeat=True):
    try:
        if getattr(problem, 'optimize_pool', False):
            from models.common.cycle.fixed_pool import audit_pool_solution
            return audit_pool_solution(problem, result, repeat)
        if getattr(problem, 'joint_batch', False):
            if getattr(problem, 'calendar_cycle', False):
                from models.common.cycle.calendar import audit_calendar_solution
                return audit_calendar_solution(problem, result, repeat)
            if getattr(problem, 'uniform_cycle', False):
                from models.common.cycle.uniform import audit_uniform_solution
                return audit_uniform_solution(problem, result, repeat)
            from models.common.cycle.joint import audit_joint_solution
            return audit_joint_solution(problem, result, repeat)
        if problem.free_assignment:
            from models.common.cycle.assignment import assigned_problem, validate_assignment
            assignment = validate_assignment(problem, result.get('assignment'))
            if result.get('model') != problem.model_number:
                raise ValueError('Model/assignment result mismatch')
            fixed = assigned_problem(problem, assignment)
            copied = {**result, 'model': fixed.model_number}
            audited = _audit(fixed, copied, repeat)
            audited['assignment'] = assignment
            return audited
        return _audit(problem, result, repeat)
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        return dict(valid=False, errors=[dict(code='MALFORMED_SOLUTION', detail=str(exc))])


def _audit(problem, result, repeat):
    p, f = problem.operating_policy, problem.fleet
    errors, ledger_events = [], []
    loading_starts = getattr(problem, 'batch_loading_starts', {b.id: b.start for b in p.batches})

    # Recheck the input assignment independently of solver prechecks. Goods
    # must exist when their batch starts loading, not merely at customer service.
    for order in problem.orders:
        batch = problem.batch_map[order.batch]
        if order.release_time is not None and loading_starts[batch.id] < order.release_time:
            errors.append(dict(code='ORDER_RELEASE_AFTER_BATCH_START', order_id=order.id,
                               batch_id=batch.id, batch_start=batch.start, release_time=order.release_time))
        if order.allowed_batch_ids is not None and order.batch not in order.allowed_batch_ids:
            errors.append(dict(code='ORDER_BATCH_NOT_ALLOWED', order_id=order.id, batch_id=batch.id))

    def error(code, **details):
        errors.append(dict(code=code, **details))

    def tick(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('Schedule times must be finite integer ticks')
        if int(value) != value or value < p.cycle_start or value > p.cycle_end:
            raise ValueError(f'Schedule time {value} outside integer cycle bounds')
        return int(value)

    def transfer(t, source, target, quantity, label):
        if quantity and problem.reusable:
            ledger_events.append(dict(time=t, **{'from': source, 'to': target},
                                      quantity=quantity, source=label))

    if (result.get('schema_version') != 2 or result.get('policy_version') != VERSION
            or result.get('time_unit') != 'ticks' or result.get('time_tick_seconds') != p.time_tick_seconds
            or result.get('time_tick_subdivision', 1) != p.time_tick_subdivision):
        error('INVALID_SOLUTION_SCHEMA')
    if result.get('operating_policy') is not None and result['operating_policy'] != p.as_dict():
        # JSON converts the dataclass batch tuple into a list.
        import json
        if json.loads(json.dumps(result['operating_policy'])) != json.loads(json.dumps(p.as_dict())):
            error('POLICY_MISMATCH')
    if result.get('model', 3) != problem.model_number:
        error('MODEL_MISMATCH')
    if result.get('packaging', 'reusable') != ('reusable' if problem.reusable else 'single_use'):
        error('PACKAGING_MISMATCH')
    expected_batches = [dict(id=b.id, start=b.start, cutoff=b.cutoff, end=b.end,
                            container_demand=problem.batch_demands[b.id]) for b in p.batches]
    if result.get('batches') != expected_batches:
        error('BATCH_CALENDAR_MISMATCH')
    for b in p.batches:
        if getattr(problem, 'require_productive_batches', False) and not problem.batch_demands[b.id]:
            error('EMPTY_BATCH_DELIVERY', batch=b.id)
        transfer(loading_starts[b.id], 'clean', 'forward', problem.batch_demands[b.id], f'issue:{b.id}')

    e1_d, e1_p = defaultdict(list), defaultdict(list)
    e2_d, e2_p = defaultdict(list), defaultdict(list)
    returns, releases, vehicles = [], [], defaultdict(list)
    distances = {'e1': 0.0, 'e2': 0.0}
    waiting = {'e1': 0, 'e2': 0}
    duration = {'e1': 0, 'e2': 0}
    route_ids = set()
    precedence = []
    task_vertices = {}

    for layer in ('e1', 'e2'):
        for index, trip in enumerate(result[f'{layer}_trips']):
            rid = trip['trip_id']
            if not isinstance(rid, str) or not rid or rid in route_ids:
                error('DUPLICATE_TRIP_ID', trip=rid)
            route_ids.add(rid)
            vehicle = trip['vehicle_id']
            if isinstance(vehicle, bool) or not isinstance(vehicle, int):
                raise ValueError('vehicle_id must be an integer')
            departure, completed = tick(trip['departure']), tick(trip['return_complete'])
            events = trip['events']
            if not isinstance(events, list) or not events:
                error('EMPTY_TRIP', trip=rid)
                continue
            if layer == 'e1':
                batch = problem.batch_map[trip['batch_id']]
                base, cap_w, cap_v = problem.depot, f.e1_weight_cap, f.e1_volume_cap
                if not 0 <= vehicle < f.e1_count:
                    error('INVALID_VEHICLE', trip=rid)
                key = ('e1', vehicle)
                slot = problem.batch_index[batch.id]
                delivery_events = [e for e in events if e['kind'] == 'd']
                if delivery_events and departure != loading_starts[batch.id]+p.e1_load_time:
                    error('E1_PHASE_VIOLATION', trip=rid, detail='delivery departure must follow scheduled loading')
                if not delivery_events and departure < batch.cutoff:
                    error('E1_PHASE_VIOLATION', trip=rid, detail='pickup-only departure precedes cutoff')
                if completed > batch.end:
                    error('RETURN_DEADLINE', trip=rid, deadline=batch.end, actual=completed)
                load_w = sum(problem.weights[e['site_id'], batch.id] for e in delivery_events)
                load_v = sum(problem.demands[e['site_id'], batch.id]*f.container_volume for e in delivery_events)
                vehicle_begin = loading_starts[batch.id] if delivery_events else departure
                unload, turnaround = p.depot_return_unload_time, p.e1_turnaround_time
            else:
                base = trip['satellite_id']
                if base not in problem.satellites or not 0 <= vehicle < f.e2_per_sat_count:
                    error('INVALID_VEHICLE', trip=rid)
                key = ('e2', base, vehicle)
                slot = trip['trip_index']
                if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < p.e2_max_trips_per_vehicle:
                    error('INVALID_TRIP_SLOT', trip=rid)
                cap_w, cap_v = f.e2_weight_cap, f.e2_volume_cap
                delivery_events = [e for e in events if e['kind'] == 'd']
                delivery_batches = {problem.order_map[e['order_id']].batch for e in delivery_events}
                if len(delivery_batches) > 1:
                    error('MIXED_DELIVERY_BATCHES', trip=rid)
                load_w = sum(problem.task(e['order_id'], 'd')[1] for e in delivery_events)
                load_v = sum(problem.task(e['order_id'], 'd')[2] for e in delivery_events)
                vehicle_begin = departure-(p.e2_dispatch_prep_time if delivery_events else 0)
                if vehicle_begin < p.cycle_start:
                    error('E2_PREPARATION_BEFORE_CYCLE', trip=rid)
                unload, turnaround = p.e2_return_unload_time, p.e2_turnaround_time
            vehicles[key].append((slot, vehicle_begin, completed, rid, turnaround))
            previous, current, seen_pick, picked = base, departure, False, 0
            previous_vertex = ('start', rid)

            def capacity():
                if load_w < -1e-6 or load_w > cap_w+1e-6:
                    error('WEIGHT_CAPACITY', trip=rid, load=load_w, capacity=cap_w)
                if load_v < -1e-6 or load_v > cap_v+1e-6:
                    error('VOLUME_CAPACITY', trip=rid, load=load_v, capacity=cap_v)

            capacity()
            for event_index, event in enumerate(events):
                vertex = ('event', rid, event_index)
                precedence.append((previous_vertex, vertex))
                previous_vertex = vertex
                kind, start = event['kind'], tick(event['start'])
                if kind not in problem.task_kinds:
                    raise ValueError('Task kind must be d or p')
                if layer == 'e1':
                    site = event['site_id']
                    service = problem.service(site)
                    n = event['quantity']
                    if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
                        raise ValueError('E1 task quantity must be a positive integer')
                    if kind == 'd':
                        if seen_pick or start+service > batch.cutoff:
                            error('E1_PHASE_VIOLATION', trip=rid, site=site)
                        if n != problem.demands[site, batch.id]:
                            error('DELIVERY_AMOUNT_MISMATCH', trip=rid, site=site)
                        e1_d[site, batch.id].append((start+service, rid))
                        w, v = problem.weights[site, batch.id], n*f.container_volume
                    else:
                        if not seen_pick:
                            waiting[layer] += max(0, batch.cutoff-current)
                            current = max(current, batch.cutoff)
                        seen_pick = True
                        w, v = n*f.container_weight, n*f.container_volume
                        e1_p[site, batch.id].append((n, start, rid))
                        transfer(start, f'sat:{site}', 'e1_empty', n, rid)
                        picked += n
                else:
                    o, w, v = problem.task(event['order_id'], kind)
                    task_vertices[o.id, kind] = vertex
                    site, service, n = o.customer, o.service_time, o.count
                    if o.satellite != base:
                        error('WRONG_SATELLITE', trip=rid, order=o.id)
                    if event.get('quantity', n) != n:
                        error('ORDER_AMOUNT_MISMATCH', trip=rid, order=o.id)
                    tw = o.delivery_window if kind == 'd' else o.pickup_window
                    if not tw[0] <= start <= tw[1]:
                        error('TIME_WINDOW', trip=rid, order=o.id, kind=kind)
                    if kind == 'd':
                        if len(p.batches) == 1 and start+service > p.batches[0].cutoff:
                            error('DELIVERY_PHASE_VIOLATION', trip=rid, order=o.id)
                        e2_d[o.id].append((start, rid))
                        ready = e1_d[o.satellite, o.batch]
                        if len(ready) != 1 or departure < ready[0][0]+p.e2_dispatch_prep_time:
                            error('DELIVERY_SYNCHRONIZATION', trip=rid, order=o.id)
                        transfer(start+service, 'forward', 'customer', n, f'{o.id}:d')
                    else:
                        e2_p[o.id].append((start, rid))
                        transfer(start+service, 'customer', 'e2_empty', n, f'{o.id}:p')
                        picked += n
                arrival = current+problem.travel(previous, site)
                if start < arrival:
                    error('TRAVEL_TIME', trip=rid, site=site, earliest=arrival, actual=start)
                waiting[layer] += max(0, start-arrival)
                distances[layer] += problem.distance(previous, site)
                current, previous = start+service, site
                load_w += w if kind == 'p' else -w
                load_v += v if kind == 'p' else -v
                capacity()
            earliest_return = current+problem.travel(previous, base)+unload
            precedence.append((previous_vertex, ('end', rid)))
            if completed < earliest_return:
                error('RETURN_BEFORE_ARRIVAL', trip=rid, earliest=earliest_return, actual=completed)
            waiting[layer] += max(0, completed-earliest_return)
            distances[layer] += problem.distance(previous, base)
            duration[layer] += completed-vehicle_begin
            if completed+turnaround > p.cycle_end:
                error('CYCLE_BOUNDARY_MISMATCH', trip=rid, detail='vehicle not ready at cycle end')
            if layer == 'e2':
                returns.append(dict(trip_id=rid, satellite_id=base, time=completed, quantity=picked))
                transfer(completed, 'e2_empty', f'sat:{base}', picked, rid)
            else:
                transfer(completed, 'e1_empty', 'dirty', picked, rid)
                if picked:
                    clean = completed+p.cleaning_duration
                    releases.append(dict(trip_id=rid, quantity=picked, unload_complete=completed, clean_ready=clean))
                    transfer(clean, 'dirty', 'clean', picked, rid)
                    if clean > p.cycle_end:
                        error('TERMINAL_DIRTY', trip=rid, clean_ready=clean)

    for (s, b), n in problem.demands.items():
        if len(e1_d[s, b]) != (1 if n else 0):
            error('E1_DELIVERY_COVERAGE', satellite=s, batch=b)
    for o in problem.orders:
        for kind, covered in ((k, e2_d[o.id] if k == 'd' else e2_p[o.id]) for k in problem.task_kinds):
            if len(covered) != 1:
                error('MISSING_TASK' if not covered else 'DUPLICATE_TASK', order=o.id, kind=kind)
        if len(e2_d[o.id]) == len(e2_p[o.id]) == 1:
            precedence.append((task_vertices[o.id, 'd'], task_vertices[o.id, 'p']))
            if e2_p[o.id][0][0] < e2_d[o.id][0][0]+o.service_time+o.min_use_time:
                error('CUSTOMER_PRECEDENCE', order=o.id)
    for key, rows in vehicles.items():
        rows.sort()
        if len({r[0] for r in rows}) != len(rows):
            error('DUPLICATE_TRIP_SLOT', vehicle=list(key))
        for previous, following in zip(rows, rows[1:]):
            precedence.append((('end', previous[3]), ('start', following[3])))
            if following[1] < previous[2]+previous[4]:
                error('VEHICLE_OVERLAP', before=previous[3], after=following[3])

    # Temporal >= constraints alone cannot detect causal cycles when every
    # service/travel duration on a cycle is zero.
    outgoing, indegree = defaultdict(list), Counter()
    vertices = set()
    for left, right in precedence:
        outgoing[left].append(right)
        indegree[right] += 1
        vertices.update((left, right))
    queue = [v for v in vertices if not indegree[v]]
    visited = 0
    while queue:
        left = queue.pop()
        visited += 1
        for right in outgoing[left]:
            indegree[right] -= 1
            if not indegree[right]:
                queue.append(right)
    if visited != len(vertices):
        error('CAUSAL_CYCLE')

    snapshots = []
    for s in problem.satellites:
        previous_cutoff = p.cycle_start-1
        for b in p.batches:
            eligible = [r for r in returns if r['satellite_id'] == s and r['quantity']
                        and previous_cutoff < r['time'] <= b.cutoff]
            q = sum(r['quantity'] for r in eligible)
            actual = e1_p[s, b.id]
            actual_q = sum(row[0] for row in actual)
            if q != actual_q or len(actual) != (1 if q else 0):
                error('CUTOFF_ELIGIBLE_LEFT_BEHIND' if actual_q < q else 'PICKUP_AMOUNT_MISMATCH',
                      satellite=s, batch=b.id, expected=q, actual=actual_q, visits=len(actual))
            snapshots.append(dict(satellite_id=s, batch_id=b.id, cutoff=b.cutoff,
                                  quantity=q, eligible_events=sorted(r['trip_id'] for r in eligible),
                                  pickup_trip=actual[0][2] if len(actual) == 1 else None,
                                  pickup_time=actual[0][1] if len(actual) == 1 else None))
            previous_cutoff = b.cutoff
    if getattr(problem, 'require_productive_batches', False):
        for b in p.batches:
            if not sum(row[0] for s in problem.satellites for row in e1_p[s, b.id]):
                error('EMPTY_BATCH_RECOVERY', batch=b.id)
    for r in returns:
        if r['quantity'] and r['time'] > p.batches[-1].cutoff:
            error('TERMINAL_OUTSTANDING', trip=r['trip_id'], time=r['time'])

    stocks, issued, required_pool = [], 0, 0
    for b in p.batches if problem.reusable else ():
        loading = loading_starts[b.id]
        available = p.container_pool_size-issued+sum(r['quantity'] for r in releases if r['clean_ready'] <= loading)
        n = problem.batch_demands[b.id]
        required_pool = max(required_pool, p.container_pool_size-available+n)
        if available < n:
            future = [r['clean_ready'] for r in releases if r['clean_ready'] > loading]
            error('CLEAN_STOCK_SHORTAGE', batch=b.id, time=loading, needed=n, available=available,
                  shortage=n-available, next_release=min(future) if future else None)
        stocks.append(dict(batch_id=b.id, time=loading, available=available, demand=n, after=available-n))
        issued += n

    in_cycle = [e for e in ledger_events if e['time'] <= p.cycle_end]
    terminal, ledger, ledger_errors = replay_ledger(in_cycle, p.container_pool_size if problem.reusable else 0, problem.satellites)
    errors.extend(ledger_errors)
    if terminal['clean'] != (p.container_pool_size if problem.reusable else 0) or any(v for k, v in terminal.items() if k != 'clean'):
        error('TERMINAL_OUTSTANDING', states=terminal)
    periodic = dict(valid=False, cycles=2, terminal_state=None)
    if repeat and not errors:
        period = p.cycle_end-p.cycle_start
        terminal2, _, errors2 = replay_ledger(in_cycle, p.container_pool_size if problem.reusable else 0, problem.satellites, 2, period)
        for rows in vehicles.values():
            if rows[0][1]+period < rows[-1][2]+rows[-1][4]:
                errors2.append(dict(code='VEHICLE_OVERLAP', before=rows[-1][3], after=rows[0][3]))
        if terminal2 != terminal:
            errors2.append(dict(code='CYCLE_BOUNDARY_MISMATCH'))
        errors.extend(errors2)
        periodic = dict(valid=not errors2, cycles=2, terminal_state=terminal2)
        if not problem.reusable:
            periodic['scope'] = 'vehicles_and_delivery_service_with_fresh_single_use_packages_each_cycle'
    total_distance = sum(distances.values())
    claimed = result.get('info', {}).get('objective')
    if claimed is not None and (not math.isfinite(claimed) or abs(claimed-total_distance) > 1e-5*max(1, total_distance)):
        error('OBJECTIVE_MISMATCH', claimed=claimed, recomputed=total_distance)
    for field, calculated in (('cutoff_snapshots', snapshots), ('depot_releases', releases)):
        if field in result and result[field] != calculated:
            error('DERIVED_STATE_MISMATCH', field=field)
    peaks = {s: max([0]+[row['states'][f'sat:{s}'] for row in ledger]) for s in problem.satellites}
    return dict(valid=not errors, errors=errors, objective=total_distance,
                distances=distances, cutoff_snapshots=snapshots, depot_releases=releases,
                clean_stock=stocks, minimum_pool_for_schedule=required_pool,
                container_ledger=ledger, terminal_state=terminal, periodic_replay=periodic,
                satellite_peak_inventory=peaks, container_balance=dict(
                    issued=issued if problem.reusable else problem.total, e2_unloaded=sum(r['quantity'] for r in returns),
                    returned_to_depot=sum(r['quantity'] for r in releases), pool=p.container_pool_size if problem.reusable else 0),
                time_metrics=dict(waiting_ticks=waiting, working_ticks=duration,
                                  working_minutes={k: p.minutes(v) for k, v in duration.items()}),
                e1_vehicle_count=len([k for k in vehicles if k[0] == 'e1']),
                e2_vehicle_count=len([k for k in vehicles if k[0] == 'e2']),
                e1_trip_count=len(result['e1_trips']), e2_trip_count=len(result['e2_trips']))
