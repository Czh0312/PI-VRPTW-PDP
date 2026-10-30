"""Redesign the 27 frozen customer networks without tuning model costs/results.

Keep customer/depot coordinates, IDs, goods and one-box demands. Select actual
Zhou satellite candidates with balanced daily customer counts. Cover two,
three and four external appointment periods, and three demand profiles.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path
import random

from tools.configuration.loader import PROJECT_ROOT, load_routing_case
from tools.data.build_formal_benchmark27 import parameter_contract, single_policy, write_json, digest, reference
from tools.data.one_box_contract import CONTRACT, preserved_orders_hash, preserved_policy_hash, validate_orders
from tools.data.formal_benchmark27_bounds import productive_count_certificate
from models.common.cycle import load_instance
from models.common.cycle.policy import MultiBatchPolicy

SOURCE = 'data/formal_benchmark27_one_box_v1'
DEFAULT_OUTPUT = 'data/formal_benchmark27_spacetime_v2'
VERSION = 'formal_benchmark27_spacetime_v2'
REVISION = 2
PROFILES = {
    2: ((.5,.5),(.35,.65),(.65,.35)),
    3: ((.34,.33,.33),(.2,.5,.3),(.5,.35,.15)),
    4: ((.25,.25,.25,.25),(.15,.35,.35,.15),(.4,.3,.2,.1)),
}
PROFILE_NAMES = ('balanced','peak','front_loaded')
E1_VOLUME = {7:7,10:10,12:12,20:20,30:30,50:30,100:60}
E1_COUNT = {7:2,10:2,12:2,20:3,30:3,50:4,100:5}
E2_TOTAL = {7:2,10:4,12:4,20:6,30:12,50:12,100:30}


def vehicle_capacities(customer_count):
    """User-approved revision 2 capacities, in benchmark volume/weight units."""
    return dict(E1_Ck=E1_VOLUME[customer_count],E1_Qk=20*E1_VOLUME[customer_count],
                E2_Cv=3 if customer_count<=12 else 6,
                E2_Qv=60 if customer_count<=12 else 120)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def euclidean(a,b):
    return math.hypot(float(a['x'])-float(b['x']),float(a['y'])-float(b['y']))


def hungarian(cost):
    """Deterministic O(n^3) square assignment, no optional numeric dependency."""
    n=len(cost);u=[0.]*(n+1);v=[0.]*(n+1);p=[0]*(n+1);way=[0]*(n+1)
    for i in range(1,n+1):
        p[0]=i;j0=0;minimum=[math.inf]*(n+1);used=[False]*(n+1)
        while True:
            used[j0]=True;i0=p[j0];delta=math.inf;j1=0
            for j in range(1,n+1):
                if used[j]:continue
                current=cost[i0-1][j-1]-u[i0]-v[j]
                if current<minimum[j]:minimum[j]=current;way[j]=j0
                if minimum[j]<delta:delta=minimum[j];j1=j
            for j in range(n+1):
                if used[j]:u[p[j]]+=delta;v[j]-=delta
                else:minimum[j]-=delta
            j0=j1
            if p[j0]==0:break
        while True:
            j1=way[j0];p[j0]=p[j1];j0=j1
            if j0==0:break
    columns=[0]*n
    for j in range(1,n+1):columns[p[j]-1]=j-1
    return columns


def balanced_facilities(customers,candidates,count):
    n=len(customers);floor=n//count;ceil=math.ceil(n/count);best=None
    combos=sorted(itertools.combinations(candidates,count),key=lambda sites:(
        sum(min(euclidean(c,s) for s in sites) for c in customers),tuple(s['node_id'] for s in sites)))
    evaluated=0
    for sites in combos:
        lower=sum(min(euclidean(c,s) for s in sites) for c in customers)
        if best is not None and lower>best[0]+1e-9:continue
        slots=[(s,j) for s in sites for j in range(ceil)]
        matrix=[[euclidean(c,s) for s,_ in slots] for c in customers]
        for _ in range(len(slots)-n):
            matrix.append([0. if j>=floor else 1e9 for s,j in slots])
        assignment=hungarian(matrix);evaluated+=1
        mapping={c['node_id']:slots[assignment[i]][0]['node_id'] for i,c in enumerate(customers)}
        distances=[euclidean(c,slots[assignment[i]][0]) for i,c in enumerate(customers)]
        key=(sum(distances),max(distances),tuple(s['node_id'] for s in sites))
        if best is None or key<best[:3]:best=(*key,sites,mapping)
    return list(best[3]),best[4],dict(distance_sum=best[0],maximum_distance=best[1],
        candidate_subsets=len(combos),balanced_assignments_evaluated=evaluated,
        objective='Minimum sum customer-satellite distance with each selected site serving floor/ceil(N/S) customers; independent of routing costs or relative model results')


def quotas(n,weights,minimum):
    result=[minimum]*len(weights)
    if sum(result)>n:raise ValueError('Impossible positive warehouse/period coverage')
    while sum(result)<n:
        j=min(range(len(weights)),key=lambda i:((result[i]+1-n*weights[i])**2-(result[i]-n*weights[i])**2,i))
        result[j]+=1
    return result


def period_cells(site_counts,weights):
    sites=sorted(site_counts);t=len(weights);global_counts=quotas(sum(site_counts.values()),weights,len(sites))
    # Each column is floor/ceil(global demand / sites); a small exact DP
    # allocates the extra boxes while preserving the balanced daily totals.
    lower=[q//len(sites) for q in global_counts]
    required=tuple(site_counts[s]-sum(lower) for s in sites)
    states={(0,)*len(sites):(0.,[])}
    for b,q in enumerate(global_counts):
        updated={}
        for extras,(cost,path) in states.items():
            for chosen in itertools.combinations(range(len(sites)),q%len(sites)):
                chosen=set(chosen);new=tuple(v+int(i in chosen) for i,v in enumerate(extras))
                if any(v>required[i] for i,v in enumerate(new)):continue
                column=tuple(lower[b]+int(i in chosen) for i in range(len(sites)))
                value=cost+sum((column[i]-site_counts[s]*weights[b])**2 for i,s in enumerate(sites))
                candidate=(value,path+[column])
                if new not in updated or candidate<updated[new]:updated[new]=candidate
        states=updated
    _,columns=states[required]
    result={s:[column[i] for column in columns] for i,s in enumerate(sites)}
    return result,global_counts


def original_candidates(base):
    group,stem=base['source_block'].split(':',1)[1].split('_',1)
    root=PROJECT_ROOT/'reference/zhou_2E_VRPTWSPD_input'
    source=root/group/(stem+'.txt') if group=='Set1' else root/group/stem/f'Set5_{stem}.dat'
    lines=[row.strip() for row in source.read_text().splitlines() if row.strip()]
    if group=='Set1':
        n,s=map(int,stem.split('-')[:2])
        customers=[tuple(map(float,row.split()[:2])) for row in lines[:n]]
        satellites=[tuple(map(float,row.split()[:2])) for row in lines[n:n+s]]
        depot=tuple(map(float,lines[n+s].split()[:2]))
    else:
        stores=next(lines[i+1] for i,row in enumerate(lines) if row.startswith('!Stores:'))
        demand=next(lines[i+1] for i,row in enumerate(lines) if row.startswith('!Customers:'))
        points=[tuple(map(float,token.split(',')[:2])) for token in stores.split()]
        depot,satellites=points[0],points[1:]
        customers=[tuple(map(float,token.split(',')[:2])) for token in demand.split()]
    nodes=[dict(node_id='D0',node_type='d',x=depot[0],y=depot[1])]
    nodes += [dict(node_id=f'S{i}',node_type='s',x=x,y=y) for i,(x,y) in enumerate(satellites)]
    nodes += [dict(node_id=f'C{i}',node_type='c',x=x,y=y) for i,(x,y) in enumerate(customers)]
    parsed=dict(nodes=nodes,source_file=reference(source))
    return parsed, [r for r in parsed['nodes'] if r['node_type']=='s']


def make_reference(base,index,size_index,target,*,source_root=None):
    old=(Path(source_root) if source_root is not None else PROJECT_ROOT/SOURCE)/'shared'/base['size']/base['id']
    with (old/'network.csv').open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f);fields=reader.fieldnames;nodes=list(reader)
    customers=sorted((r for r in nodes if r['node_type']=='c'),key=lambda r:r['node_id'])
    depot=next(r for r in nodes if r['node_type']=='d')
    parsed,candidates=original_candidates(base)
    original={r['node_id']:r for r in parsed['nodes']}
    for node in customers+[depot]:
        assert float(node['x'])==original[node['node_id']]['x'] and float(node['y'])==original[node['node_id']]['y']
    n=base['customer_count'];a,b=divmod(index,3);periods=2+(b+size_index)%3
    network_level=(a+b+size_index)%3;profile_index=(a+2*b+size_index)%3
    requested=((1,2,2) if n<=12 else (1,2,3) if n<=30 else (2,3,4 if n==50 else 5))[network_level]
    station_count=min(requested,len(candidates),n//periods)
    selected,assignment,spatial=balanced_facilities(customers,candidates,station_count)
    sites=sorted(assignment.values());counts=Counter(sites)
    weights=PROFILES[periods][profile_index];cells,global_counts=period_cells(counts,weights)
    old_satellites={r['node_id']:r for r in nodes if r['node_type']=='s'}
    satellites=[]
    for site in selected:
        row=deepcopy(old_satellites.get(site['node_id'],nodes[1]))
        row.update(node_id=site['node_id'],node_type='s',x=site['x'],y=site['y'],
                   delivery_demand=0,service_time=3.,delivery_ready=480,delivery_due=1260,
                   pickup_ready=480,pickup_due=1260,assigned_sat='')
        satellites.append(row)
    for customer in customers:customer['assigned_sat']=assignment[customer['node_id']]
    with (target/'network.csv').open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows([depot]+satellites+customers)
    fleet=read(old/'fleet.json');fleet.update(E1_vehicle_count=E1_COUNT[n],**vehicle_capacities(n),
        vehicles_per_satellite=E2_TOTAL[n]//station_count,parameter_scope='benchmark27_spacetime_v2_physical_design')
    assert E2_TOTAL[n]%station_count==0
    write_json(target/'fleet.json',fleet)
    # Economic bytes are a compatibility placeholder only, explicitly outside this redesign.
    (target/'economic.json').write_bytes((old/'economic.json').read_bytes())
    policy=read(old/'reference_policy.json');length=(policy['cycle_end']-policy['cycle_start'])/periods
    depot_travel=max(math.ceil(euclidean(depot,s)/fleet['speed']*60)/60 for s in satellites)
    cutoff=math.floor(min(.72*length,length-depot_travel-22)*2)/2
    policy.update(container_pool_size=n,description='External appointment periods; fixed reference for M5 only. M6-M8 retain their optimized K domains.',
        batches=[dict(id=f'b{i+1}',delivery_duration=cutoff,return_duration=length-cutoff) for i in range(periods)])
    MultiBatchPolicy.from_config(policy);write_json(target/'reference_policy.json',policy)
    orders=read(old/'reference_orders.json');rng=random.Random(20261021+100*size_index+index)
    strata={}
    for site in sorted(counts):
        local=sorted((o for o in orders if assignment[o['customer_id']]==site),key=lambda o:o['order_id']);rng.shuffle(local)
        cursor=0
        for stage,q in enumerate(cells[site]):
            for order in local[cursor:cursor+q]:
                ready=policy['cycle_start']+stage*length+40+rng.randint(-5,5)
                order.update(satellite_id=site,delivery_ready=ready,
                    delivery_due=ready+min(75,math.floor(.30*length)),
                    pickup_ready=ready+order['service_time']+order['min_use_time'],
                    pickup_due=min(ready+145,policy['cycle_start']+(stage+1)*length-20),
                    delivery_batch_id=f'b{stage+1}',allowed_batch_ids=[f'b{i+1}' for i in range(periods)],
                    release_time=policy['cycle_start'])
                strata[order['order_id']]=stage+1
            cursor+=q
    orders.sort(key=lambda o:o['order_id']);validate_orders(orders,n)
    write_json(target/'reference_orders.json',orders)
    density=[value for row in cells.values() for value in row]
    old_periods=len(read(old/'reference_policy.json')['batches']);old_orders=read(old/'reference_orders.json')
    old_sites=sorted({o['satellite_id'] for o in old_orders})
    old_bins=Counter((o['satellite_id'],o['delivery_batch_id']) for o in old_orders)
    old_density=[old_bins[s,f'b{i+1}'] for s in old_sites for i in range(old_periods)]
    stats=dict(id=base['id'],size=base['size'],customers=n,Q=n,source_block=base['source_block'],
        satellites=station_count,requested_satellites=requested,external_periods=periods,
        profile=PROFILE_NAMES[profile_index],target_period_shares=list(weights),period_totals=global_counts,
        selected_satellites=[s['node_id'] for s in satellites],customers_per_site=dict(counts),
        warehouse_period_quantities=cells,mean_warehouse_period_demand=n/(station_count*periods),
        minimum_warehouse_period_demand=min(density),share_one_or_two=sum(1<=q<=2 for q in density)/len(density),
        share_empty=sum(q==0 for q in density)/len(density),daily_site_count_range=max(counts.values())-min(counts.values()),
        period_site_count_ranges=[max(row[j] for row in cells.values())-min(row[j] for row in cells.values()) for j in range(periods)],
        period_imbalance_cv=(sum((q-n/periods)**2 for q in global_counts)/periods)**.5/(n/periods),
        E1_vehicles=fleet['E1_vehicle_count'],E1_volume=fleet['E1_Ck'],E1_weight=fleet['E1_Qk'],
        E2_vehicles_total=E2_TOTAL[n],E2_per_site=fleet['vehicles_per_satellite'],
        E2_volume=fleet['E2_Cv'],E2_weight=fleet['E2_Qv'],reference_cutoff_minutes=cutoff,
        old_satellites=len(old_sites),old_period_totals=[sum(o['container_count'] for o in old_orders if o['delivery_batch_id']==f'b{i+1}') for i in range(old_periods)],
        old_minimum_warehouse_period_demand=min(old_density),old_share_one_or_two=sum(1<=q<=2 for q in old_density)/len(old_density),
        spatial_selection=spatial,appointment_period_by_order=strata,
        original_source_file=parsed['source_file'],original_source_sha256=digest(PROJECT_ROOT/parsed['source_file']),
        coordinate_preservation='Customer IDs, all customer/depot x/y and original goods weights unchanged; satellite x/y chosen from the same original Zhou block',
        warehouse_count_adjustment='At least one order per site-period requires S*T<=Q; no new orders or coordinates are invented')
    write_json(target/'design_metrics.json',stats)
    return orders,policy,stats


def write_design_docs(out,all_stats):
    """Keep the readable design table reproducible with the generated inputs."""
    labels={'balanced':'均衡型','peak':'高峰型','front_loaded':'前段集中型'}
    lines=['# 新版27例：空间与需求时段设计','',
        '本轮只更新物理算例。27个基础算例、216个八模型运行入口；成本文件逐字节沿用旧版作为兼容模板，等待另行设计，不依据经济结果选例。','',
        '## 设计规则','',
        '- 保留S01—S09、M01—M09、L01—L09的顾客集合、顾客与中央仓坐标、完整货物重量和一客一箱规则；周期仍为08:00—21:00，服务、使用和清洗时长保留。',
        '- 两、三、四个外部需求时段各9例；均衡、高峰、前段集中三种分布各9例。各规模内采用确定性Latin覆盖，来源和顾客数仍有差异，不称为严格控制的全因子实验。',
        '- 卫星站从同一原始Zhou网络的实际候选点选择，不移动或虚构坐标。日需求分仓在floor/ceil(Q/S)之间，以客户到站总距离最小选择设施与分区；每个时段各仓需求最多相差1箱。',
        '- 每仓每时段至少1笔订单；小算例的整数粒度会使实际比例偏离目标比例，保留并报告这种差异，不增加虚构订单。',
        '- 同一顾客数下一级车容量和数量、二级车总数固定，二级车按卫星站数均分。一级体积容量依次为7/10/12/20/30/30/60，对应7/10/12/20/30/50/100顾客。',
        '- 修订2按用户确认收紧载重：一级载重=体积容量×20；小规模二级容量3箱、载重60；中大规模二级容量6箱、载重120。车辆数保持，重量使用基准单位，不是实测公斤。',
        '- 两至四时段是外部服务需求属性及M5参考分批；M1—M4使用单批，M5固定参考分批并优化箱池，M6—M8仍自由优化订单分批和K，只剔除输入证明不可能的K。',
        '- 货物释放时间仍为周期起点，保持单批模型可比较。本版没有声称模拟分时段新到货。参考清洗回库时间余量按固定规则计算，没有根据模型相对收益调参。','',
        '## 27例一览','',
        '| 算例 | 顾客/箱次 | 卫星站 | 外部时段 | 分布 | 各时段总需求 | 一级容量 | 二级总车数 | 最小仓时段需求 |',
        '|---|---:|---:|---:|---|---|---:|---:|---:|']
    rows=[]
    for r in all_stats:
        lines.append(f"| {r['id']} | {r['Q']} | {r['satellites']} | {r['external_periods']} | {labels[r['profile']]} | {' / '.join(map(str,r['period_totals']))} | {r['E1_volume']} | {r['E2_vehicles_total']} | {r['minimum_warehouse_period_demand']} |")
        rows.append({k:r[k] for k in ('id','size','customers','satellites','external_periods','profile',
            'mean_warehouse_period_demand','minimum_warehouse_period_demand','share_one_or_two','share_empty',
            'E1_vehicles','E1_volume','E1_weight','E2_vehicles_total','E2_per_site','E2_volume','E2_weight')})
        rows[-1]['period_totals']=' / '.join(map(str,r['period_totals']))
    lines+=['','## 运行与核验','',
        '完整输入在本目录内；[case_index.csv](case_index.csv)列出216个入口，[design.json](design.json)包含分仓、时段数量、稀疏度和来源指标。可行性状态以validation/feasibility.json及独立审计为准。可行见证不自动作为ALNS初解，也不证明最优。','',
        '```sh',
        '.venv/bin/python -m tools.audit.audit_benchmark27_spacetime --dataset data/formal_benchmark27_spacetime_v2',
        '.venv/bin/python -m tools.audit.certify_formal_benchmark27 --dataset data/formal_benchmark27_spacetime_v2 --verify',
        '.venv/bin/python main.py route --case data/formal_benchmark27_spacetime_v2/model_8/large/L05/case.json --time 30',
        '```','',
        '重新生成须使用新的非空目录；生成程序拒绝覆盖。旧formal_benchmark27_one_box_v1及其历史结果保持不变。新物理设计需要新的实验输入快照，不能沿用旧结果。','']
    (out/'README.md').write_text('\n'.join(lines),encoding='utf-8')
    with (out/'design_summary.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def build(output=DEFAULT_OUTPUT):
    out=(PROJECT_ROOT/output).resolve();source=PROJECT_ROOT/SOURCE;old_manifest=read(source/'manifest.json')
    if out.exists() and any(out.iterdir()):raise ValueError(f'Refusing to replace {out}')
    out.mkdir(parents=True,exist_ok=True)
    old_hashes={p.relative_to(source).as_posix():digest(p) for p in source.rglob('*') if p.is_file()}
    manifest=dict(dataset_version=VERSION,dataset_revision=REVISION,base_case_count=27,model_config_count=216,
        models=list(range(1,9)),sizes=dict(small=9,medium=9,large=9),source_block_count=old_manifest['source_block_count'],
        generator=reference(__file__),generator_sha256=digest(__file__),source_dataset=SOURCE,
        source_dataset_manifest_sha256=digest(source/'manifest.json'),demand_contract=CONTRACT,
        scenario_label='一客一箱；多种卫星站密度与2/3/4外部需求时段',
        economics_status='Existing economic files retained byte-for-byte as runtime placeholders; costs are outside this physical redesign',
        objective_by_model={str(i):'distance' if i<=4 else 'cost' for i in range(1,9)},
        runtime_path_basis='Project-root-relative; all runtime inputs inside this dataset',
        cases=[],base_cases=[],limitations=[
            'Coverage design, not a fully controlled factorial experiment: source networks and customer counts remain heterogeneous.',
            'Two/three/four appointment periods are exogenous demand attributes; M6-M8 do not have K restricted to that range.',
            'All goods remain available at cycle start so single-batch M1-M4 remain admissible; this is not a late-arrival benchmark.',
            'Small cases necessarily contain one/two-box site-period cells under the one-order-per-customer contract.',
            'Fleet totals and E1 capacity are fixed by customer count, not decreased with satellite count; geographical coverage differences remain.',
            'Existing price files are placeholders only; no economic superiority or optimality is asserted.'])
    all_stats=[]
    for size_index,size in enumerate(('small','medium','large')):
        for index,old in enumerate(b for b in old_manifest['base_cases'] if b['size']==size):
            shared=out/'shared'/size/old['id'];shared.mkdir(parents=True)
            orders,policy,stats=make_reference(old,index,size_index,shared);all_stats.append(stats)
            provenance=dict(base_case_id=old['id'],family_id=old['family_id'],source_block=old['source_block'],
                source_dataset=SOURCE,source_files={key:reference(source/'shared'/size/old['id']/name)
                    for key,name in [('data','network.csv'),('orders','reference_orders.json'),('routing_config','fleet.json'),
                                     ('operating_policy','reference_policy.json'),('economic_config','economic.json')]},
                demand_contract=CONTRACT,preserved_orders_sha256=preserved_orders_hash(orders),
                preserved_policy_sha256=preserved_policy_hash(policy),
                preserved_hash_scope='Frozen newly designed reference fields; original coordinate/weight preservation is independently checked against source_dataset.',
                design=stats)
            write_json(shared/'provenance.json',provenance)
            base={k:old[k] for k in ('id','size','customer_count','family_id','source_block','total_container_uses')}
            base.update(satellite_count=stats['satellites'],external_period_count=stats['external_periods'],demand_profile=stats['profile'],models={})
            common=dict(data=reference(shared/'network.csv'),routing_config=reference(shared/'fleet.json'),
                economic_config=reference(shared/'economic.json'),dataset_metadata=reference(shared/'provenance.json'))
            for model in range(1,9):
                folder=out/f'model_{model}'/size/old['id'];morders=deepcopy(orders)
                mpolicy=single_policy(policy) if model<=4 else deepcopy(policy)
                if model<=4:
                    for order in morders:order.update(delivery_batch_id='b1',allowed_batch_ids=['b1'])
                elif model==5:
                    for order in morders:order['allowed_batch_ids']=[order['delivery_batch_id']]
                if model==6:
                    cfg,fleet=load_routing_case(base['models']['5'],use_pool_config=False)
                    cert=productive_count_certificate(load_instance(cfg,fleet));write_json(shared/'batch_count_certificate.json',cert)
                options=(dict(schedule_mode={6:'uniform_cycle',7:'uniform_independent_cutoffs',8:'flexible_cycle'}[model],
                    batch_count_candidates=cert['candidates_by_model'][str(model)],min_phase_minutes=1,pool_min=1,pool_max=old['total_container_uses']) if model>=6 else {})
                write_json(folder/'orders.json',morders);write_json(folder/'policy.json',mpolicy)
                contract=parameter_contract(model,old,morders,mpolicy,options)
                contract.update(demand_contract=CONTRACT,external_appointment_period_count=stats['external_periods'],
                    economics_status=manifest['economics_status'])
                contract['notes']=[s.replace('M1-M5','M1-M4') for s in contract['notes']]
                write_json(folder/'parameters.json',contract)
                cfg=dict(schema_version=2,case_name=f'{VERSION}_{old["id"]}_M{model}',model=model,solver='alns',**common,
                    orders=reference(folder/'orders.json'),operating_policy=reference(folder/'policy.json'),
                    exact_backend='joint_batch_indexed' if model>=6 else 'multibatch_indexed',
                    exact_time=300,alns_time=30,alns_iter=10**9,seed=42,save_plots=False,
                    dataset_version=VERSION,base_case_id=old['id'],parameter_contract=reference(folder/'parameters.json'))
                if model==5:cfg.update(objective_mode='cost',pool_options=dict(pool_min=1,pool_max=old['total_container_uses']))
                else:cfg['container_pool_size']=old['total_container_uses']
                if options:cfg.update(joint_batch_options=options,batch_count_certificate=reference(shared/'batch_count_certificate.json'))
                write_json(folder/'case.json',cfg)
                entry=dict(base_case_id=old['id'],model=model,size=size,customer_count=old['customer_count'],
                    family_id=old['family_id'],source_block=old['source_block'],total_container_uses=old['total_container_uses'],
                    case_path=reference(folder/'case.json'),parameters=reference(folder/'parameters.json'))
                manifest['cases'].append(entry);base['models'][str(model)]=entry['case_path']
            manifest['base_cases'].append(base)
            print(f"BUILT {old['id']} N={old['customer_count']} S={stats['satellites']} T={stats['external_periods']} {stats['profile']} {stats['period_totals']}",flush=True)
    protocol=dict(dataset_revision=REVISION,physical_design_only=True,economics_status=manifest['economics_status'],
        external_period_counts=[2,3,4],period_frequency=dict(Counter(str(r['external_periods']) for r in all_stats)),
        profiles=list(PROFILE_NAMES),profile_frequency=dict(Counter(r['profile'] for r in all_stats)),
        selection='Preserve all original 27 IDs/customer sets and 14 source blocks; deterministic Latin coverage of period count, density tier and profile within size; no model-result screening.',
        customer_count_unchanged=True,coordinates_unchanged=True,goods_weights_unchanged=True,
        demand_contract=CONTRACT,E1_volume_by_customer_count=E1_VOLUME,E1_count_by_customer_count=E1_COUNT,
        E2_total_by_customer_count=E2_TOTAL,fleet_control='Totals fixed within each customer-count stratum; uniform E2_per_site derived by division.',
        vehicle_capacity_policy=dict(revision=2,E1_weight='20 * E1 volume capacity',
            E2_small=dict(volume=3,weight=60),E2_medium_large=dict(volume=6,weight=120),
            vehicle_counts='unchanged',weight_unit='benchmark demand units'),
        fixed_reference='M5 alone fixes external reference periods and assignments; M1-M4 use single batch, M6-M8 optimize K and order assignment over input-certified domains.',
        fairness='Compare models within a base case; across-case network/time effects are descriptive unless separately controlled.',
        late_arrival_scope='Only service-demand timing varies; release_time remains cycle_start for single-batch compatibility.',
        feasibility='Input audit and explicit independent two-cycle schedule witnesses; no optimality or search-success claim.')
    write_json(out/'comparison_protocol.json',protocol)
    write_json(out/'design.json',dict(date='2026-10-21',source_dataset=SOURCE,protocol=protocol,cases=all_stats))
    write_design_docs(out,all_stats)
    with (out/'case_index.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(manifest['cases'][0]));writer.writeheader();writer.writerows(manifest['cases'])
    for model in range(1,9):
        (out/f'model_{model}'/'README.md').write_text(f'# 模型{model}\n\n27例运行入口见根目录case_index.csv。两至四个外部需求时段不限制优化模型的K域。\n',encoding='utf-8')
    manifest['artifact_sha256']={p.relative_to(out).as_posix():digest(p) for p in out.rglob('*') if p.is_file()}
    write_json(out/'manifest.json',manifest)
    assert all(digest(source/name)==value for name,value in old_hashes.items()),'Source dataset modified'
    write_json(out/'source_integrity.json',dict(passed=True,source_dataset=SOURCE,files_checked=len(old_hashes),sha256=old_hashes))
    manifest['artifact_sha256']['source_integrity.json']=digest(out/'source_integrity.json');write_json(out/'manifest.json',manifest)
    return out


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',default=DEFAULT_OUTPUT)
    print(build(parser.parse_args().output))
