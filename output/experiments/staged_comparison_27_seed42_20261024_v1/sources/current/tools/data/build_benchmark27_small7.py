"""Freeze seven-customer subsets without changing retained physical inputs.

Use the first seven numeric customer IDs of each v3 small case. Preserve their
orders, appointments, assignments, coordinates, warehouses, fleet and prices.
Medium/large cases change only version names and runtime path prefixes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import csv
import json
from pathlib import Path

from models.common.cycle import load_instance
from tools.configuration.loader import PROJECT_ROOT, load_routing_case
from tools.data.build_benchmark27_small10 import customer_key, read, rebase
from tools.data.build_benchmark27_spacetime import write_design_docs
from tools.data.build_formal_benchmark27 import digest, parameter_contract, reference, write_json
from tools.data.formal_benchmark27_bounds import productive_count_certificate
from tools.data.one_box_contract import CONTRACT, preserved_orders_hash, preserved_policy_hash

SOURCE = 'data/formal_benchmark27_spacetime_v3'
VERSION = 'formal_benchmark27_spacetime_v4_1'
DEFAULT_OUTPUT = f'data/{VERSION}'


def select_customers(orders):
    customers = sorted((o['customer_id'] for o in orders), key=customer_key)
    if len(customers) != 10 or len(set(customers)) != 10:
        raise ValueError('Require exactly ten distinct source customers')
    return customers[:7]


def copy_case(source, out, base):
    folders = [Path('shared')/base['size']/base['id']]
    folders += [Path(f'model_{m}')/base['size']/base['id'] for m in range(1, 9)]
    for folder in folders:
        for origin in (source/folder).rglob('*'):
            if not origin.is_file():
                continue
            target = out/origin.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rebase(origin.read_text(encoding='utf-8'), source, out), encoding='utf-8', newline='')
            # CSV and JSON data with no paths must keep their original bytes.
            if reference(source)+'/' not in origin.read_text(encoding='utf-8'):
                target.write_bytes(origin.read_bytes())
            if origin.name == 'case.json':
                cfg = read(target)
                cfg.update(dataset_version=VERSION, case_name=cfg['case_name'].replace(Path(SOURCE).name, VERSION))
                write_json(target, cfg)
    return json.loads(rebase(json.dumps(base, ensure_ascii=False), source, out))


def build_small(source, out, original, previous_stats):
    base = copy_case(source, out, original)
    name = base['id']
    origin = source/'shared'/'small'/name
    shared = out/'shared'/'small'/name
    selected = select_customers(read(origin/'reference_orders.json'))
    selection = dict(source_base_case_id=name, mode='first', customer_count=7,
        selected_customer_ids=selected, physical_design_policy='filter_only',
        rule='First seven numeric customer IDs of the corresponding frozen v3 ten-customer case; no solver-result screening.')
    base.update(customer_count=7, total_container_uses=7,
        family_id=original['family_id']+'_subset7_first', customer_selection=selection)
    with (origin/'network.csv').open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        fields, nodes = reader.fieldnames, list(reader)
    with (shared/'network.csv').open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(r for r in nodes if r['node_type'] != 'c' or r['node_id'] in selected)
    orders = [o for o in read(origin/'reference_orders.json') if o['customer_id'] in selected]
    policy = read(origin/'reference_policy.json')
    policy['container_pool_size'] = 7
    write_json(shared/'reference_orders.json', orders)
    write_json(shared/'reference_policy.json', policy)
    sites = sorted(r['node_id'] for r in nodes if r['node_type']=='s')
    periods = [b['id'] for b in policy['batches']]
    counts = Counter(o['satellite_id'] for o in orders)
    cells = {s:[sum(o['satellite_id']==s and o['delivery_batch_id']==b for o in orders) for b in periods] for s in sites}
    totals = [sum(cells[s][j] for s in sites) for j in range(len(periods))]
    if not all(totals):
        raise ValueError(f'{name}: filtering emptied an entire external demand period')
    density = [q for row in cells.values() for q in row]
    stats = deepcopy(previous_stats)
    stats.update(customers=7, Q=7, customer_selection=selection, customers_per_site=dict(counts),
        warehouse_period_quantities=cells, period_totals=totals,
        mean_warehouse_period_demand=7/len(density), minimum_warehouse_period_demand=min(density),
        share_one_or_two=sum(1<=q<=2 for q in density)/len(density),
        share_empty=sum(q==0 for q in density)/len(density),
        daily_site_count_range=max(counts[s] for s in sites)-min(counts[s] for s in sites),
        period_site_count_ranges=[max(cells[s][j] for s in sites)-min(cells[s][j] for s in sites) for j in range(len(periods))],
        period_imbalance_cv=(sum((q-7/len(periods))**2 for q in totals)/len(periods))**.5/(7/len(periods)),
        appointment_period_by_order={o['order_id']:periods.index(o['delivery_batch_id'])+1 for o in orders},
        coordinate_preservation='Filter only: selected customer/depot/warehouse rows and all retained order fields unchanged from v3.',
        warehouse_count_adjustment='Warehouse count unchanged from v3; an individual site-period cell may be empty after filtering.')
    write_json(shared/'design_metrics.json', stats)
    provenance = dict(base_case_id=name, family_id=base['family_id'], source_block=base['source_block'],
        source_dataset=SOURCE, customer_selection=selection, demand_contract=CONTRACT,
        source_files={key:reference(origin/file) for key,file in (
            ('data','network.csv'), ('orders','reference_orders.json'), ('routing_config','fleet.json'),
            ('operating_policy','reference_policy.json'), ('economic_config','economic.json'))},
        preserved_orders_sha256=preserved_orders_hash(orders), preserved_policy_sha256=preserved_policy_hash(policy),
        preserved_hash_scope='All retained orders unchanged from the named v3 subset; pool bound reduced to seven.', design=stats)
    write_json(shared/'provenance.json', provenance)
    entries = []
    for model in range(1, 9):
        folder = out/f'model_{model}'/'small'/name
        morders = [o for o in read(source/f'model_{model}'/'small'/name/'orders.json') if o['customer_id'] in selected]
        mpolicy = read(folder/'policy.json')
        mpolicy['container_pool_size'] = 7
        write_json(folder/'orders.json', morders)
        write_json(folder/'policy.json', mpolicy)
        if model == 6:
            cfg5, fleet = load_routing_case(base['models']['5'], use_pool_config=False)
            cert = productive_count_certificate(load_instance(cfg5, fleet))
            write_json(shared/'batch_count_certificate.json', cert)
        cfg = read(folder/'case.json')
        if model == 5:
            cfg['pool_options'].update(pool_min=1, pool_max=7)
        else:
            cfg['container_pool_size'] = 7
        options = {}
        if model >= 6:
            options = cfg['joint_batch_options']
            options.update(pool_min=1, pool_max=7, batch_count_candidates=cert['candidates_by_model'][str(model)])
        contract = parameter_contract(model, base, morders, mpolicy, options)
        contract.update(demand_contract=CONTRACT, external_appointment_period_count=stats['external_periods'],
            economics_status='v3 cost bytes unchanged; seven-customer filtering only.')
        contract['notes'] = [s.replace('M1-M5','M1-M4') for s in contract['notes']]
        write_json(folder/'parameters.json', contract)
        write_json(folder/'case.json', cfg)
        entries.append(dict(base_case_id=name, model=model, size='small', customer_count=7,
            family_id=base['family_id'], source_block=base['source_block'], total_container_uses=7,
            case_path=reference(folder/'case.json'), parameters=reference(folder/'parameters.json')))
    return base, entries, stats


def build(output=DEFAULT_OUTPUT):
    source, out = (PROJECT_ROOT/SOURCE).resolve(), (PROJECT_ROOT/output).resolve()
    if source == out or source in out.parents or out in source.parents:
        raise ValueError('New dataset must be separate from the source dataset')
    if out.exists() and any(out.iterdir()):
        raise ValueError(f'Refusing to overwrite nonempty dataset: {out}')
    source_hashes = {p.relative_to(source).as_posix():digest(p) for p in source.rglob('*') if p.is_file()}
    old = read(source/'manifest.json')
    old_stats = {r['id']:r for r in read(source/'design.json')['cases']}
    manifest = {k:deepcopy(v) for k,v in old.items() if k not in ('cases','base_cases','artifact_sha256','feasibility_certification')}
    manifest.update(dataset_version=VERSION, dataset_revision=4, source_dataset=SOURCE,
        source_dataset_manifest_sha256=digest(source/'manifest.json'), generator=reference(__file__), generator_sha256=digest(__file__),
        customer_count_quotas=dict(small={'7':9}, medium={'20':4,'30':5}, large={'50':4,'100':5}), cases=[], base_cases=[])
    stats, selections = [], {}
    for original in old['base_cases']:
        name = original['id']
        if original['size']=='small':
            base, entries, metrics = build_small(source, out, original, old_stats[name])
            selections[name] = base['customer_selection']
        else:
            base = copy_case(source, out, original)
            entries = [json.loads(rebase(json.dumps(r), source, out)) for r in old['cases'] if r['base_case_id']==name]
            metrics = deepcopy(old_stats[name])
        manifest['base_cases'].append(base)
        manifest['cases'].extend(entries)
        stats.append(metrics)
        print(f'BUILT {name} customers={base["customer_count"]}', flush=True)
    manifest['limitations'] = [s for s in manifest['limitations'] if not s.startswith('Small cases use six raw')]
    manifest['limitations'] += ['Nine small cases are filtered from six original source blocks; paired subsets overlap and are not independent samples.',
        'Small site-period cells may be empty or unbalanced after filtering. Appointments, warehouses, fleet and retained orders are not redesigned.']
    protocol = read(source/'comparison_protocol.json')
    protocol.update(dataset_revision=4, customer_count_unchanged=False, customer_count_quotas=manifest['customer_count_quotas'],
        E1_volume_by_customer_count={str(r['customers']):r['E1_volume'] for r in stats},
        E1_count_by_customer_count={str(r['customers']):r['E1_vehicles'] for r in stats},
        E2_total_by_customer_count={str(r['customers']):r['E2_vehicles_total'] for r in stats},
        metadata_revision='4.1: fleet mapping tables describe the actual retained v3 fleet, including seven-customer subsets.',
        selection='First seven numeric customer IDs from each v3 small case; filter only, without routing-result screening. Medium/large unchanged.',
        warehouse_period_rule='Small subsets may have empty/unbalanced site-period cells; all external periods remain nonempty.',
        vehicle_capacity_policy=dict(revision=2, E1_weight='20 * E1 volume capacity', E2_small=dict(volume=3,weight=60),
            E2_medium_large=dict(volume=6,weight=120), vehicle_counts='v3 unchanged: small two E1, four E2; E1 volume 10 and weight 200 retained.', weight_unit='benchmark demand units'))
    write_json(out/'comparison_protocol.json', protocol)
    write_json(out/'selection.json', dict(source_dataset=SOURCE, cases=selections, unchanged_sizes=['medium','large'],
        customer_count_quotas=manifest['customer_count_quotas'], selected_without_solver_results=True))
    write_json(out/'design.json', dict(date='2026-10-23',source_dataset=SOURCE,protocol=protocol,cases=stats))
    write_design_docs(out, stats)
    (out/'README.md').write_text('# 27例修订4：小规模统一7位顾客\n\n'
        'S01—S09分别从冻结v3同编号10顾客算例中按顾客编号数值排序取前7位，不依据求解结果选例。'
        '保留所选顾客的全部订单字段、时间窗、货重、分仓和坐标；保留中央仓、全部卫星站及车辆。'
        '仅过滤顾客及订单，将箱池与优化上界改为7，并重新计算输入可证的K候选域。\n\n'
        '中规模20/30顾客、大规模50/100顾客各9例保持；所有模型共用同一27例，共216个入口。'
        '成本文件逐字节保留。小规模仍为一级2辆、容量10/载重200，二级共4辆、每辆3箱/载重60。\n\n'
        '两、三、四个外部需求时段各9例，三种来源需求分布各9例；过滤后的实际需求量见'
        '[设计总表](design_summary.csv)和[逐仓逐时段明细](design.json)。不再要求小规模每仓每时段都有订单，'
        '也不重新分配订单来恢复目标比例；每个外部时段总需求均非空。\n\n'
        '9个小规模仍来自6个原始来源，同源子集可能重叠，不能视为9个独立样本。'
        '[选择清单](selection.json)记录顾客编号，[216项入口](case_index.csv)列出各模型配置。'
        'v3及历史结果保留，不得改名冒充本版结果。\n\n'
        '输入可行性以[独立两周期见证](validation/README.md)为准；见证不证明最优，也不自动作为求解初解。\n', encoding='utf-8')
    with (out/'case_index.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(manifest['cases'][0]));writer.writeheader();writer.writerows(manifest['cases'])
    for model in range(1,9):
        (out/f'model_{model}'/'README.md').write_text(f'# 模型{model}\n\n小规模9例均为7位顾客。27例入口见根目录case_index.csv。\n',encoding='utf-8')
    if not all(digest(source/name)==expected for name,expected in source_hashes.items()):
        raise ValueError('Source dataset changed during generation')
    write_json(out/'source_integrity.json',dict(passed=True,source_dataset=SOURCE,files_checked=len(source_hashes),sha256=source_hashes))
    manifest['artifact_sha256'] = {p.relative_to(out).as_posix():digest(p) for p in out.rglob('*') if p.is_file()}
    write_json(out/'manifest.json',manifest)
    return out


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default=DEFAULT_OUTPUT)
    print(build(parser.parse_args().output))
