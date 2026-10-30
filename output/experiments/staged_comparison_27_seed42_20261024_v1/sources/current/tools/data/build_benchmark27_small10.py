"""Make all nine small cases ten-customer cases, preserving frozen predecessors.

Keep the three native ten-customer cases. For each of the three existing
twelve-customer sources use its first and last ten numeric customer IDs.
Selection uses no routing result; the paired subsets share eight customers.
Medium and large inputs are copied with runtime paths rebased only.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import csv
import json
from pathlib import Path

from tools.configuration.loader import PROJECT_ROOT, load_routing_case
from tools.data.build_benchmark27_spacetime import make_reference, write_design_docs
from tools.data.build_formal_benchmark27 import (
    digest, parameter_contract, reference, single_policy, write_json,
)
from tools.data.formal_benchmark27_bounds import productive_count_certificate
from tools.data.one_box_contract import CONTRACT, preserved_orders_hash, preserved_policy_hash
from models.common.cycle import load_instance

SOURCE = 'data/formal_benchmark27_spacetime_v2'
VERSION = 'formal_benchmark27_spacetime_v3'
DEFAULT_OUTPUT = f'data/{VERSION}'
SELECTION = {
    'S01': ('S07', 'last'), 'S02': ('S08', 'last'), 'S03': ('S09', 'last'),
    'S04': ('S04', 'all'), 'S05': ('S05', 'all'), 'S06': ('S06', 'all'),
    'S07': ('S07', 'first'), 'S08': ('S08', 'first'), 'S09': ('S09', 'first'),
}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def customer_key(customer):
    return int(customer.removeprefix('C'))


def select_customers(orders, mode):
    customers = sorted((o['customer_id'] for o in orders), key=customer_key)
    if len(set(customers)) != len(customers) or len(customers) < 10:
        raise ValueError('Selection requires at least ten distinct source customers')
    if mode == 'all':
        if len(customers) != 10:
            raise ValueError('Identity selection requires exactly ten customers')
        return customers
    if mode not in ('first', 'last'):
        raise ValueError(f'Unknown customer selection: {mode}')
    return customers[:10] if mode == 'first' else customers[-10:]


def rebase(value, source, out):
    return value.replace(reference(source) + '/', reference(out).replace('\\', '/') + '/')


def copy_case(source, out, base):
    """Keep all original bytes except project-relative runtime path prefixes."""
    folders = [Path('shared')/base['size']/base['id']]
    folders += [Path(f'model_{m}')/base['size']/base['id'] for m in range(1, 9)]
    for relative in folders:
        for origin in (source/relative).rglob('*'):
            if not origin.is_file():
                continue
            target = out/origin.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = origin.read_bytes()
            target.write_bytes(rebase(payload.decode('utf-8'), source, out).encode('utf-8'))
            if origin.name == 'case.json':
                cfg = read(target)
                cfg.update(dataset_version=VERSION,
                           case_name=cfg['case_name'].replace('formal_benchmark27_spacetime_v2', VERSION))
                write_json(target, cfg)
    return json.loads(rebase(json.dumps(base, ensure_ascii=False), source, out))


def prepare_source(source, out, original, case_id, mode):
    old = source/'shared'/'small'/original['id']
    orders = read(old/'reference_orders.json')
    chosen = select_customers(orders, mode)
    target = out/'source_inputs'/'shared'/'small'/case_id
    target.mkdir(parents=True)
    with (old/'network.csv').open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        fields, rows = reader.fieldnames, list(reader)
    with (target/'network.csv').open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(r for r in rows if r['node_type'] != 'c' or r['node_id'] in chosen)
    write_json(target/'reference_orders.json', [o for o in orders if o['customer_id'] in chosen])
    policy = read(old/'reference_policy.json')
    policy['container_pool_size'] = 10
    write_json(target/'reference_policy.json', policy)
    for name in ('fleet.json', 'economic.json'):
        (target/name).write_bytes((old/name).read_bytes())
    return dict(source_base_case_id=original['id'], mode=mode, customer_count=10,
                selected_customer_ids=chosen,
                rule='Numeric customer IDs; first/last ten of the twelve-customer source. Native ten-customer cases are retained.')


def build_small(source, out, original, case_id, index, mode):
    selection = prepare_source(source, out, original, case_id, mode)
    base = deepcopy(original)
    base.update(id=case_id, customer_count=10, total_container_uses=10,
                family_id=original['family_id']+f'_subset10_{mode}', models={},
                customer_selection=selection)
    shared = out/'shared'/'small'/case_id
    shared.mkdir(parents=True)
    orders, policy, stats = make_reference(base, index, 0, shared, source_root=out/'source_inputs')
    stats.update(customer_selection=selection,
        coordinate_preservation='Selected customer/depot coordinates and goods weights preserved from the declared twelve-customer parent; no invented customers.')
    write_json(shared/'design_metrics.json', stats)
    source_shared = source/'shared'/'small'/original['id']
    provenance = dict(base_case_id=case_id, family_id=base['family_id'], source_block=base['source_block'],
        source_dataset=SOURCE, customer_selection=selection, demand_contract=CONTRACT,
        source_files={key:reference(source_shared/name) for key,name in (
            ('data','network.csv'), ('orders','reference_orders.json'), ('routing_config','fleet.json'),
            ('operating_policy','reference_policy.json'), ('economic_config','economic.json'))},
        preserved_orders_sha256=preserved_orders_hash(orders),
        preserved_policy_sha256=preserved_policy_hash(policy),
        preserved_hash_scope='Frozen selected reference orders; independently checked against the named source subset.',
        design=stats)
    write_json(shared/'provenance.json', provenance)
    base.update(satellite_count=stats['satellites'], external_period_count=stats['external_periods'],
                demand_profile=stats['profile'])
    entries = []
    common = dict(data=reference(shared/'network.csv'), routing_config=reference(shared/'fleet.json'),
                  economic_config=reference(shared/'economic.json'), dataset_metadata=reference(shared/'provenance.json'))
    for model in range(1, 9):
        folder = out/f'model_{model}'/'small'/case_id
        morders = deepcopy(orders)
        mpolicy = single_policy(policy) if model <= 4 else deepcopy(policy)
        if model <= 4:
            for order in morders:
                order.update(delivery_batch_id='b1', allowed_batch_ids=['b1'])
        elif model == 5:
            for order in morders:
                order['allowed_batch_ids'] = [order['delivery_batch_id']]
        if model == 6:
            cfg, fleet = load_routing_case(base['models']['5'], use_pool_config=False)
            cert = productive_count_certificate(load_instance(cfg, fleet))
            write_json(shared/'batch_count_certificate.json', cert)
        options = (dict(schedule_mode={6:'uniform_cycle',7:'uniform_independent_cutoffs',8:'flexible_cycle'}[model],
            batch_count_candidates=cert['candidates_by_model'][str(model)], min_phase_minutes=1,
            pool_min=1, pool_max=10) if model >= 6 else {})
        write_json(folder/'orders.json', morders)
        write_json(folder/'policy.json', mpolicy)
        contract = parameter_contract(model, base, morders, mpolicy, options)
        contract.update(demand_contract=CONTRACT, external_appointment_period_count=stats['external_periods'],
                        economics_status='Inherited cost template; no price changes in the customer-count revision.')
        contract['notes'] = [s.replace('M1-M5', 'M1-M4') for s in contract['notes']]
        write_json(folder/'parameters.json', contract)
        cfg = dict(schema_version=2, case_name=f'{VERSION}_{case_id}_M{model}', model=model, solver='alns',
            **common, orders=reference(folder/'orders.json'), operating_policy=reference(folder/'policy.json'),
            exact_backend='joint_batch_indexed' if model >= 6 else 'multibatch_indexed',
            exact_time=300, alns_time=30, alns_iter=10**9, seed=42, save_plots=False,
            dataset_version=VERSION, base_case_id=case_id, parameter_contract=reference(folder/'parameters.json'))
        if model == 5:
            cfg.update(objective_mode='cost', pool_options=dict(pool_min=1, pool_max=10))
        else:
            cfg['container_pool_size'] = 10
        if options:
            cfg.update(joint_batch_options=options, batch_count_certificate=reference(shared/'batch_count_certificate.json'))
        write_json(folder/'case.json', cfg)
        entry = dict(base_case_id=case_id, model=model, size='small', customer_count=10,
            family_id=base['family_id'], source_block=base['source_block'], total_container_uses=10,
            case_path=reference(folder/'case.json'), parameters=reference(folder/'parameters.json'))
        entries.append(entry)
        base['models'][str(model)] = entry['case_path']
    return base, entries, stats


def build(output=DEFAULT_OUTPUT):
    source, out = (PROJECT_ROOT/SOURCE).resolve(), (PROJECT_ROOT/output).resolve()
    if source == out or source in out.parents or out in source.parents:
        raise ValueError('New dataset must be separate from the source dataset')
    if out.exists() and any(out.iterdir()):
        raise ValueError(f'Refusing to overwrite nonempty dataset: {out}')
    out.mkdir(parents=True, exist_ok=True)
    source_hashes = {p.relative_to(source).as_posix():digest(p) for p in source.rglob('*') if p.is_file()}
    old = read(source/'manifest.json')
    original = {b['id']:b for b in old['base_cases']}
    old_stats = {r['id']:r for r in read(source/'design.json')['cases']}
    manifest = {k:deepcopy(v) for k,v in old.items() if k not in (
        'cases','base_cases','artifact_sha256','feasibility_certification')}
    manifest.update(dataset_version=VERSION, dataset_revision=3, source_dataset=SOURCE,
        source_dataset_manifest_sha256=digest(source/'manifest.json'),
        generator=reference(__file__), generator_sha256=digest(__file__),
        customer_count_quotas=dict(small={'10':9}, medium={'20':4,'30':5}, large={'50':4,'100':5}),
        cases=[], base_cases=[])
    stats, selections = [], {}
    for old_base in old['base_cases']:
        name = old_base['id']
        if old_base['size'] == 'small':
            parent, mode = SELECTION[name]
            if mode != 'all':
                base, entries, metrics = build_small(source, out, original[parent], name, int(name[1:])-1, mode)
            else:
                base = copy_case(source, out, old_base)
                entries = [json.loads(rebase(json.dumps(r), source, out)) for r in old['cases'] if r['base_case_id']==name]
                metrics = deepcopy(old_stats[name])
                base['customer_selection'] = dict(source_base_case_id=name, mode='all', customer_count=10,
                    selected_customer_ids=select_customers(read(source/'shared'/'small'/name/'reference_orders.json'), 'all'))
            selections[name] = base['customer_selection']
        else:
            base = copy_case(source, out, old_base)
            entries = [json.loads(rebase(json.dumps(r), source, out)) for r in old['cases'] if r['base_case_id']==name]
            metrics = deepcopy(old_stats[name])
        manifest['base_cases'].append(base)
        manifest['cases'].extend(entries)
        stats.append(metrics)
        print(f'BUILT {name} customers={base["customer_count"]}', flush=True)
    manifest['source_block_count'] = len({b['source_block'] for b in manifest['base_cases']})
    manifest['limitations'] = [s for s in manifest['limitations'] if not s.startswith('Coverage design,')]
    manifest['limitations'].append('Small cases use six raw source blocks: three native ten-customer networks and two overlapping ten-customer subsets per twelve-customer source. Nine small cases are not nine independent sources.')
    protocol = read(source/'comparison_protocol.json')
    protocol.update(dataset_revision=3, customer_count_unchanged=False,
        customer_count_quotas=manifest['customer_count_quotas'],
        selection='Small: retain native ten-customer S04-S06; S01-S03 use last ten and S07-S09 first ten numeric IDs from former S07-S09. No routing-result screening. Medium/large preserved.',
        vehicle_capacity_policy=dict(revision=2, E1_weight='20 * E1 volume capacity',
            E2_small=dict(volume=3,weight=60), E2_medium_large=dict(volume=6,weight=120),
            vehicle_counts='All small cases now follow the existing ten-customer fleet: two E1 and four E2; medium/large unchanged.', weight_unit='benchmark demand units'))
    write_json(out/'comparison_protocol.json', protocol)
    write_json(out/'selection.json', dict(source_dataset=SOURCE, cases=selections,
        unchanged_sizes=['medium','large'], customer_count_quotas=manifest['customer_count_quotas'],
        selected_without_solver_results=True))
    write_json(out/'design.json', dict(date='2026-10-23',source_dataset=SOURCE,protocol=protocol,cases=stats))
    write_design_docs(out, stats)
    readme = (out/'README.md').read_text(encoding='utf-8')
    readme = readme.replace('formal_benchmark27_spacetime_v2', VERSION)
    readme = readme.replace('- 保留S01—S09、M01—M09、L01—L09的顾客集合、顾客与中央仓坐标、完整货物重量和一客一箱规则；',
        '- 修订3将S01—S09统一为10位顾客：S04—S06保留原10位顾客；S01—S03取原S07—S09的末10位，S07—S09取对应来源的前10位（均按顾客编号数值排序）。保留所选顾客的真实坐标和完整货重，一客一箱；中大规模顾客集合保持。')
    readme = readme.replace('一级体积容量依次为7/10/12/20/30/30/60，对应7/10/12/20/30/50/100顾客。',
        '小规模统一一级2辆、体积10、载重200，二级共4辆；中大规模沿用修订2。')
    readme += '\n## 小规模来源与历史结果\n\n9个小规模算例来自6个原始来源；同一12顾客来源的前10位与末10位子集重叠8位顾客，必须保留来源配对，不能称为9个独立样本。选择清单见[selection.json](selection.json)。\n\n修订2及其求解结果保留。修订3已有调度见证才可声明输入可行；它不代表完成了新一轮精确或ALNS实验。中大规模和S04—S06仅重定位运行路径，保留原输入内容及费用。\n'
    (out/'README.md').write_text(readme, encoding='utf-8')
    with (out/'case_index.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(manifest['cases'][0]))
        writer.writeheader()
        writer.writerows(manifest['cases'])
    for model in range(1,9):
        (out/f'model_{model}'/'README.md').write_text(f'# 模型{model}\n\n小规模9例均为10位顾客。27例入口见根目录case_index.csv。\n',encoding='utf-8')
    if not all(digest(source/name)==expected for name,expected in source_hashes.items()):
        raise ValueError('Source dataset changed during generation')
    write_json(out/'source_integrity.json',dict(passed=True,source_dataset=SOURCE,
        files_checked=len(source_hashes),sha256=source_hashes))
    manifest['artifact_sha256'] = {p.relative_to(out).as_posix():digest(p) for p in out.rglob('*') if p.is_file()}
    write_json(out/'manifest.json',manifest)
    return out


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default=DEFAULT_OUTPUT)
    print(build(parser.parse_args().output))
