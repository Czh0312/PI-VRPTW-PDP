"""Independently validate spatial/time coverage and original coordinate preservation."""
import argparse
from collections import Counter,defaultdict
import csv
import hashlib
import json
from pathlib import Path

from tools.configuration.loader import PROJECT_ROOT,resolve_project_path
from tools.audit.audit_formal_benchmark27 import audit_dataset,require


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def nodes(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:return {r['node_id']:r for r in csv.DictReader(f)}


def source_satellites(path,source_block):
    rows=[r.strip() for r in Path(path).read_text().splitlines() if r.strip()]
    if 'Set1_' in source_block:
        _,stem=source_block.split('_',1);n,s=map(int,stem.split('-')[:2])
        xy=[tuple(map(float,row.split()[:2])) for row in rows[n:n+s]]
    else:
        raw=next(rows[i+1] for i,row in enumerate(rows) if row.startswith('!Stores:'))
        xy=[tuple(map(float,p.split(',')[:2])) for p in raw.split()[1:]]
    return {f'S{i}':point for i,point in enumerate(xy)}


def audit_design(dataset):
    root=resolve_project_path(dataset);manifest=read(root/'manifest.json');design=read(root/'design.json')
    standard=audit_dataset(root)
    require(not standard['fixed_model_necessary_condition_failures'],'Fixed model failed necessary physical conditions')
    source=resolve_project_path(manifest['source_dataset']);original=read(source/'manifest.json')
    require({b['id'] for b in original['base_cases']}=={b['id'] for b in manifest['base_cases']},'Changed original 27 IDs')
    metrics={r['id']:r for r in design['cases']};fleet_by_n=defaultdict(set);report=[]
    require(Counter(r['external_periods'] for r in metrics.values())=={2:9,3:9,4:9},'Unbalanced period count coverage')
    require(Counter(r['profile'] for r in metrics.values())==dict(balanced=9,peak=9,front_loaded=9),'Unbalanced demand profile coverage')
    for base in manifest['base_cases']:
        name,size=base['id'],base['size'];a=source/'shared'/size/name;b=root/'shared'/size/name
        before,after=nodes(a/'network.csv'),nodes(b/'network.csv');stats=metrics[name]
        fixed={k:r for k,r in before.items() if r['node_type'] in ('c','d')}
        require(set(fixed)=={k for k,r in after.items() if r['node_type'] in ('c','d')},f'{name}: customer/depot IDs changed')
        for key,node in fixed.items():
            require((float(node['x']),float(node['y']))==(float(after[key]['x']),float(after[key]['y'])),f'{name}: coordinate moved: {key}')
        raw=resolve_project_path(stats['original_source_file'])
        require(hashlib.sha256(raw.read_bytes()).hexdigest()==stats['original_source_sha256'],f'{name}: raw source changed')
        possible=source_satellites(raw,base['source_block'])
        sites={k:r for k,r in after.items() if r['node_type']=='s'}
        require(len(sites)==stats['satellites'],f'{name}: wrong selected satellite count')
        for sid,node in sites.items():
            require((float(node['x']),float(node['y']))==possible[sid],f'{name}: invented satellite location')
        old_orders={o['order_id']:o for o in read(a/'reference_orders.json')}
        orders=read(b/'reference_orders.json');policy=read(b/'reference_policy.json');old_policy=read(a/'reference_policy.json')
        n=base['customer_count'];t=len(policy['batches']);counts=Counter(o['satellite_id'] for o in orders)
        cells=Counter((o['satellite_id'],o['delivery_batch_id']) for o in orders)
        require(t==stats['external_periods'] and n==stats['Q']==len(orders),f'{name}: changed period/Q semantics')
        for order in orders:
            old=old_orders[order['order_id']]
            for field in ('customer_id','goods_weight','container_count','service_time','min_use_time','release_time'):
                require(order[field]==old[field],f'{name}: preserved order field changed: {field}')
            require(order['satellite_id']==after[order['customer_id']]['assigned_sat'],f'{name}: network/order assignment mismatch')
        for field in old_policy:
            if field not in ('batches','description','container_pool_size'):
                require(policy[field]==old_policy[field],f'{name}: physical policy changed: {field}')
        require(max(counts.values())-min(counts.values())<=1,f'{name}: daily site demand unbalanced')
        for stage in range(t):
            values=[cells[s,f'b{stage+1}'] for s in sites]
            require(min(values)>=1 and max(values)-min(values)<=1,f'{name}: within-period warehouse demand unbalanced')
            require(sum(values)==stats['period_totals'][stage],f'{name}: wrong reported period totals')
            require(all(cells[s,f'b{stage+1}']==stats['warehouse_period_quantities'][s][stage] for s in sites),f'{name}: wrong reported warehouse-period quantities')
        fleet=read(b/'fleet.json')
        if manifest['dataset_revision']>=2:
            require(fleet['E1_Qk']==20*fleet['E1_Ck'],f'{name}: incorrect revision-2 E1 weight limit')
            expected=(3,60) if size=='small' else (6,120)
            require((fleet['E2_Cv'],fleet['E2_Qv'])==expected,f'{name}: incorrect revision-2 E2 capacities')
        for field,key in (('E1_Ck','E1_volume'),('E1_Qk','E1_weight'),('E2_Cv','E2_volume'),('E2_Qv','E2_weight')):
            require(fleet[field]==stats[key],f'{name}: stale vehicle capacity metadata: {key}')
        signature=tuple(fleet[k] for k in ('E1_vehicle_count','E1_Ck','E1_Qk','E2_Cv','E2_Qv','speed','container_weight','container_volume'))+(fleet['vehicles_per_satellite']*len(sites),)
        fleet_by_n[n].add(signature)
        require(fleet['E1_Ck']>=max(counts.values()),f'{name}: unsplit single-batch site volume exceeds E1 capacity')
        require(fleet['E1_Qk']>=max(sum(o['goods_weight']+2 for o in orders if o['satellite_id']==s) for s in sites),f'{name}: unsplit site weight exceeds E1 capacity')
        require((a/'economic.json').read_bytes()==(b/'economic.json').read_bytes(),f'{name}: costs changed despite physical-only scope')
        report.append(dict(id=name,customers=n,satellites=len(sites),external_periods=t,profile=stats['profile'],
            coordinate_preserved=True,goods_weight_preserved=True,daily_demand_balanced=True,
            within_period_demand_balanced=True,legacy_costs_unchanged=True,
            E1_volume=fleet['E1_Ck'],E2_total=fleet['vehicles_per_satellite']*len(sites)))
    require(all(len(v)==1 for v in fleet_by_n.values()),'Fleet totals/capacities differ within a customer-count stratum')
    return dict(valid=True,dataset_version=manifest['dataset_version'],base_case_count=27,model_config_count=216,
        period_frequency=dict(Counter(r['external_periods'] for r in report)),profile_frequency=dict(Counter(r['profile'] for r in report)),
        selected_satellite_range=[min(r['satellites'] for r in report),max(r['satellites'] for r in report)],
        costs_changed=False,source_coordinate_preservation=True,fleet_control_within_customer_count=True,
        input_validation=standard,cases=report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--dataset',default='data/formal_benchmark27_spacetime_v2');parser.add_argument('--output')
    args=parser.parse_args();result=audit_design(args.dataset)
    if args.output:
        target=resolve_project_path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('cases','input_validation')},ensure_ascii=False))
