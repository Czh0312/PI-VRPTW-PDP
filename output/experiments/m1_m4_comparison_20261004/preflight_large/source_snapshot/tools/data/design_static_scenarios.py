"""Deterministic scenario design, independent of optimization results.

Reference tours certify appointment compatibility only. They are disclosed in
provenance, never selected by an ALNS/exact objective or used as an optimal label.
"""
from collections import Counter, defaultdict
from itertools import combinations
import hashlib
import math


def key(namespace, identifier):
    return hashlib.sha256(f'operational_scenarios_v2:{namespace}:{identifier}'.encode()).hexdigest()


def draw(namespace, identifier, values):
    return values[int(key(namespace, identifier)[:12], 16) % len(values)]


def euclidean(a, b):
    return math.hypot(float(a['x'])-float(b['x']), float(a['y'])-float(b['y']))


def select_territories(rows, count, cap_ratio):
    """Keep source facilities; choose coverage by geometry, then cap territories.

    All models receive the same available facilities. Far/less flexible customers
    are assigned first; no optimization solver or performance result is involved.
    """
    customers = [r for r in rows if r['node_type'] == 'c']
    candidates = sorted((r for r in rows if r['node_type'] == 's'), key=lambda r: r['node_id'])
    distances = {(c['node_id'], s['node_id']): euclidean(c, s) for c in customers for s in candidates}
    def score(sites):
        nearest = [min(distances[c['node_id'], s['node_id']] for s in sites) for c in customers]
        return sum(nearest), max(nearest), tuple(s['node_id'] for s in sites)
    selected = min(combinations(candidates, count), key=score)
    ids = {s['node_id'] for s in selected}
    capacity = math.ceil(len(customers)/count*cap_ratio)
    loads, assignment = Counter(), {}
    # Seed each retained service territory with a distinct nearby customer;
    # otherwise the subsequent capacity balancing can empty a selected site.
    for site in sorted(ids, key=lambda s: (min(distances[c['node_id'], s] for c in customers), s)):
        customer = min((c['node_id'] for c in customers if c['node_id'] not in assignment),
                       key=lambda c: (distances[c, site], c))
        assignment[customer] = site
        loads[site] = 1
    ranked = sorted(customers, key=lambda c: (-min(distances[c['node_id'], s] for s in ids), c['node_id']))
    for c in ranked:
        if c['node_id'] in assignment: continue
        s = min((s for s in ids if loads[s] < capacity), key=lambda s: (distances[c['node_id'], s], s))
        assignment[c['node_id']] = s
        loads[s] += 1
    if any(not loads[s] for s in ids):
        raise ValueError('Selected territory has no customers; revise the published spatial design')
    return [{**r, **({'assigned_sat': assignment[r['node_id']]} if r['node_type']=='c' else {})}
            for r in rows if r['node_type']!='s' or r['node_id'] in ids], capacity


