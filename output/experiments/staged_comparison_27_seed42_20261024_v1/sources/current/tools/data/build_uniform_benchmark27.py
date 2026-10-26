"""Build the only active 27-case dataset: exactly 7/20/50 customers.

No optimization runs. A deterministic proportional selection of existing
site/period strata retains all physical order fields and source resources.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import csv
import json
from pathlib import Path

from models.common.cycle import load_instance
from tools.configuration.loader import PROJECT_ROOT, load_routing_case, resolve_project_path
from tools.data.build_formal_benchmark27 import digest, parameter_contract, reference, write_json
from tools.data.formal_benchmark27_bounds import productive_count_certificate
from tools.data.one_box_contract import CONTRACT, preserved_orders_hash, preserved_policy_hash, validate_orders

VERSION = 'formal_benchmark27_uniform_7_20_50_v1'
SOURCE = 'data/formal_benchmark27_spacetime_v4_1'
DEFAULT_OUTPUT = f'data/{VERSION}'
COUNTS = dict(small=7, medium=20, large=50)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def customer_key(value):
    return int(value.removeprefix('C'))


def select_customers(orders, count):
    """Allocate at least one per nonempty site/period, then largest remainders."""
    validate_orders(orders, len(orders))
    if not 0 < count <= len(orders):
        raise ValueError('Cannot select the requested number of existing customers')
    if count == len(orders):
        return sorted((o['customer_id'] for o in orders), key=customer_key)
    cells = defaultdict(list)
    for order in orders:
        cells[order['satellite_id'], order['delivery_batch_id']].append(order)
    if len(cells) > count:
        raise ValueError('Requested size cannot retain all nonempty source strata')
    capacity = {key: len(value)-1 for key,value in cells.items()}
    total, remaining = sum(capacity.values()), count-len(cells)
    allocated = {key: 1 + remaining*value//total for key,value in capacity.items()}
    missing = count-sum(allocated.values())
    priority = sorted(cells, key=lambda key: (-(remaining*capacity[key] % total), key))
    for key in priority[:missing]:
        allocated[key] += 1
    selected = [o['customer_id'] for key in sorted(cells)
                for o in sorted(cells[key], key=lambda o: customer_key(o['customer_id']))[:allocated[key]]]
    assert len(selected) == count
    return sorted(selected, key=customer_key)


def network_rows(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames, list(reader)


def build(output=DEFAULT_OUTPUT, source=SOURCE):
    origin, out = resolve_project_path(source).resolve(), resolve_project_path(output).resolve()
    if origin == out or origin in out.parents or out in origin.parents:
        raise ValueError('New dataset must be separate from its frozen source')
    if out.exists() and any(out.iterdir()):
        raise ValueError('Refusing to overwrite an existing dataset')
    out.mkdir(parents=True, exist_ok=True)
    old = read(origin/'manifest.json')
    if len(old['base_cases']) != 27:
        raise ValueError('Expected all 27 source bases')
    source_hashes = {p.relative_to(origin).as_posix():digest(p) for p in origin.rglob('*') if p.is_file()}
    manifest = dict(dataset_version=VERSION, dataset_revision=5, base_case_count=27, model_config_count=216,
        models=list(range(1,9)), sizes={size:9 for size in COUNTS},
        customer_count_quotas={size:{str(count):9} for size,count in COUNTS.items()},
        customer_count_by_size=COUNTS, source_dataset=SOURCE,
        source_dataset_manifest_sha256=digest(origin/'manifest.json'),
        generator=reference(__file__), generator_sha256=digest(__file__), demand_contract=CONTRACT,
        runtime_path_basis='Project-root-relative; every runtime input inside this dataset',
        objective_by_model={str(m):'cost' if m == 3 or m >= 5 else 'distance' for m in range(1,9)},
        cases=[], base_cases=[], route_feasibility='Not certified; input inspection only; no solver runs',
        limitations=['Source subsets can overlap; 27 cases are not necessarily independent samples.',
            'Each case retains its source satellites and vehicle resources. Resources are matched across models, but not controlled across all cases of the same customer count.',
            'Source profile names describe the original design; actual retained site/period counts are reported.',
            'M6-M8 keep their input-certified native K domains. The current M3/M8 ALNS plan separately restricts M8 to K=1..4 and requires minute calendar decisions.'])
    designs, selections = [], {}
    for original in old['base_cases']:
        size, name = original['size'], original['id']
        n = COUNTS[size]
        old_shared = origin/'shared'/size/name
        shared = out/'shared'/size/name
        shared.mkdir(parents=True)
        before_orders = read(old_shared/'reference_orders.json')
        selected = select_customers(before_orders,n)
        selected_set = set(selected)
        orders = [o for o in before_orders if o['customer_id'] in selected_set]
        validate_orders(orders,n)
        fields, rows = network_rows(old_shared/'network.csv')
        kept = [r for r in rows if r['node_type'] != 'c' or r['node_id'] in selected_set]
        with (shared/'network.csv').open('w',encoding='utf-8',newline='') as handle:
            writer = csv.DictWriter(handle,fieldnames=fields)
            writer.writeheader();writer.writerows(kept)
        for filename in ('fleet.json','economic.json'):
            (shared/filename).write_bytes((old_shared/filename).read_bytes())
        policy = {**read(old_shared/'reference_policy.json'),'container_pool_size':n}
        write_json(shared/'reference_orders.json',orders)
        write_json(shared/'reference_policy.json',policy)
        selection = dict(source_base_case_id=name, source_customer_count=len(before_orders), customer_count=n,
            mode='all' if len(before_orders)==n else 'proportional_site_period_v1',
            selected_customer_ids=selected, physical_design_policy='filter_orders_and_customers_only',
            rule='One per nonempty site/period; proportional remaining counts by largest remainder; numeric customer IDs within each stratum. No optimization outcomes used.')
        selections[name] = selection
        stats = deepcopy(read(old_shared/'design_metrics.json'))
        sites = sorted(r['node_id'] for r in kept if r['node_type']=='s')
        periods = [b['id'] for b in policy['batches']]
        counts = Counter(o['satellite_id'] for o in orders)
        cells = {site:[sum(o['satellite_id']==site and o['delivery_batch_id']==bid for o in orders) for bid in periods] for site in sites}
        totals = [sum(cells[site][i] for site in sites) for i in range(len(periods))]
        if not all(totals):
            raise ValueError(f'{name}: a whole reference period became empty')
        values = [value for row in cells.values() for value in row]
        stats.update(customers=n,Q=n,source_customers=len(before_orders),customer_selection=selection,
            customers_per_site={s:counts[s] for s in sites},warehouse_period_quantities=cells,period_totals=totals,
            mean_warehouse_period_demand=n/len(values),minimum_warehouse_period_demand=min(values),
            share_empty=sum(q==0 for q in values)/len(values),share_one_or_two=sum(1<=q<=2 for q in values)/len(values),
            daily_site_count_range=max(counts[s] for s in sites)-min(counts[s] for s in sites),
            period_site_count_ranges=[max(cells[s][i] for s in sites)-min(cells[s][i] for s in sites) for i in range(len(periods))],
            period_imbalance_cv=(sum((q-n/len(periods))**2 for q in totals)/len(periods))**.5/(n/len(periods)),
            appointment_period_by_order={o['order_id']:periods.index(o['delivery_batch_id'])+1 for o in orders})
        designs.append(stats)
        write_json(shared/'design_metrics.json',stats)
        base = {**deepcopy(original),'customer_count':n,'total_container_uses':n,'customer_selection':selection,
                'family_id':original['family_id']+(f'_uniform{n}' if n!=len(before_orders) else ''),'models':{}}
        write_json(shared/'provenance.json',dict(base_case_id=name,family_id=base['family_id'],source_block=base['source_block'],
            source_dataset=SOURCE,source_base_case_id=name,customer_selection=selection,demand_contract=CONTRACT,
            source_files={key:f'{SOURCE}/shared/{size}/{name}/{filename}' for key,filename in (
                ('data','network.csv'),('orders','reference_orders.json'),('routing_config','fleet.json'),
                ('operating_policy','reference_policy.json'),('economic_config','economic.json'))},
            preserved_orders_sha256=preserved_orders_hash(orders),preserved_policy_sha256=preserved_policy_hash(policy),design=stats))
        for model in range(1,9):
            folder = out/f'model_{model}'/size/name
            folder.mkdir(parents=True)
            source_folder = origin/f'model_{model}'/size/name
            morders = [o for o in read(source_folder/'orders.json') if o['customer_id'] in selected_set]
            mpolicy = {**read(source_folder/'policy.json'),'container_pool_size':n}
            write_json(folder/'orders.json',morders)
            write_json(folder/'policy.json',mpolicy)
            cfg = read(source_folder/'case.json')
            for key, filename in (('data','network.csv'),('routing_config','fleet.json'),
                                  ('economic_config','economic.json'),('dataset_metadata','provenance.json')):
                cfg[key] = reference(shared/filename)
            for key, filename in (('orders','orders.json'),('operating_policy','policy.json'),('parameter_contract','parameters.json')):
                cfg[key] = reference(folder/filename)
            cfg.update(dataset_version=VERSION,case_name=f'{VERSION}_{name}_M{model}',solver='alns',
                       alns_time={'small':300,'medium':600,'large':1200}[size],alns_iter=10**9,seed=42)
            cfg.pop('initial_joint_result',None)
            if model == 5:
                cfg.pop('container_pool_size',None)
                cfg.update(objective_mode='cost',pool_options=dict(pool_min=1,pool_max=n))
            else:
                cfg['container_pool_size'] = n
            if model == 3:
                cfg['objective_mode'] = 'cost'
            if model >= 6:
                cfg['joint_batch_options'].update(pool_min=1,pool_max=n)
                cfg['batch_count_certificate'] = reference(shared/'batch_count_certificate.json')
                cfg['joint_batch_options']['batch_count_candidates'] = certificate['candidates_by_model'][str(model)]
            options = cfg.get('joint_batch_options',{})
            contract = parameter_contract(model,base,morders,mpolicy,options)
            contract.update(demand_contract=CONTRACT,external_appointment_period_count=len(periods))
            contract['notes'] = [note for note in contract['notes'] if 'native distance objective' not in note]
            if model == 3:
                contract['objective'] = 'cost'
                contract['notes'].append('M3 directly optimizes the same economic profile as M5-M8, with its original fixed single-batch calendar and full-Q pool.')
            write_json(folder/'parameters.json',contract)
            write_json(folder/'case.json',cfg)
            base['models'][str(model)] = reference(folder/'case.json')
            if model == 5:
                problem = load_instance(*load_routing_case(folder/'case.json',use_pool_config=False))
                certificate = productive_count_certificate(problem)
                write_json(shared/'batch_count_certificate.json',certificate)
            manifest['cases'].append(dict(base_case_id=name,model=model,size=size,customer_count=n,
                family_id=base['family_id'],source_block=base['source_block'],total_container_uses=n,
                case_path=reference(folder/'case.json'),parameters=reference(folder/'parameters.json')))
        manifest['base_cases'].append(base)
        print(f'BUILT {name}: {len(before_orders)} -> {n} customers',flush=True)
    manifest['source_block_count'] = len({b['source_block'] for b in manifest['base_cases']})
    write_json(out/'selection.json',dict(source_dataset=SOURCE,customer_count_by_size=COUNTS,cases=selections,selected_without_solver_results=True))
    protocol = dict(dataset_version=VERSION,customer_count_by_size=COUNTS,cases_per_size=9,
        trial_cases_per_size=3,trial_ids=dict(small=['S01','S02','S03'],medium=['M01','M02','M03'],large=['L01','L02','L03']),
        costs='Source economic bytes retained; model 3 cost objective explicitly enabled',
        resources='Source fleet and sites retained per case; shared by every model of that case',
        scope_limits=manifest['limitations'])
    write_json(out/'comparison_protocol.json',protocol)
    write_json(out/'design.json',dict(source_dataset=SOURCE,protocol=protocol,cases=designs))
    with (out/'case_index.csv').open('w',encoding='utf-8-sig',newline='') as handle:
        writer = csv.DictWriter(handle,fieldnames=list(manifest['cases'][0]));writer.writeheader();writer.writerows(manifest['cases'])
    with (out/'design_summary.csv').open('w',encoding='utf-8-sig',newline='') as handle:
        names = ['id','size','customers','source_customers','satellites','external_periods','profile','E1_vehicles','E1_volume','E1_weight','E2_vehicles_total']
        writer = csv.DictWriter(handle,fieldnames=names,extrasaction='ignore');writer.writeheader();writer.writerows(designs)
    (out/'README.md').write_text('# 统一规模27例：小7、中20、大50\n\n'
        '每档9例，共27个基础算例，模型1～8共216个运行入口。实验先选每档前三例；完整数据保留全部27例。\n\n'
        '对30/100顾客来源按卫星站与需求时段作确定性比例裁剪，分别保留20/50位顾客；7/20/50顾客来源保留原集合。'
        '保留所选顾客的全部需求、坐标、服务时间和时间窗，保留各来源的卫星站和车辆；箱池与池量上界同步为实际顾客数。'
        '同一基础算例的所有模型共用物理资源；同规模不同基础算例的车辆数不保证相同，详见design_summary.csv。\n\n'
        '模型3显式使用总成本目标，与模型8共用经济参数。数据集保留原生可证K域；当前ALNS实验方案另行约束模型8 K=1～4、边界与截点按整分钟。\n\n'
        '本版仅完成输入生成与检查，未运行求解，不沿用旧数据集的可行性或最优性结论。来源版本已归档，不能将历史结果改名作为本版结果。\n',encoding='utf-8')
    if any(digest(origin/name)!=value for name,value in source_hashes.items()):
        raise ValueError('Source dataset changed while building')
    write_json(out/'source_integrity.json',dict(passed=True,source_dataset=SOURCE,files_checked=len(source_hashes),sha256=source_hashes))
    manifest['artifact_sha256'] = {p.relative_to(out).as_posix():digest(p) for p in out.rglob('*') if p.is_file()}
    write_json(out/'manifest.json',manifest)
    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',default=SOURCE)
    parser.add_argument('--output',default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(build(args.output,args.source))
