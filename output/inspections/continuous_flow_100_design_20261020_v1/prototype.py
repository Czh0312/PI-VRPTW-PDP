"""Construct one declared rolling-demand scenario; no optimizer or objective filtering."""
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import random
import sys

PROJECT = Path.cwd()
sys.path.insert(0, str(PROJECT))
from cli.route import load_config
from models.common.cycle import load_instance
from models.common.cycle.instance import MultiBatchInstance
from models.common.cycle.policy import Batch
from models.common.cycle.state import empty_result
from models.common.cycle.audit import audit_solution
from models.routing.model_5.alns.evaluator import _e1_returns

ROOT = Path(__file__).resolve().parent
SOURCE = PROJECT / 'data/formal_benchmark27_interleaved_v1'
SEED = 20261020
rng = random.Random(SEED)
config, source_fleet = load_config(SOURCE / 'model_3/large/L05/case.json', use_pool_config=False)
fleet = json.loads((PROJECT / 'output/inspections/m8_l05_100_newfleet_20261019_v1/fleet_override.json').read_text(encoding='utf-8'))
base = load_instance(config, {**fleet, 'operating_policy': source_fleet['operating_policy']})
starts = [480, 600, 720, 840, 960]
ends = starts[1:] + [1260]
cutoffs = [565, 675, 795, 915, 1182]
batches = tuple(Batch(f'b{i+1}', start*60, cutoff*60, end*60)
                for i, (start, cutoff, end) in enumerate(zip(starts, cutoffs, ends)))
policy = replace(base.operating_policy, batches=batches, container_pool_size=100)

cohorts = {}
global_counts = Counter()
for site in base.satellites:
    local = sorted((o for o in base.orders if o.satellite == site), key=lambda o:o.id)
    rng.shuffle(local)
    local_counts = Counter()
    for i, order in enumerate(local):
        tie = {wave:rng.random() for wave in range(5)}
        wave = min(range(5), key=lambda wave:(local_counts[wave],global_counts[wave],tie[wave]))
        cohorts[order.id] = wave
        local_counts[wave] += 1
        global_counts[wave] += 1

# Put one customer with a physically short first return in the opening wave.
# This satisfies the current M8 productive-first-batch contract; not a cost choice.
opening = min(base.orders, key=lambda o: (
    base.travel(base.depot, o.satellite) + 2*base.travel(o.satellite, o.customer)
    + o.service_time*2 + o.min_use_time, o.id))
old_group = cohorts[opening.id]
if old_group:
    swap = next(o for o in base.orders if o.satellite == opening.satellite and cohorts[o.id] == 0)
    cohorts[swap.id], cohorts[opening.id] = old_group, 0
release_times = {}
for wave in range(5):
    ids = sorted(oid for oid in cohorts if cohorts[oid] == wave)
    rng.shuffle(ids)
    for rank,oid in enumerate(ids):
        release_times[oid] = starts[0]*60 if wave == 0 else round((
            starts[wave-1] + (rank+.5)*(starts[wave]-starts[wave-1])/len(ids)
            + rng.uniform(-1.5,1.5))*60)
orders = [replace(o, batch=f'b{cohorts[o.id]+1}', allowed_batch_ids=tuple(b.id for b in batches),
    release_time=release_times[o.id],
    delivery_window=(480*60,1260*60), pickup_window=(480*60,1260*60)) for o in base.orders]
p = MultiBatchInstance(base.network, orders, policy, model=5)
order_map = p.order_map
result = empty_result(p, 'feasibility_witness')
deliveries = {}
returned = []
used_slots = defaultdict(int)
vehicle_ready = defaultdict(lambda: policy.cycle_start)
stats = []

def split(ids, maximum=4):
    ids = list(ids)
    groups, row, weight = [], [], 0
    for oid in ids:
        w = p.task(oid, 'd')[1]
        if row and (len(row) >= maximum or weight+w > p.fleet.e2_weight_cap):
            groups.append(row)
            row, weight = [], 0
        row.append(oid)
        weight += w
    if row:
        groups.append(row)
    return groups

