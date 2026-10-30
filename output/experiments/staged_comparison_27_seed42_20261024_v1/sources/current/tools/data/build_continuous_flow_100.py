"""Construct evenly spread customer appointments; original opening goods readiness retained."""
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

ROOT = PROJECT / 'output/inspections/continuous_flow_100_design_20261020_v1'
ROOT.mkdir(parents=True, exist_ok=True)
SOURCE = PROJECT / 'data/formal_benchmark27_interleaved_v1'
SEED = 20261020
rng = random.Random(SEED)
config, source_fleet = load_config(SOURCE / 'model_3/large/L05/case.json', use_pool_config=False)
fleet = json.loads((SOURCE / 'shared/large/L05/fleet.json').read_text(encoding='utf-8'))
fleet_profile = json.loads((PROJECT / 'config/experiments/benchmark27_one_box_v1/fleet_by_size_draft.json').read_text(encoding='utf-8'))
fleet.update(fleet_profile['by_size']['large'])
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
release_times = {o.id:o.release_time for o in base.orders}
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
            earliest_start = arrival if kind == 'd' else max(arrival, deliveries[oid]+order.service_time+order.min_use_time+120)
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

if '--publish' in sys.argv:
    import shutil
    from models.common.cycle.joint import lift_result
    from models.common.cycle.calendar import attach_calendar_decisions
    from models.cost.comparison import evaluate_comparison_cost

    destination = PROJECT / 'data/scenarios/continuous_flow_100_v1'
    if destination.exists():
        raise ValueError('Refusing to overwrite an existing scenario')
    if not audit['valid'] or issues or not audit['periodic_replay']['valid']:
        raise ValueError('Physical construction failed; no scenario published')

    def read(path):
        return json.loads(path.read_text(encoding='utf-8'))
    def write(path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    def rel(path):
        return path.relative_to(PROJECT).as_posix()

    original_rows = read(SOURCE / 'shared/large/L05/reference_orders.json')
    event_times = {(e['order_id'],e['kind']):e['start'] for trip in result['e2_trips'] for e in trip['events']}
    rows = deepcopy(original_rows)
    for row in rows:
        oid = row['order_id']
        keyed = random.Random(int(hashlib.sha256(f'{SEED}:windows:{oid}'.encode()).hexdigest(),16))
        d,pu = event_times[oid,'d'],event_times[oid,'p']
        row['delivery_ready'] = max(480,math.floor(d/60)-keyed.randint(10,25))
        row['delivery_due'] = min(math.ceil(d/60)+keyed.randint(10,25),
            math.floor(pu/60)-row['service_time']-row['min_use_time'])
        row['pickup_ready'] = max(math.floor(pu/60)-keyed.randint(10,25),
            row['delivery_due']+row['service_time']+row['min_use_time'])
        row['pickup_due'] = min(1260,math.ceil(pu/60)+keyed.randint(10,25))
        assert row['delivery_ready']*60 <= d <= row['delivery_due']*60
        assert row['pickup_ready']*60 <= pu <= row['pickup_due']*60
        assert row['pickup_ready'] >= row['delivery_due']+row['service_time']+row['min_use_time']
    window_fields={'delivery_ready','delivery_due','pickup_ready','pickup_due'}
    preserved = tuple(key for key in original_rows[0] if key not in window_fields)
    assert [{key:row[key] for key in preserved} for row in rows] == [
        {key:row[key] for key in preserved} for row in original_rows]
    assert len(rows)==len({row['customer_id'] for row in rows})==100
    assert all(row['container_count']==1 for row in rows)
    common = destination / 'shared'
    common.mkdir(parents=True)
    shutil.copy2(SOURCE / 'shared/large/L05/network.csv',common / 'network.csv')
    shutil.copy2(SOURCE / 'shared/large/L05/economic.json',common / 'economic.json')
    write(common / 'fleet.json',fleet)
    write(common / 'reference_orders.json',rows)
    write(common / 'fleet_profile_snapshot.json',fleet_profile)
    policy_input = read(SOURCE / 'model_3/large/L05/policy.json')
    policy_input['description'] = 'Reference replenishment plan for rolling demand. M8 freely optimizes K, boundaries, cutoffs and assignments.'
    policy_input['batches'] = [dict(id=b.id,delivery_duration=(b.cutoff-b.start)/60,
        return_duration=(b.end-b.cutoff)/60) for b in batches]
    contracts = dict(scenario_id='continuous_flow_100_v1',date='2026-10-20',seed=SEED,
        base_case='L05',source_dataset=rel(SOURCE),customers=100,
        business='All 100 orders remain ready at 08:00. Only customer delivery/pickup appointment endpoints change; service demand is more evenly spread across the working day.',
        time_distribution='Five balanced appointment cohorts, independently shuffled within satellites; feasible randomized appointment widths. Reference dispatch calendar is not binding for M8.',
        reference_load_minutes=starts,reference_demands=[20]*5,
        changed_fields=['delivery_ready','delivery_due','pickup_ready','pickup_due'],
        preserved_fields=list(preserved)+['coordinates','fixed satellite ownership','fleet','cycle','cleaning duration','economic coefficients'],
        opening_return_rule='First appointment includes a short physically possible return to satisfy current M8 productive-first-batch contract.',
        distribution_disclosure='Synthetic and feasibility-conditioned; not independent empirical sampling or a claim of exact uniformity.',
        selection='No ALNS, MILP, costs, desired model ranking or seed screening used to select the instance.',
        source_orders_sha256=digest(SOURCE / 'shared/large/L05/reference_orders.json'),
        generator=rel(Path(__file__).resolve()),generator_sha256=digest(Path(__file__).resolve()),
        m3_scope='M3 remains a valid matched single-batch baseline: all goods ready at opening. No rolling release or required business dispatches are introduced.')
    write(common / 'scenario.json',contracts)

    case_paths = {}
    for model in (3,5,8):
        folder = destination / f'model_{model}'
        model_rows=read(SOURCE/f'model_{model}/large/L05/orders.json')
        window_map={row['order_id']:{key:row[key] for key in window_fields} for row in rows}
        for row in model_rows:
            row.update(window_map[row['order_id']])
            if model==5:
                row['delivery_batch_id']=f'b{cohorts[row["order_id"]]+1}'
                row['allowed_batch_ids']=[b.id for b in batches]
        write(folder / 'orders.json',model_rows)
        write(folder / 'policy.json',policy_input if model==5 else read(SOURCE/f'model_{model}/large/L05/policy.json'))
        case = dict(schema_version=2,case_name=f'continuous_flow_100_v1_M{model}',model=model,solver='alns',
            data=rel(common/'network.csv'),routing_config=rel(common/'fleet.json'),
            economic_config=rel(common/'economic.json'),dataset_metadata=rel(common/'scenario.json'),
            orders=rel(folder/'orders.json'),operating_policy=rel(folder/'policy.json'),
            container_pool_size=100,exact_backend='joint_batch_indexed' if model==8 else 'multibatch_indexed',
            exact_time=300,alns_time=120,alns_iter=1000000000,seed=42,save_plots=False)
        if model==8:
            case['joint_batch_options']=dict(schedule_mode='flexible_cycle',
                batch_count_candidates=list(range(1,101)),min_phase_minutes=1,pool_min=1,pool_max=100)
        write(folder/'case.json',case)
        case_paths[f'M{model}']=folder/'case.json'
        if model==8:
            for label,bounds in (('pool80',(1,80)),('fixed_pool100',(100,100))):
                variant=deepcopy(case)
                variant['case_name']+=f'_{label}'
                variant['joint_batch_options'].update(pool_min=bounds[0],pool_max=bounds[1])
                write(folder/f'case_{label}.json',variant)
                case_paths[label]=folder/f'case_{label}.json'

    # Recreate the public inputs and independently audit, rather than certifying
    # only the broad-window construction that was used to place appointments.
    cfg5,fl5=load_config(case_paths['M5'],use_pool_config=False)
    final5=load_instance(cfg5,fl5)
    raw=deepcopy(result)
    raw['operating_policy']=final5.operating_policy.as_dict()
    raw['info']['objective']=None
    checked5=audit_solution(final5,raw)
    assert checked5['valid'] and checked5['periodic_replay']['valid'],checked5['errors']
    raw['info'].update(status='FEASIBLE',objective=checked5['objective'],proven_optimal=False,
        proof_scope='Constructed physical witness, not an algorithm result')
    raw.update(validation_only=True,solver='feasibility_witness',audit=checked5)
    write(destination/'validation/model5_witness.json',raw)
    cfg3,fl3=load_config(case_paths['M3'],use_pool_config=False)
    final3=load_instance(cfg3,fl3)
    raw3=empty_result(final3,'feasibility_witness')
    raw3['e2_trips']=deepcopy(raw['e2_trips'])
    quantities3={'b1':{site:sum(row['quantity'] for row in returned if row['satellite']==site) for site in final3.satellites}}
    state3=dict(deliveries={'b1':[[s] for s in final3.satellites]},
        pickup_order={'b1':list(final3.satellites)},pickup_vehicle={'b1':{s:i for i,s in enumerate(final3.satellites)}})
    raw3['e1_trips'],issues3,_=_e1_returns(final3,state3,quantities3)
    checked3=audit_solution(final3,raw3)
    assert not issues3 and checked3['valid'] and checked3['periodic_replay']['valid'],checked3['errors']
    raw3['info'].update(status='FEASIBLE',objective=checked3['objective'],proven_optimal=False,
        proof_scope='Constructed physical witness, not an algorithm result')
    raw3.update(validation_only=True,audit=checked3)
    write(destination/'validation/model3_witness.json',raw3)
    cost3=evaluate_comparison_cost(final3,raw3,cfg3['economic_config'],audit=checked3)
    # The ordinary appointment construction has no return in its second
    # reference wave. M8 requires every counted batch to recover something.
    # Merge its first two supply waves for the validation witness, without
    # changing any customer appointment or adding a return task/trip.
    witness_batches=(Batch('b1',batches[0].start,batches[0].cutoff,batches[1].end),)+tuple(
        Batch(f'b{i}',b.start,b.cutoff,b.end) for i,b in enumerate(batches[2:],2))
    witness_orders=[replace(o,batch='b1' if cohorts[o.id]<2 else f'b{cohorts[o.id]}',
        allowed_batch_ids=tuple(b.id for b in witness_batches)) for o in final5.orders]
    witness_problem=MultiBatchInstance(final5.network,witness_orders,
        replace(final5.operating_policy,batches=witness_batches),model=5)
    witness_raw=empty_result(witness_problem,'feasibility_witness')
    witness_raw['e2_trips']=deepcopy(raw['e2_trips'])
    witness_quantities={b.id:{site:0 for site in witness_problem.satellites} for b in witness_batches}
    for row in returned:
        if row['quantity']:
            bid=next(b.id for b in witness_batches if row['time']<=b.cutoff)
            witness_quantities[bid][row['satellite']]+=row['quantity']
    witness_state=dict(deliveries={b.id:[[s] if witness_problem.demands[s,b.id] else []
        for s in witness_problem.satellites] for b in witness_batches},
        pickup_order={b.id:list(witness_problem.satellites) for b in witness_batches},
        pickup_vehicle={b.id:{s:i for i,s in enumerate(witness_problem.satellites)} for b in witness_batches})
    witness_raw['e1_trips'],witness_issues,_=_e1_returns(witness_problem,witness_state,witness_quantities)
    witness_audit=audit_solution(witness_problem,witness_raw)
    assert not witness_issues and witness_audit['valid'] and witness_audit['periodic_replay']['valid'],witness_audit['errors']
    assert all(sum(witness_quantities[b.id].values())>0 for b in witness_batches)
    records=[]
    paired=[]
    for label in ('M8','pool80','fixed_pool100'):
        cfg,fl=load_config(case_paths[label],use_pool_config=False)
        public=load_instance(cfg,fl)
        witness_boundaries=[b.start for b in witness_batches]+[policy.cycle_end]
        child=public.fixed_calendar(4,witness_boundaries)
        pool=100 if label=='fixed_pool100' else witness_audit['minimum_pool_for_schedule']
        candidate=lift_result(child,witness_raw,{o.id:o.batch for o in witness_problem.orders},pool,
            'feasibility_witness',{b.id:b.cutoff for b in witness_batches})
        if not candidate.get('audit',{}).get('valid'):
            raise ValueError((label,candidate.get('audit',{}).get('errors')))
        attach_calendar_decisions(public,candidate,4,witness_boundaries)
        checked=audit_solution(public,candidate)
        assert checked['valid'] and checked['periodic_replay']['valid'],checked['errors']
        candidate.update(validation_only=True,audit=checked)
        price=evaluate_comparison_cost(public,candidate,cfg['economic_config'],audit=checked)
        record=dict(configuration=label,K=4,N=pool,valid=True,periodic_replay=True,
            minimum_pool_for_this_schedule=checked['minimum_pool_for_schedule'],
            e1_trips=checked['e1_trip_count'],e2_trips=checked['e2_trip_count'],
            acquisition=price['components']['container_pool'],cost=price['total'],
            operating_cost=price['total']-price['components']['container_pool'],
            clean_stock=checked['clean_stock'])
        records.append(record)
        paired.append(candidate)
        write(destination/f'validation/{label}_witness.json',candidate)
    assert all(c['e1_trips']==paired[0]['e1_trips'] and c['e2_trips']==paired[0]['e2_trips'] for c in paired)
    mixed=sum(any(e['kind']=='d' for e in t['events']) and any(e['kind']=='p' for e in t['events'])
              for t in raw['e2_trips'])
    pure_e1=sum(not any(e['kind']=='d' for e in t['events']) for t in raw['e1_trips'])
    def histogram(source_rows,kind):
        prefix='delivery' if kind=='d' else 'pickup'
        counts=Counter(math.floor((r[f'{prefix}_ready']+r[f'{prefix}_due'])/120) for r in source_rows)
        return {str(h):counts[h] for h in range(8,21)}
    hourly=dict(old_delivery=histogram(original_rows,'d'),new_delivery=histogram(rows,'d'),
        old_pickup=histogram(original_rows,'p'),new_pickup=histogram(rows,'p'))
    validation=dict(valid=True,loaded_configurations=5,physical_witnesses_checked=5,
        independently_replayed_cycles=2,records=records,same_routes_and_times_across_pool_sizes=True,
        e2_mixed_trips=mixed,e1_pickup_only_trips=pure_e1,
        gurobi_checked=False,optimization_run=False,optimality_claim=False,
        actual_events_by_hour=dict(delivery=payload['delivery_histogram'],pickup=payload['pickup_histogram']),
        appointment_midpoints_by_hour=hourly)
    validation['single_batch_matched_witness']=dict(valid=True,periodic_replay=True,N=100,
        e1_trips=checked3['e1_trip_count'],e2_trips=checked3['e2_trip_count'],cost=cost3['total'],
        same_e2_routes_and_times_as_multibatch=True)
    write(destination/'validation/summary.json',validation)
    write(destination/'validation/source_fingerprint.json',dict(
        files={rel(path):digest(path) for path in destination.rglob('*.json')
               if path.name!='source_fingerprint.json'}))
    lines=['# 日内均匀预约与交错送取：100顾客示范算例','',
        '保留L05的100个顾客、坐标、固定配送站、完整货物重量、一客一箱、服务/最低使用时间和更新后的大规模车辆。'
        '单独保存，不覆盖原数据集。模型3/8只修改四个预约时间窗端点，其余订单字段保留。'
        '模型5另提供五批参考安排，仅作为验证日历。','',
        '100单货物仍全部在08:00就绪，没有加入分时到货。预约分为五个平衡时段，每段20位顾客；'
        '验证见证的参考补货时刻为08:00、10:00、12:00、14:00、16:00，模型8的求解不固定为这五批。'
        '顾客预约围绕物理可行的混合送取路线分别加随机宽度，配送服务分散在08—17时，回收与后续配送交错。'
        '近似均匀不等于完全均匀，更不是实测需求。','',
        '## 已验证的机制','',
        '| 场景 | 箱池 | 一级趟数 | 二级趟数 | 首次购箱费 | 每周期运营费 |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for record in (records[-1],records[0]):
        lines.append(f'| {"充足箱池" if record["N"]==100 else "同路线回用"} | {record["N"]} | '
            f'{record["e1_trips"]} | {record["e2_trips"]} | {record["acquisition"]:.2f} | {record["operating_cost"]:.2f} |')
    lines+=['',f'两份见证的路线、顾客任务顺序和服务时间完全相同。混合送取二级趟次为{mixed}趟；'
        f'专门空跑回收的一级趟次为{pure_e1}。模型8见证将前两轮供货合并为一批，不改任何顾客窗口，得到四批；'
        '在同一个四批参考方案内，回用箱没有增加运输趟次；'
        f'单批见证另有{checked3["e1_trip_count"]}趟一级运输，二级路线和时间完全相同。'
        '这不等于已经证明五批方案相对于单批不增加一级运输。'
        '这里证明的是一种可行机制，不是ALNS或精确求解的优化结果，也没有证明路线或箱池最优。','',
        '## 有限箱池的逐轮库存','',
        '| 装载时刻 | 本轮配送 | 装载前干净箱 | 装载后干净箱 |','| --- | ---: | ---: | ---: |']
    for stock in records[0]['clean_stock']:
        sec=stock['time']
        lines.append(f'| {sec//3600:02d}:{sec//60%60:02d} | {stock["demand"]} | {stock["available"]} | {stock["after"]} |')
    lines+=['','## 可用配置','',
        '- model_8/case.json：正常完整批次数域K=1～100、箱池1～100，允许优化批次与箱池。',
        '- model_8/case_pool80.json：相同业务，箱池上限80。',
        '- model_8/case_fixed_pool100.json：相同业务，固定100箱作为充足箱池对照。',
        '- model_5/case.json：固定参考补货日历和配送批次，用于核查基础路线。','',
        '- model_3/case.json：同样货物就绪和顾客窗口的单批对照，已验证可行。','',
        '只调整时间窗，并不预先保证多批次更便宜。应比较模型3与模型8的优化结果，区分二级正常送取趟次和一级回收周转增加的运输。','',
        'validation/保存的是独立验证见证，不会自动提供给ALNS作初解。所有配置加载及五份见证的连续两周期重放通过。'
        'Gurobi未检查，未运行任何优化求解。','',
        '重建命令（目标目录存在会拒绝覆盖）：',
        '.\\tools\\run.ps1 -WithoutGurobi python -Arguments @(\'tools/data/build_continuous_flow_100.py\',\'--publish\')','']
    (destination/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(dict(published=rel(destination),validation=validation),ensure_ascii=False,indent=2))
