"""Build fixed-assignment B=1/2/3 pairs without invoking a routing solver.

Orders, windows and resources are identical within a family. Only the fixed
delivery assignment and batch calendar vary. The generated recovery profiles
are scenarios, not observations of reusable-container returns.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VERSION = 'static_batch_suite_v3_one_box'
from tools.data.design_static_scenarios import design_orders, select_territories
from tools.configuration.loader import LEGACY_CONFIG_ROOT, LEGACY_DATA_ROOT, resolve_project_path

DEFAULT_OUTPUT = ROOT/'output/generated_datasets/static_batch_suite'
ZHOU_PROFILE = 'zhou_10_per_customer_count_v4'
ZHOU_CUSTOMERS_BY_SIZE = {'small': (7, 10, 12), 'medium': (20, 30), 'large': (50, 100)}
# Frozen before regeneration; never select on solver objective/status/runtime.
# Small: first five source IDs for each customer count. Keep the same five
# 100-customer parents for nested 20/30/50-customer cases. Both temporal
# profiles share their subset, territories, quantities and fixed resources.
ZHOU_SMALL_SOURCES = {n: tuple(f'Set1_{n}-2-{suffix}' for suffix in ('1', '1b', '2', '2b', '3'))
                      for n in ZHOU_CUSTOMERS_BY_SIZE['small']}
ZHOU_PARENT_SOURCES = ('Set2_100-5-1b', 'Set2_100-5-2', 'Set2_100-5-3',
                       'Set2_100-10-1', 'Set2_100-10-2')
NETWORK_FIELDS = ['node_id', 'node_type', 'x', 'y', 'delivery_demand', 'service_time',
                  'delivery_ready', 'delivery_due', 'pickup_ready', 'pickup_due', 'assigned_sat']


def read_csv(path):
    with resolve_project_path(path).open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def reference(path):
    path = Path(path).resolve()
    # V2's provenance names are part of its byte-identical published dataset.
    if path.is_relative_to(LEGACY_CONFIG_ROOT):
        return (Path('config') / path.relative_to(LEGACY_CONFIG_ROOT)).as_posix()
    if path.is_relative_to(LEGACY_DATA_ROOT):
        return (Path('data') / path.relative_to(LEGACY_DATA_ROOT)).as_posix()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def digest(path):
    return hashlib.sha256(resolve_project_path(path).read_bytes()).hexdigest()


def stable_key(family, identifier):
    # Spatial subset selection is frozen independently of the demand release.
    return hashlib.sha256(f'static_batch_suite_v2:{family}:{identifier}'.encode()).hexdigest()


def encode_json(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode()


def encode_csv(rows, fields):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode()


def policy(batches, start, span, recovery, tail, pool, cleaning, trips=6, closing_recovery=None, closing_tail=None):
    # B2 merges the first two B3 waves. Final cutoff and cycle end stay fixed.
    widths = {1: [3*span], 2: [2*span, span], 3: [span]*3}[batches]
    if closing_tail is not None:
        widths[-1] += tail-closing_tail
    recoveries = [recovery]*batches
    if closing_recovery is not None:
        recoveries[-1] = closing_recovery
    return dict(policy_version='multi_batch_cutoff_v1', time_unit='minutes', time_tick_seconds=1,
                source_type='scenario_assumption', description='Fixed calendar; all demand and windows known.',
                cycle_start=start, cycle_end=start+3*span+tail,
                batches=[dict(id=f'b{i+1}', delivery_duration=w-recoveries[i], return_duration=recoveries[i])
                         for i, w in enumerate(widths)], container_pool_size=pool,
                cleaning_duration=cleaning, e1_load_time=2, e2_dispatch_prep_time=1,
                e2_return_unload_time=1, depot_return_unload_time=1,
                e1_turnaround_time=0, e2_turnaround_time=1,
                e2_max_trips_per_vehicle=trips, terminal_policy='all_clean_at_depot')


def assigned_batch(cohort, batches):
    return 0 if batches == 1 else (int(cohort == 2) if batches == 2 else cohort)


def build_suite(output=DEFAULT_OUTPUT, source_root=ROOT):
    output, source_root = Path(output).resolve(), Path(source_root).resolve()
    files, records, families = {}, [], []
    design_path = source_root/'config/datasets/static_batch_one_box_v1.json'
    design = json.loads(design_path.read_text())

    def put(path, value):
        files[path] = encode_json(value)

    def family(name, size, kind, rows, orders, cohorts, base_policy, fleet, sources, provenance, matrices=None):
        folder = output/'shared'/size/name
        network = []
        for row in rows:
            network.append(dict(node_id=row['node_id'], node_type=row['node_type'],
                                x=float(row['x']), y=float(row['y']), delivery_demand=0,
                                service_time=float(row.get('service_time') or 0),
                                delivery_ready=base_policy['start'],
                                delivery_due=base_policy['start']+3*base_policy['span']+base_policy['tail'],
                                pickup_ready=base_policy['start'],
                                pickup_due=base_policy['start']+3*base_policy['span']+base_policy['tail'],
                                assigned_sat=row.get('assigned_sat', '')))
        files[folder/'network.csv'] = encode_csv(network, NETWORK_FIELDS)
        satellites = sorted(r['node_id'] for r in rows if r['node_type'] == 's')
        by_site = Counter()
        weight_by_site = Counter()
        parallel = Counter()
        for o in orders:
            by_site[o['satellite_id']] += o['container_count']
            weight_by_site[o['satellite_id']] += o['goods_weight']+o['container_count']*fleet['container_weight']
            parallel[o['satellite_id'], cohorts[o['order_id']]] += 1
        original_fleet = dict(fleet)
        if kind == 'synthetic_mechanism':
            fleet = {**fleet, 'E1_vehicle_count': max(fleet['E1_vehicle_count'], len(satellites)),
                     'E1_Qk': max(fleet['E1_Qk'], max(weight_by_site.values(), default=0)),
                     'E1_Ck': max(fleet['E1_Ck'], max(by_site.values(), default=0)*fleet['container_volume']),
                     'vehicles_per_satellite': max(fleet['vehicles_per_satellite'], max(parallel.values(), default=0))}
        elif any(n > fleet['E1_Ck']+1e-8 for n in (v*fleet['container_volume'] for v in by_site.values())):
            raise ValueError(f'{name}: fixed E1 volume capacity insufficient; no automatic expansion')
        put(folder/'fleet.json', fleet)
        total = sum(o['container_count'] for o in orders)
        source_hashes = {reference(p): digest(p) for p in sources}
        provenance = {**provenance, 'family_id': name, 'source_kind': kind, 'size': size,
                      'input_source_sha256': source_hashes, 'original_fleet': original_fleet,
                      'fleet_rule': ('Preserved dedicated mechanism fixture.' if kind == 'synthetic_mechanism' else
                                    'Published size-based fleet and capacities; identical across paired profiles and B1/B2/B3; no demand-driven expansion.'),
                      'cohort_by_order': cohorts,
                      'pairing_contract': 'All order fields except delivery_batch_id are identical; '
                                          'same network, fleet, pool, cycle and final cutoff.',
                      'return_batch': 'Not an input. Derived by the solver/auditor from actual unload times.'}
        put(folder/'provenance.json', provenance)
        if matrices:
            ids = [r['node_id'] for r in network]
            for matrix_name, source in matrices.items():
                matrix = {r['node_id']: r for r in read_csv(source)}
                files[folder/f'{matrix_name}.csv'] = encode_csv(
                    [dict(node_id=i, **{j: float(matrix[i][j]) for j in ids}) for i in ids], ['node_id']+ids)
        family_cases = {}
        for b in (1, 2, 3):
            batch_mode = 'single_batch' if b == 1 else 'multi_batch'
            case_folder = output/batch_mode/size/name
            if b > 1:
                case_folder = case_folder/f'b{b}'
            transformed = [{**o, 'delivery_batch_id': f'b{assigned_batch(cohorts[o["order_id"]], b)+1}'} for o in orders]
            p = policy(b, **base_policy, pool=total)
            put(case_folder/'orders.json', transformed)
            put(case_folder/'policy.json', p)
            case_id = f'{name}_b{b}'
            cfg = dict(schema_version=2, case_name=case_id, model=3 if b == 1 else 5, solver='alns',
                       description=f'{kind}; {size}; fixed {b} batches; recovery windows are scenarios.',
                       data=reference(folder/'network.csv'), orders=reference(case_folder/'orders.json'),
                       routing_config=reference(folder/'fleet.json'), operating_policy=reference(case_folder/'policy.json'),
                       exact_backend='multibatch_indexed', exact_time=30, alns_time=5, alns_iter=5000, seed=42,
                       dataset_metadata=reference(folder/'provenance.json'))
            if matrices:
                cfg.update(distance_matrix=reference(folder/'distance_km.csv'), duration_matrix=reference(folder/'duration_min.csv'))
            case_path = case_folder/'case.json'
            put(case_path, cfg)
            family_cases[f'b{b}'] = reference(case_path)
            demand = Counter()
            for o in transformed:
                demand[o['delivery_batch_id']] += o['container_count']
            suggested = (sorted({max(demand.values()), (total+max(demand.values()))//2, total})
                         if kind == 'synthetic_mechanism' else [total] if b == 1 else
                         sorted({max(demand.values()), *(max(1, math.ceil(total*r)) for r in design['appointments']['pool_ratios'])}))
            records.append(dict(case_id=case_id, family_id=name,
                                group=batch_mode, comparison_models=[1, 2, 3, 4] if b == 1 else [5],
                                size=size, source_kind=kind, batch_count=b,
                                customer_count=sum(r['node_type'] == 'c' for r in rows), order_count=len(orders),
                                total_container_uses=total, default_pool=total,
                                batch_demands={f'b{i+1}': demand[f'b{i+1}'] for i in range(b)},
                                suggested_pool_sizes=suggested, case_path=reference(case_path)))
        families.append(dict(family_id=name, provenance=reference(folder/'provenance.json'), source_kind=kind,
                             size=size, cases=family_cases))

    # Two small mechanism families: forced late vs cutoff-sensitive first return.
    demo = source_root/'data/source_inputs/mechanism'
    demo_fleet_path = resolve_project_path(source_root/'config/routing/validation/multibatch_fleet.json')
    rows = read_csv(demo/'network.csv')
    for mode in ('forced_late', 'cutoff_choice'):
        orders, cohorts = [], {}
        for stage in range(3):
            for s, c in [('A', 'CA'), ('B', 'CB')]:
                oid = f'{s}{stage+1}'
                ready, due = ((32, 40) if mode == 'forced_late' else (22, 35)) if stage == 0 and s == 'A' else (15, 20)
                orders.append(dict(order_id=oid, customer_id=c, satellite_id=s, container_count=1, goods_weight=2,
                                   delivery_ready=stage*60, delivery_due=stage*60+25,
                                   pickup_ready=stage*60+ready, pickup_due=stage*60+due,
                                   service_time=1, min_use_time=2))
                cohorts[oid] = stage
        family(f'mechanism_{mode}', 'small', 'synthetic_mechanism', rows, orders, cohorts,
               dict(start=0, span=60, recovery=30, tail=10, cleaning=5, trips=6),
               json.loads(demo_fleet_path.read_text()), [demo/'network.csv', demo_fleet_path],
               dict(window_rule=mode, time_unit='minutes', distance_unit='synthetic_coordinate_units',
                    observed_fields=[], scenario_fields='All fields; six uses at two repeated customer locations.'))

    zhou = source_root/'data/zhou_later_pickup'
    selected = [(zhou/'small/standard_same_day'/f'{name}.csv', None, 'small', n)
                for n, names in ZHOU_SMALL_SOURCES.items() for name in names]
    selected += [(zhou/'large/standard_same_day'/f'{name}.csv', limit, size, 100)
                 for size, limit in (('medium', 20), ('medium', 30), ('large', 50), ('large', None))
                 for name in ZHOU_PARENT_SOURCES]
    selected = [(*entry, scenario) for scenario in (1, 2) for entry in selected]
    for source, limit, size, source_customers, scenario in selected:
        rows = read_csv(source)
        customers = [r for r in rows if r['node_type'] == 'c']
        if len(customers) != source_customers:
            raise ValueError(f'Unexpected Zhou source customer count: {source}')
        base_name = f'zhou_{source.stem}' + (f'_subset{limit}' if limit else '')
        name = base_name + ('_scenario02' if scenario == 2 else '')
        customers.sort(key=lambda r: stable_key(source.stem+':nested_customers', r['node_id']))
        customers = customers[:limit] if limit else customers
        keep = {r['node_id'] for r in customers}
        rows = [r for r in rows if r['node_type'] != 'c' or r['node_id'] in keep]
        n = len(customers)
        rows, territory_cap = select_territories(rows, design['zhou']['satellites_by_customers'][str(n)],
                                                design['zhou']['territory_customer_cap_ratio'])
        rows = [{**r, **({'service_time': design['zhou']['satellite_service']} if r['node_type']=='s' else {})}
                for r in rows]
        z = design['zhou']
        fleet = dict(schema_version=1, source_type='scenario_assumption',
                     parameter_scope='operational_scenarios_v2_zhou',
                     units=dict(weight='benchmark demand units', volume='benchmark volume units',
                                speed='benchmark coordinate units per minute'),
                     E1_vehicle_count=z['satellites_by_customers'][str(n)],
                     E1_Qk=30*n, E1_Ck=2*n,
                     vehicles_per_satellite=z['e2_per_satellite_by_customers'][str(n)],
                     E2_Qv=z['e2_weight_capacity'], E2_Cv=z['e2_volume_capacity'],
                     container_weight=z['container_weight'], container_volume=z['container_volume'], speed=z['speed'])
        orders, cohorts, reference_plan = design_orders(rows, base_name, scenario, fleet, design)
        originals = {r['node_id']+'_order': {k:r[k] for k in ('delivery_ready','delivery_due','pickup_ready','pickup_due','assigned_sat')}
                     for r in customers}
        family(name, size, 'zhou_derived_scenario', rows, orders, cohorts,
               design['calendar'], fleet, [source, design_path, Path(__file__).with_name('design_static_scenarios.py')],
               dict(selection_profile=ZHOU_PROFILE, design_version=design['design_version'],
                    business_assumption=design['business_assumption'],
                    source_instance=source.stem, base_family_id=base_name, scenario_index=scenario,
                    scenario_rule='Paired use-duration/collection profiles; identical customer subset, territories, demand and fleet.',
                    scale_pair_id=None if size == 'small' else source.stem,
                    selection_rule='Five source blocks; source-keyed SHA256 ordering; nested 20/30/50 subsets; identical subsets within both profiles; no solver filtering.',
                    territory_rule='Choose source satellites by summed nearest distance; seed each with a distinct nearby customer, then assign distant customers first to nearest available territory under a published count cap.',
                    territory_customer_cap=territory_cap,
                    cohort_rule='Per-territory customer hash rank modulo three, independent of use/return hash streams.',
                    time_unit='scenario_minutes', distance_unit='benchmark_coordinate_units_not_km',
                    preserved_fields=['selected_source_locations','goods_weight','customer_service_time'],
                    scenario_fields=['satellite_subset','territories','satellite_service','travel_speed','box_count','windows','use_duration','fleet','calendar'],
                    original_windows=originals, **reference_plan,
                    window_rule='One operating day; delivery and return appointment windows around disclosed feasible reference tours.',
                    limitation='Derived short-use transport-box scenarios, not original benchmark windows or observed operations.'))
        scenario_metadata = dict(base_family_id=base_name, scenario_index=scenario, source_instance=source.stem)
        families[-1].update(scenario_metadata)
        for record in records[-3:]:
            record.update(scenario_metadata)

    # Real-based families retain observed AOI/day counts, locations and the
    # already saved asymmetric road matrices; no additional road API requests.
    real = resolve_project_path(source_root/'data/real_case/processed/lade_hz_0823_0829_30aoi')
    geography = read_csv(real/'nodes_geographic.csv')
    ranking = sorted((r for r in geography if r['node_type'] == 'c'),
                     key=lambda r: (-float(r['weekly_orders']), r['node_id']))
    real_fleet = resolve_project_path(source_root/'config/routing/real_cases/lade_hangzhou.json')
    for source in sorted((real/'instances').glob('*.csv')):
        for size, n in [('small', 10), ('medium', 20), ('medium', 30)]:
            keep = {r['node_id'] for r in ranking[:n]}
            rows = [r for r in read_csv(source) if r['node_type'] != 'c' or r['node_id'] in keep]
            name = f'lade_{source.stem.split("_")[2]}_{n}aoi'
            real_design = design['lade']
            fleet = dict(schema_version=1, source_type='scenario_assumption',
                         parameter_scope='operational_scenarios_v2_lade',
                         units=dict(weight='kg', volume='m3', distance='km', time='minute', speed='km per minute'),
                         E1_vehicle_count=3, E1_Qk=real_design['e1_weight_capacity'], E1_Ck=real_design['e1_volume_capacity'],
                         vehicles_per_satellite=real_design['e2_per_satellite_by_customers'][str(n)],
                         E2_Qv=real_design['e2_weight_capacity'], E2_Cv=real_design['e2_volume_capacity'],
                         container_weight=real_design['container_weight'], container_volume=real_design['container_volume'], speed=.5)
            times = {r['node_id']:{k:float(v) for k,v in r.items() if k!='node_id'} for r in read_csv(real/'duration_min.csv')}
            profile = 1+int(source.stem.split('_')[2]) % 2
            orders, cohorts, reference_plan = design_orders(rows, name, profile, fleet, design, road_times=times, lade=True)
            originals = {r['node_id']+'_order': {k:r[k] for k in ('delivery_ready','delivery_due','pickup_ready','pickup_due')}
                         for r in rows if r['node_type']=='c'}
            family(name, size, 'lade_derived_scenario', rows, orders, cohorts,
                   design['calendar'], fleet,
                   [source, design_path, Path(__file__).with_name('design_static_scenarios.py'), real/'metadata.json',
                    real/'nodes_geographic.csv', real/'distance_km.csv', real/'duration_min.csv'],
                   dict(design_version=design['design_version'], business_assumption=design['business_assumption'],
                        selection_rule='Nested top 10/20/30 AOIs by saved weekly order counts.',
                        scale_basis='AOI/day aggregated demand; not individual customers.',
                        observed_basis=['AOI/day delivery counts','AOI coordinates'],
                        derived_basis=['proxy depot/satellites','saved OSRM matrices'],
                        scenario_fields=['goods weights','one parcel per container','fleet','appointments','minimum use','100 percent same-cycle returns','cleaning'],
                        time_unit='minutes', distance_unit='km', original_windows=originals,
                        recovery_rule='Same-day transport-box scenario; historical returns and original service bands retained only in provenance.',
                        window_rule='Published constructive appointment rule; no cyclic-index coupling of cohort and return type.',
                        source_metadata=reference(real/'metadata.json'), **reference_plan),
                   matrices={'distance_km': real/'distance_km.csv', 'duration_min': real/'duration_min.csv'})

    zhou_cases = [r for r in records if r['source_kind'] == 'zhou_derived_scenario']
    zhou_counts = Counter((r['size'], r['batch_count'], r['customer_count']) for r in zhou_cases)
    if zhou_counts != Counter({(s, b, n): 10 for s, ns in ZHOU_CUSTOMERS_BY_SIZE.items() for n in ns
                              for b in (1, 2, 3)}):
        raise ValueError('Expected exactly ten Zhou cases per 7/10/12/20/30/50/100 customer count')
    comparison = ['# Zhou 主对比集：每种顾客数十例', '',
                  f'选样版本：`{ZHOU_PROFILE}`。70 个基础场景；B1、B2、B3 各 70 个配置。',
                  'B1 供 M1–M4 共用；B2/B3 供 M5 使用。重复 ALNS 种子不增加算例数。', '',
                  '小规模：7、10、12 顾客各 10 例，共 30 例；中规模：20、30 顾客各 10 例，共 20 例；',
                  '大规模：50、100 顾客各 10 例，共 20 例。',
                  '每档 5 个来源块×2 个使用与回收情景，合计 10 例；不是 10 个独立原始样本。',
                  '同源两情景共用顾客子集、仓库、固定分区、箱数、车队及需求批次，只改变使用与预约回收节奏。',
                  '20/30/50/100 顾客使用来源级固定排序形成嵌套子集，仓库数随规模为 3/3/4/5。',
                  '所有非机制例采用 08:00–21:00 的周转箱情景；来源坐标保留，时间、资源为明确假设。',
                  '仓库仅从来源候选点中选择，固定分区按距离及公开的区域客户数上限构造。',
                  '容量与车辆数由 config/datasets/static_batch_v2.json 预先指定，不按每例需求自动扩容。',
                  '已公开构造参考路线证明窗口兼容性；它们不是最优解，求解器不自动读取参考路线。',
                  '同一情景 B1/B2/B3 保持配对；ALNS 种子重复另计，跨规模不能只归因为顾客数量。', '',
                  '| 编号 | 规模 | 顾客数 | 来源实例 | 数据情景 | 单批 M1–M4 | 多批 M5：B2 | 多批 M5：B3 |',
                  '| --- | --- | ---: | --- | --- | --- | --- | --- |']
    for size, prefix in [('small', 'S'), ('medium', 'M'), ('large', 'L')]:
        subset = sorted((r for r in zhou_cases if r['size'] == size and r['batch_count'] == 1),
                        key=lambda r: r['customer_count'])
        by_count = Counter()
        for record in subset:
            name = record['family_id']
            n = record['customer_count']
            by_count[n] += 1
            comparison.append(f'| {prefix}{n:03}-{by_count[n]:02} | {size} | {n} | {record["source_instance"]} | '
                              f'scenario{record["scenario_index"]:02} | '
                              f'[B1](single_batch/{size}/{name}/case.json) | '
                              f'[B2](multi_batch/{size}/{name}/b2/case.json) | '
                              f'[B3](multi_batch/{size}/{name}/b3/case.json) |')
    files[output/'zhou_comparison.md'] = ('\n'.join(comparison)+'\n').encode()
    counts = Counter((r['group'], r['size']) for r in records)
    for mode, title in [('single_batch', '单批次：M1–M4 对比'), ('multi_batch', '多批次：M5')]:
        lines = [f'# {title}', '', '按 small、medium、large 三种规模管理；数据来源保存在 source_kind 字段。', '',
                 '| 规模 | 案例数 |', '| --- | ---: |']
        for size in ('small', 'medium', 'large'):
            subset = [r for r in records if r['group'] == mode and r['size'] == size]
            lines.append(f'| [{size}]({size}/README.md) | {len(subset)} |')
            details = [f'# {title} / {size}', '',
                       '同一 family_id 的 B1/B2/B3 使用相同网络、需求、窗口、车队和默认池。',
                       'case.json 是运行入口；orders.json 和 policy.json 是该批次配置的订单与日程。',
                       '共用网络、车队和来源资料位于 data/shared/，不按模型复制数据。', '',
                       '| 案例 | 来源 | 顾客 | 订单 | 批次数 |', '| --- | --- | ---: | ---: | ---: |']
            for record in subset:
                path = Path(record['case_path'])
                if not path.is_absolute():
                    path = ROOT/path
                relative = path.relative_to(output/mode/size).as_posix()
                details.append(f"| [{record['case_id']}]({relative}) | {record['source_kind']} | "
                               f"{record['customer_count']} | {record['order_count']} | {record['batch_count']} |")
            files[output/mode/size/'README.md'] = ('\n'.join(details)+'\n').encode()
        files[output/mode/'README.md'] = ('\n'.join(lines)+'\n').encode()
    manifest = dict(dataset_version=VERSION, layout_version=2, design_version=design['design_version'],
                    design_parameters=reference(design_path), design_sha256=digest(design_path),
                    zhou_selection_profile=dict(version=ZHOU_PROFILE,
                                                customers_by_size={s: list(ns) for s, ns in ZHOU_CUSTOMERS_BY_SIZE.items()},
                                                families_per_customer_count=10, source_blocks_per_customer_count=5,
                                                scenarios_per_source_block=2,
                                                small_sources={str(n): list(names) for n, names in ZHOU_SMALL_SOURCES.items()},
                                                medium_large_sources=list(ZHOU_PARENT_SOURCES)),
                    information_mode='static_all_known', assignment_mode='fixed_input',
                    generator=reference(Path(__file__)), generator_sha256=digest(__file__),
                    source_root=reference(source_root), family_count=len(families), case_count=len(records),
                    counts={g: {s: counts[g, s] for s in ('small', 'medium', 'large')}
                            for g in ('single_batch', 'multi_batch')},
                    source_counts=dict(Counter(r['source_kind'] for r in records)),
                    families=families, cases=records,
                    artifact_sha256={str(path.relative_to(output)): hashlib.sha256(data).hexdigest()
                                     for path, data in sorted(files.items())})
    put(output/'manifest.json', manifest)
    # Idempotent re-generation accepts identical files, but never overwrites
    # changed source data or a previous version with different contents.
    for path, data in files.items():
        if path.exists() and path.read_bytes() != data:
            raise ValueError(f'Refusing to overwrite changed dataset file: {path}; use a new --output')
    for path, data in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(data)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    result = build_suite(args.output)
    print(json.dumps({k: result[k] for k in ('dataset_version', 'family_count', 'case_count', 'counts')}, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    main()