def route(site, ds, ps, wave, earliest, vehicle=None):
    eligible = range(p.fleet.e2_per_sat_count) if vehicle is None else [vehicle]
    vehicle = min(eligible, key=lambda v:(max(earliest,vehicle_ready[site,v]), used_slots[site,v],v))
    departure = max(earliest, vehicle_ready[site,vehicle] + (policy.e2_dispatch_prep_time if ds else 0))
    current, at = departure, site
    waiting = 0
    tasks = [(oid,'d') for oid in ds] + [(oid,'p') for oid in ps]
    load_w = sum(p.task(oid,'d')[1] for oid in ds)
    load_v = len(ds)
    events = []
    while tasks:
        choices = []
        for oid,kind in tasks:
            order = order_map[oid]
            if kind == 'p' and oid not in deliveries:
                continue
            _,w,v = p.task(oid,kind)
            if kind == 'p' and (load_v+v > p.fleet.e2_volume_cap or load_w+w > p.fleet.e2_weight_cap):
                continue
            arrival = current+p.travel(at,order.customer)
            earliest_start = arrival if kind == 'd' else max(arrival, deliveries[oid]+order.service_time+order.min_use_time)
            # Earliest reachable task serves other customers while a pickup is not ready.
            choices.append((earliest_start, p.travel(at,order.customer), kind, oid))
        if not choices:
            raise ValueError('No causal capacity-feasible task')
        start, travel, kind, oid = min(choices)
        order = order_map[oid]
        waiting += start-current-travel
        events.append(dict(order_id=oid, kind=kind, start=start, quantity=1))
        if kind == 'd':
            deliveries[oid] = start
        _,w,v = p.task(oid,kind)
        load_w += w if kind == 'p' else -w
        load_v += v if kind == 'p' else -v
        current, at = start+order.service_time, order.customer
        tasks.remove((oid,kind))
    complete = current+p.travel(at,site)+policy.e2_return_unload_time
    slot = used_slots[site,vehicle]
    used_slots[site,vehicle] += 1
    vehicle_ready[site,vehicle] = complete + policy.e2_turnaround_time
    trip = dict(trip_id=f'e2_{site}_{vehicle}_{slot}', satellite_id=site,
        vehicle_id=vehicle, trip_index=slot, departure=departure, return_complete=complete, events=events)
    result['e2_trips'].append(trip)
    returned.append(dict(satellite=site, time=complete, quantity=len(ps)))
    stats.append(dict(site=site, wave=wave+1, deliveries=len(ds), pickups=len(ps),
        departure=departure, finish=complete, waiting=waiting))

for wave, batch in enumerate(batches):
    for vehicle, site in enumerate(p.satellites):
        current_ids = sorted(o.id for o in orders if o.satellite == site and cohorts[o.id] == wave)
        rng.shuffle(current_ids)
        previous_ids = sorted(o.id for o in orders if o.satellite == site and cohorts[o.id] == wave-1
                              and o.id != opening.id)
        supply = batch.start + policy.e1_load_time + p.travel(p.depot,site) + p.service(site) + policy.e2_dispatch_prep_time
        if wave == 0 and site == opening.satellite:
            route(site, [opening.id], [opening.id], wave, supply)
            current_ids.remove(opening.id)
        groups = split(current_ids, maximum=3 if wave == len(batches)-1 else 4)
        old_groups = [[] for _ in groups]
        for i,oid in enumerate(previous_ids):
            old_groups[i % len(groups)].append(oid)
        for i,(ds,ps) in enumerate(zip(groups,old_groups)):
            route(site, ds, ps, wave, supply + rng.randint(0,18)*60)
    if wave == len(batches)-1:
        for site in p.satellites:
            ids = sorted(o.id for o in orders if o.satellite == site and cohorts[o.id] == wave)
            for group in split(ids, maximum=3):
                ready = max(deliveries[oid]+order_map[oid].service_time+order_map[oid].min_use_time for oid in group)
                route(site, [], group, wave, ready)

quantities = {b.id: {site:0 for site in p.satellites} for b in batches}
for row in returned:
    if row['quantity']:
        batch = next((b for b in batches if row['time'] <= b.cutoff),None)
        if batch is None:
            raise ValueError(f'Last pickup misses final cutoff: {row}')
        quantities[batch.id][row['satellite']] += row['quantity']
state = dict(deliveries={b.id:[[s] if p.demands[s,b.id] else [] for s in p.satellites] for b in batches},
    pickup_order={b.id:list(p.satellites) for b in batches},
    pickup_vehicle={b.id:{s:i for i,s in enumerate(p.satellites)} for b in batches})
result['e1_trips'],issues,_ = _e1_returns(p,state,quantities)
audit = audit_solution(p,result)
payload = dict(seed=SEED, opening=opening.id, cohorts=cohorts, starts=starts, cutoffs=cutoffs,
    e1_issues=issues, valid=audit['valid'], errors=audit['errors'][:20],
    minimum_pool=audit['minimum_pool_for_schedule'], stocks=audit['clean_stock'],
    trip_stats=stats, demand_counts=dict(Counter(cohorts.values())),
    delivery_histogram=dict(sorted(Counter(t//3600 for t in deliveries.values()).items())),
    pickup_histogram=dict(sorted(Counter(event['start']//3600 for trip in result['e2_trips'] for event in trip['events'] if event['kind']=='p').items())))
(ROOT/'prototype_summary.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(ROOT/'prototype_schedule.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({key:value for key,value in payload.items() if key not in ('trip_stats','cohorts')},ensure_ascii=False,indent=2))
