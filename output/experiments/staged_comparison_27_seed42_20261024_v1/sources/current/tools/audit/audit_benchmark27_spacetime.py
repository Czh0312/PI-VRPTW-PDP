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
    if manifest['dataset_version']=='formal_benchmark27_uniform_7_20_50_v1':
        from tools.audit.audit_uniform_benchmark27 import audit_uniform
        return audit_uniform(root)
    standard=audit_dataset(root)
    require(not standard['fixed_model_necessary_condition_failures'],'Fixed model failed necessary physical conditions')
    source=resolve_project_path(manifest['source_dataset']);original=read(source/'manifest.json')
    require({b['id'] for b in original['base_cases']}=={b['id'] for b in manifest['base_cases']},'Changed original 27 IDs')
    original_bases={b['id']:b for b in original['base_cases']}
    if manifest['dataset_revision']>=3:
        expected_small={3:10,4:7}[manifest['dataset_revision']]
        require(all(b['customer_count']==expected_small for b in manifest['base_cases'] if b['size']=='small'),
                f'Revision {manifest["dataset_revision"]} requires {expected_small} customers in every small case')
        require(hashlib.sha256((source/'manifest.json').read_bytes()).hexdigest()==manifest['source_dataset_manifest_sha256'],
                'Source manifest changed')
    small_networks=set()
    metrics={r['id']:r for r in design['cases']};fleet_by_n=defaultdict(set);report=[]
    require(Counter(r['external_periods'] for r in metrics.values())=={2:9,3:9,4:9},'Unbalanced period count coverage')
    require(Counter(r['profile'] for r in metrics.values())==dict(balanced=9,peak=9,front_loaded=9),'Unbalanced demand profile coverage')
    for base in manifest['base_cases']:
        name,size=base['id'],base['size']
        selection=base.get('customer_selection')
        filter_only=manifest['dataset_revision']==4 and size=='small'
        if filter_only:
            require(selection and selection.get('physical_design_policy')=='filter_only',f'{name}: missing filter-only contract')
        parent=selection['source_base_case_id'] if selection else name
        require(parent in original_bases and original_bases[parent]['size']==size,f'{name}: invalid source parent')
        require(base['source_block']==original_bases[parent]['source_block'],f'{name}: incorrect source block')
        a=source/'shared'/size/parent;b=root/'shared'/size/name
        before,after=nodes(a/'network.csv'),nodes(b/'network.csv');stats=metrics[name]
        fixed={k:r for k,r in before.items() if r['node_type'] in ('c','d')}
        if selection:
            customer_ids=sorted((k for k,r in fixed.items() if r['node_type']=='c'),key=lambda k:int(k.removeprefix('C')))
            mode=selection['mode']
            require(mode in ('all','first','last'),f'{name}: invalid subset rule')
            count=base['customer_count']
            expected=customer_ids if mode=='all' else customer_ids[:count] if mode=='first' else customer_ids[-count:]
            require(len(expected)==count and selection['customer_count']==count and selection['selected_customer_ids']==expected,f'{name}: non-deterministic customer subset')
            fixed={k:r for k,r in fixed.items() if r['node_type']=='d' or k in expected}
        require(set(fixed)=={k for k,r in after.items() if r['node_type'] in ('c','d')},f'{name}: customer/depot IDs changed')
        for key,node in fixed.items():
            require((float(node['x']),float(node['y']))==(float(after[key]['x']),float(after[key]['y'])),f'{name}: coordinate moved: {key}')
        raw=resolve_project_path(stats['original_source_file'])
        require(hashlib.sha256(raw.read_bytes()).hexdigest()==stats['original_source_sha256'],f'{name}: raw source changed')
        possible=source_satellites(raw,base['source_block'])
        sites={k:r for k,r in after.items() if r['node_type']=='s'}
        if filter_only:
            require(selection['mode']=='first' and parent==name,f'{name}: incorrect v4 parent or selection mode')
            require(after=={k:r for k,r in before.items() if r['node_type']!='c' or k in expected},f'{name}: filter-only network changed')
            require((a/'fleet.json').read_bytes()==(b/'fleet.json').read_bytes(),f'{name}: filter-only fleet changed')
        require(len(sites)==stats['satellites'],f'{name}: wrong selected satellite count')
        for sid,node in sites.items():
            require((float(node['x']),float(node['y']))==possible[sid],f'{name}: invented satellite location')
        old_orders={o['order_id']:o for o in read(a/'reference_orders.json')}
        orders=read(b/'reference_orders.json');policy=read(b/'reference_policy.json');old_policy=read(a/'reference_policy.json')
        if filter_only:
            require(orders==[o for o in old_orders.values() if o['customer_id'] in expected],f'{name}: retained order fields changed')
            expected_policy=dict(old_policy,container_pool_size=7)
            require(policy==expected_policy,f'{name}: filter-only policy changed beyond pool size')
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
        daily_balanced=max(counts[s] for s in sites)-min(counts[s] for s in sites)<=1
        if not filter_only:
            require(daily_balanced,f'{name}: daily site demand unbalanced')
        within_balanced=True
        for stage in range(t):
            values=[cells[s,f'b{stage+1}'] for s in sites]
            within_balanced=within_balanced and max(values)-min(values)<=1
            if not filter_only:
                require(min(values)>=1 and max(values)-min(values)<=1,f'{name}: within-period warehouse demand unbalanced')
            else:
                require(sum(values)>0,f'{name}: an external period is empty')
            require(sum(values)==stats['period_totals'][stage],f'{name}: wrong reported period totals')
            require(all(cells[s,f'b{stage+1}']==stats['warehouse_period_quantities'][s][stage] for s in sites),f'{name}: wrong reported warehouse-period quantities')
        fleet=read(b/'fleet.json')
        if manifest['dataset_version']=='formal_benchmark27_spacetime_v4_1':
            for mapping,actual in (('E1_volume_by_customer_count',fleet['E1_Ck']),
                    ('E1_count_by_customer_count',fleet['E1_vehicle_count']),
                    ('E2_total_by_customer_count',fleet['vehicles_per_satellite']*len(sites))):
                require(design['protocol'][mapping][str(n)]==actual,f'{name}: stale protocol fleet mapping: {mapping}')
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
        if manifest['dataset_revision']>=3:
            if size=='small':
                signature=tuple(sorted((k,float(r['x']),float(r['y'])) for k,r in after.items() if r['node_type'] in ('c','d')))
                require(signature not in small_networks,f'{name}: duplicated small customer network')
                small_networks.add(signature)
            if size!='small' or (selection and selection['mode']=='all'):
                for filename in ('network.csv','fleet.json','reference_orders.json','reference_policy.json','economic.json','batch_count_certificate.json'):
                    require((a/filename).read_bytes()==(b/filename).read_bytes(),f'{name}: retained physical input changed: {filename}')
        report.append(dict(id=name,customers=n,satellites=len(sites),external_periods=t,profile=stats['profile'],
            coordinate_preserved=True,goods_weight_preserved=True,daily_demand_balanced=daily_balanced,
            within_period_demand_balanced=within_balanced,legacy_costs_unchanged=True,
            E1_volume=fleet['E1_Ck'],E2_total=fleet['vehicles_per_satellite']*len(sites)))
    require(all(len(v)==1 for v in fleet_by_n.values()),'Fleet totals/capacities differ within a customer-count stratum')
    return dict(valid=True,dataset_version=manifest['dataset_version'],base_case_count=27,model_config_count=216,
        period_frequency=dict(Counter(r['external_periods'] for r in report)),profile_frequency=dict(Counter(r['profile'] for r in report)),
        selected_satellite_range=[min(r['satellites'] for r in report),max(r['satellites'] for r in report)],
        costs_changed=False,source_coordinate_preservation=True,fleet_control_within_customer_count=True,
        input_validation=standard,cases=report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--dataset',default='data/formal_benchmark27_uniform_7_20_50_v1');parser.add_argument('--output')
    args=parser.parse_args();result=audit_design(args.dataset)
    if args.output:
        target=resolve_project_path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('cases','input_validation')},ensure_ascii=False))
