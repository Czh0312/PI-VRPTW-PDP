"""Independent M6 audit: no optimization or schedule-construction imports.

Each empty return carries order IDs only as custody tokens. Identical physical
containers are allocated through the common clean-stock ledger after washing.
"""
from collections import Counter, defaultdict
import math

from .instance import VERSION


def audit_solution(problem, result, *, repeat=True):
    errors = []
    def fail(code, **details):
        errors.append(dict(code=code, **details))
    try:
        return _audit(problem, result, errors, fail, repeat)
    except (KeyError, TypeError, ValueError, IndexError, OverflowError) as exc:
        fail('MALFORMED_SOLUTION', detail=str(exc))
        return dict(valid=False, errors=errors, periodic_replay=dict(valid=False))


def _audit(problem, result, errors, fail, repeat):
    p, f = problem.operating_policy, problem.fleet
    if result.get('model') != 6 or result.get('policy_version') != VERSION:
        fail('MODEL_IDENTITY')
    def tick(t):
        if isinstance(t, bool) or not isinstance(t, int):
            raise ValueError('Solution times must be integer ticks')
        return t
    events, vehicles = [], defaultdict(list)
    tasks = {layer: {} for layer in ('e1', 'e2')}
    trip_ids, distances, working = set(), dict(e1=0., e2=0.), dict(e1=0, e2=0)
    issues, clean_returns = [], []
    trip_counts = Counter()
    def transfer(time, source, destination, quantity, label):
        if quantity:
            events.append((time, source, destination, quantity, label))
    for layer in ('e1', 'e2'):
        for trip in result[layer+'_trips']:
            rid = trip['trip_id']
            if rid in trip_ids:
                fail('DUPLICATE_TRIP', trip=rid)
            trip_ids.add(rid)
            v = trip['vehicle_id']
            limit = f.e1_count if layer == 'e1' else f.e2_per_sat_count
            if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v < limit:
                fail('INVALID_VEHICLE', trip=rid)
            base = problem.depot if layer == 'e1' else trip['satellite_id']
            if layer == 'e2' and base not in problem.satellites:
                fail('WRONG_SATELLITE', trip=rid)
            key = (layer, base, v)
            trip_counts[key] += 1
            begin, depart, end = (tick(trip[k]) for k in ('loading_start', 'departure', 'return_complete'))
            visits = trip['events']
            if not visits:
                fail('EMPTY_TRIP', trip=rid)
            deliveries = []
            for ev in visits:
                if ev['kind'] == 'd':
                    deliveries.extend(ev['order_ids'] if layer == 'e1' else [ev['order_id']])
            prep = (p.e1_load_time if layer == 'e1' else p.e2_dispatch_prep_time) if deliveries else 0
            if begin < p.cycle_start or depart < begin+prep:
                fail('LOADING_TIME', trip=rid)
            turn = p.e1_turnaround_time if layer == 'e1' else p.e2_turnaround_time
            if end+turn > p.cycle_end:
                fail('VEHICLE_NOT_READY_AT_END', trip=rid)
            vehicles[key].append((begin, end+turn, rid))
            working[layer] += end-begin
            load_w = sum(problem.task(oid, 'd')[1] for oid in deliveries)
            load_v = len(deliveries)*f.container_volume
            cap_w = f.e1_weight_cap if layer == 'e1' else f.e2_weight_cap
            cap_v = f.e1_volume_cap if layer == 'e1' else f.e2_volume_cap
            def capacity():
                if not -1e-7 <= load_w <= cap_w+1e-7 or not -1e-7 <= load_v <= cap_v+1e-7:
                    fail('CAPACITY', trip=rid, weight=load_w, volume=load_v)
            capacity()
            if layer == 'e1':
                issues.append((begin, len(deliveries), rid))
                transfer(begin, 'clean', 'e1_full', len(deliveries), rid)
            else:
                transfer(begin, 'sat_full:'+base, 'e2_full', len(deliveries), rid)
            previous, current, picked = base, depart, []
            for ev in visits:
                kind, start = ev['kind'], tick(ev['start'])
                if kind not in ('d', 'p'):
                    raise ValueError('Unknown event kind')
                oids = ev['order_ids'] if layer == 'e1' else [ev['order_id']]
                if not oids or len(oids) != len(set(oids)):
                    fail('EVENT_COVERAGE', trip=rid)
                if layer == 'e1':
                    site = ev['site_id']
                    duration = problem.service(site)
                    if ev.get('quantity', len(oids)) != len(oids):
                        fail('QUANTITY_MISMATCH', trip=rid)
                else:
                    o = problem.order_map[oids[0]]
                    site, duration = o.customer, o.service_time
                    lo, hi = o.delivery_window if kind == 'd' else o.pickup_window
                    if not lo <= start <= hi:
                        fail('TIME_WINDOW', trip=rid, order=o.id)
                arrival = current+problem.travel(previous, site)
                if start < arrival:
                    fail('TRAVEL_TIME', trip=rid, site=site, earliest=arrival, actual=start)
                distances[layer] += problem.distance(previous, site)
                for oid in oids:
                    o, w, volume = problem.task(oid, kind)
                    if o.satellite != (site if layer == 'e1' else base):
                        fail('WRONG_SATELLITE', order=oid)
                    if (oid, kind) in tasks[layer]:
                        fail('DUPLICATE_TASK', layer=layer, order=oid, kind=kind)
                    tasks[layer][oid, kind] = dict(start=start, finish=start+duration,
                                                   begin=begin, end=end, trip=rid)
                    load_w += w if kind == 'p' else -w
                    load_v += volume if kind == 'p' else -volume
                    if kind == 'p':
                        picked.append(oid)
                capacity()
                if layer == 'e1':
                    if kind == 'd':
                        transfer(start+duration, 'e1_full', 'sat_full:'+site, len(oids), rid)
                    else:
                        transfer(start, 'sat_empty:'+site, 'e1_empty', len(oids), rid)
                elif kind == 'd':
                    transfer(start+duration, 'e2_full', 'customer', 1, oids[0])
                else:
                    transfer(start+duration, 'customer', 'e2_empty', 1, oids[0])
                current, previous = start+duration, site
            unload = p.depot_return_unload_time if layer == 'e1' else p.e2_return_unload_time
            if end < current+problem.travel(previous, base)+unload:
                fail('RETURN_BEFORE_UNLOADED', trip=rid)
            distances[layer] += problem.distance(previous, base)
            if layer == 'e1':
                ready = end+p.cleaning_duration
                transfer(end, 'e1_empty', 'dirty', len(picked), rid)
                transfer(ready, 'dirty', 'clean', len(picked), rid)
                clean_returns.append((ready, len(picked), rid))
                if picked and ready > p.cycle_end:
                    fail('TERMINAL_DIRTY', trip=rid, clean_ready=ready)
            else:
                transfer(end, 'e2_empty', 'sat_empty:'+base, len(picked), rid)
    for key, trips in vehicles.items():
        trips.sort()
        for a, b in zip(trips, trips[1:]):
            if b[0] < a[1]:
                fail('VEHICLE_OVERLAP', before=a[2], after=b[2])
        cap = problem.e1_max_trips if key[0] == 'e1' else p.e2_max_trips_per_vehicle
        if len(trips) > cap:
            fail('TRIP_LIMIT', vehicle=key)
    if len(result['e1_trips']) > 2*problem.total:
        fail('EMPTY_OR_DUPLICATE_E1_TRIPS')
    for o in problem.orders:
        if any((o.id, k) not in tasks[l] for l in tasks for k in ('d', 'p')):
            fail('MISSING_TASK', order=o.id)
            continue
        a, b = tasks['e1'][o.id, 'd'], tasks['e2'][o.id, 'd']
        c, d = tasks['e2'][o.id, 'p'], tasks['e1'][o.id, 'p']
        if a['begin'] < (o.release_time if o.release_time is not None else p.cycle_start):
            fail('ORDER_NOT_RELEASED', order=o.id)
        if b['begin'] < a['finish']:
            fail('FULL_BOX_NOT_READY_FOR_PREPARATION', order=o.id)
        if c['start'] < b['finish']+o.min_use_time:
            fail('MIN_USE_TIME', order=o.id)
        if d['start'] < c['end']:
            fail('EMPTY_NOT_READY_FOR_LOADING', order=o.id)
    deltas = defaultdict(lambda: [0, 0])
    for t, n, _ in issues:
        deltas[t][1] += n
    for t, n, _ in clean_returns:
        deltas[t][0] += n
    required, deficit = 0, 0
    for t, (returned, issued) in sorted(deltas.items()):
        deficit += issued-returned
        required = max(required, deficit)
    if required > problem.pool_max:
        fail('POOL_LIMIT', required=required, maximum=problem.pool_max)
    stock = {'clean': required, 'dirty': 0, 'e1_full': 0, 'e1_empty': 0,
             'e2_full': 0, 'e2_empty': 0, 'customer': 0}
    stock.update({prefix+s: 0 for s in problem.satellites for prefix in ('sat_full:', 'sat_empty:')})
    ledger = []
    def replay(offset=0):
        grouped = defaultdict(list)
        for t, source, dest, n, label in events:
            grouped[t+offset].append((source, dest, n, label))
        for t, rows in sorted(grouped.items()):
            # Simultaneous completed transfers are usable by starts at this tick.
            change = Counter()
            for source, dest, n, label in rows:
                change[source] -= n; change[dest] += n
            for state, n in change.items():
                stock[state] += n
                if stock[state] < 0:
                    fail('NEGATIVE_INVENTORY', time=t, location=state)
            if sum(stock.values()) != required:
                fail('CONTAINER_CONSERVATION', time=t)
            if not offset:
                ledger.append(dict(time=t, states=dict(stock)))
    replay()
    if stock['clean'] != required or any(n for k,n in stock.items() if k != 'clean'):
        fail('TERMINAL_OUTSTANDING', states=dict(stock))
    first_terminal = dict(stock)
    periodic = dict(valid=False, cycles=2, repeat_interval=p.repeat_interval)
    if repeat and not errors:
        replay(p.repeat_interval)
        for trips in vehicles.values():
            if trips[-1][1] > trips[0][0]+p.repeat_interval:
                fail('PERIODIC_VEHICLE_OVERLAP')
        periodic['valid'] = not errors and stock == first_terminal
    cost = sum(distances[k]*problem.distance_cost[k] for k in distances)
    if problem.transport_budget is not None and cost > problem.transport_budget+1e-7:
        fail('TRANSPORT_BUDGET', cost=cost)
    claimed = result.get('container_pool_size')
    if claimed is not None and (isinstance(claimed, bool) or not isinstance(claimed, int) or not required <= claimed <= problem.pool_max):
        fail('CLAIMED_POOL', claimed=claimed, required=required)
    return dict(valid=not errors, errors=errors, minimum_pool_for_schedule=required,
                distances=distances, transport_cost=cost, container_ledger=ledger,
                terminal_state=first_terminal, periodic_replay=periodic,
                working_ticks=working, e1_trip_count=len(result['e1_trips']), e2_trip_count=len(result['e2_trips']))