def design_orders(rows, family, profile, fleet, design, *, road_times=None, lade=False):
    """Create appointments around a capacity-feasible, reproducible reference.

    Batch allocation, use durations and return appointments have separate hash
    namespaces. Vehicle counts/capacities are fixed before this function runs.
    No retry, customer dropping, window extension or per-case fleet expansion.
    """
    calendar, a = design['calendar'], design['appointments']
    nodes = {r['node_id']: r for r in rows}
    depot = next(r['node_id'] for r in rows if r['node_type']=='d')
    def travel(x, y):
        minutes = road_times[x][y] if road_times else euclidean(nodes[x], nodes[y])/fleet['speed']
        return math.ceil(minutes*60-1e-9)/60  # same conservative seconds as the model
    groups = defaultdict(list)
    for r in rows:
        if r['node_type']=='c': groups[r['assigned_sat']].append(r)
    orders, cohorts, routes, timing = [], {}, [], {}
    for site, customers in sorted(groups.items()):
        customers.sort(key=lambda r: key(family+':cohort', r['node_id']))
        phases = defaultdict(list)
        for i, r in enumerate(customers):
            stage = i % 3
            oid = r['node_id']+'_order'
            phases[stage].append(r)
            cohorts[oid] = stage
        ready = [calendar['start']]*fleet['vehicles_per_satellite']
        for stage in range(3):
            anchor = calendar['start']+stage*calendar['span']
            remaining = {r['node_id']: r for r in phases[stage]}
            while remaining:
                selected, load_w, load_v, position = [], 0, 0, site
                while remaining and len(selected) < a['max_stops_per_reference_trip']:
                    choices = []
                    for cid, r in remaining.items():
                        n = int(r['delivery_container_count']) if lade else math.ceil(float(r['delivery_demand'])/design['zhou']['goods_per_container'])
                        w, v = float(r['delivery_demand'])+n*fleet['container_weight'], n*fleet['container_volume']
                        if load_w+w <= fleet['E2_Qv']+1e-8 and load_v+v <= fleet['E2_Cv']+1e-8:
                            choices.append((travel(position, cid), cid, n, w, v))
                    if not choices: break
                    _, cid, n, w, v = min(choices)
                    selected.append((remaining.pop(cid), n))
                    load_w += w; load_v += v; position = cid
                if not selected: raise ValueError('Individual order exceeds fixed vehicle capacity')
                vehicle = min(range(len(ready)), key=lambda v: (ready[v], v))
                depart = max(ready[vehicle]+1, anchor+2+travel(depot, site)+float(nodes[site]['service_time'])+1)
                time, position, stops = depart, site, []
                for r, n in selected:
                    cid, oid = r['node_id'], r['node_id']+'_order'
                    time += travel(position, cid)
                    use = draw(family+f':use:{profile}', oid, a['use_minutes_by_profile'][str(profile)])
                    delay = draw(family+f':appointment:{profile}', oid, a['collection_delay_minutes'])
                    timing[oid] = dict(delivery=time, use=use, delay=delay)
                    stops.append((r, n, oid))
                    time += float(r['service_time']); position = cid
                for r, n, oid in stops:
                    cid, service = r['node_id'], float(r['service_time'])
                    time = max(time+travel(position, cid), timing[oid]['delivery']+service+timing[oid]['use']+timing[oid]['delay'])
                    timing[oid]['pickup'] = time
                    time += service; position = cid
                finish = time+travel(position, site)+1
                ready[vehicle] = finish
                routes.append(dict(satellite=site, vehicle=vehicle, depart_after=round(depart, 8),
                                   tasks=[(oid, 'd') for _, _, oid in stops]+[(oid, 'p') for _, _, oid in stops]))
                final_cutoff = (calendar['start']+3*calendar['span']+calendar['tail']
                                -calendar.get('closing_tail', calendar['tail'])
                                -calendar.get('closing_recovery', calendar['recovery']))
                if finish > final_cutoff+1e-6:
                    raise ValueError(f'{family} profile{profile}: reference return {finish:.2f} exceeds final cutoff {final_cutoff}; no automatic relaxation')
                for r, n, oid in stops:
                    t = timing[oid]; service=float(r['service_time'])
                    # Integral-minute windows contain the witness; the declared
                    # delivery deadline already respects the latest pickup.
                    pdue = min(math.ceil(t['pickup']+a['pickup_late_slack']),
                               math.floor(final_cutoff-service-travel(r['node_id'], site)-1))
                    ddue = min(math.floor(t['delivery']+a['delivery_late_slack']),
                               math.floor(pdue-service-t['use']))
                    orders.append(dict(order_id=oid, customer_id=r['node_id'], satellite_id=site,
                        container_count=n, goods_weight=float(r['delivery_demand']),
                        delivery_ready=max(anchor, math.floor(t['delivery']-a['delivery_early_slack'])),
                        delivery_due=ddue,
                        pickup_ready=max(anchor, math.floor(t['pickup']-a['pickup_early_slack'])), pickup_due=pdue,
                        service_time=service, min_use_time=t['use']))
    return orders, cohorts, dict(reference_e2_routes=routes, reference_time_unit='minutes',
                                 reference_purpose='Feasibility construction only; no objective optimization or performance filtering.',
                                 reference_rule='Per-territory hash cohorts; nearest feasible capacity groups; earliest available vehicle; delivery then collection.',
                                 usage_profile=profile,
                                 appointment_rule='Separate hash streams for cohort, use and collection delay; window slack around disclosed constructive service times.')
