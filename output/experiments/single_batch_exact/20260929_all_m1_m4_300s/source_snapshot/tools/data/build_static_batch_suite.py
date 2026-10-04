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
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VERSION = 'static_batch_suite_v1'
DEFAULT_OUTPUT = ROOT/'data/multibatch/static_suite_v1'
NETWORK_FIELDS = ['node_id', 'node_type', 'x', 'y', 'delivery_demand', 'service_time',
                  'delivery_ready', 'delivery_due', 'pickup_ready', 'pickup_due', 'assigned_sat']


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def reference(path):
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stable_key(family, identifier):
    return hashlib.sha256(f'{VERSION}:{family}:{identifier}'.encode()).hexdigest()


def encode_json(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode()


def encode_csv(rows, fields):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode()


def policy(batches, start, span, recovery, tail, pool, cleaning, trips=6):
    # B2 merges the first two B3 waves. Final cutoff and cycle end stay fixed.
    widths = {1: [3*span], 2: [2*span, span], 3: [span]*3}[batches]
    return dict(policy_version='multi_batch_cutoff_v1', time_unit='minutes', time_tick_seconds=1,
                source_type='scenario_assumption', description='Fixed calendar; all demand and windows known.',
                cycle_start=start, cycle_end=start+3*span+tail,
                batches=[dict(id=f'b{i+1}', delivery_duration=w-recovery, return_duration=recovery)
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

    def put(path, value):
        files[path] = encode_json(value)

    def family(name, size, kind, rows, orders, cohorts, base_policy, fleet, sources, provenance, matrices=None):
        folder = output/'families'/name
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
        fleet = {**fleet, 'E1_vehicle_count': max(fleet['E1_vehicle_count'], len(satellites)),
                 'E1_Qk': max(fleet['E1_Qk'], max(weight_by_site.values(), default=0)),
                 'E1_Ck': max(fleet['E1_Ck'], max(by_site.values(), default=0)*fleet['container_volume']),
                 'vehicles_per_satellite': max(fleet['vehicles_per_satellite'], max(parallel.values(), default=0))}
        put(folder/'fleet.json', fleet)
        total = sum(o['container_count'] for o in orders)
        source_hashes = {reference(p): digest(p) for p in sources}
        provenance = {**provenance, 'family_id': name, 'source_kind': kind, 'size': size,
                      'input_source_sha256': source_hashes, 'original_fleet': original_fleet,
                      'fleet_rule': 'Same fleet in B1/B2/B3; E1 can carry one complete satellite shipment; '
                                    'one E1 per satellite available; E2 count >= largest designed cohort per satellite.',
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
        for b in (1, 2, 3):
            transformed = [{**o, 'delivery_batch_id': f'b{assigned_batch(cohorts[o["order_id"]], b)+1}'} for o in orders]
            p = policy(b, **base_policy, pool=total)
            put(folder/f'orders_b{b}.json', transformed)
            put(folder/f'policy_b{b}.json', p)
            case_id = f'{name}_b{b}'
            cfg = dict(schema_version=2, case_name=case_id, model=3 if b == 1 else 5, solver='alns',
                       description=f'{kind}; {size}; fixed {b} batches; recovery windows are scenarios.',
                       data=reference(folder/'network.csv'), orders=reference(folder/f'orders_b{b}.json'),
                       routing_config=reference(folder/'fleet.json'), operating_policy=reference(folder/f'policy_b{b}.json'),
                       exact_backend='multibatch_indexed', exact_time=30, alns_time=5, alns_iter=5000, seed=42,
                       dataset_metadata=reference(folder/'provenance.json'))
            if matrices:
                cfg.update(distance_matrix=reference(folder/'distance_km.csv'), duration_matrix=reference(folder/'duration_min.csv'))
            case_path = output/'cases'/f'{case_id}.json'
            put(case_path, cfg)
            demand = Counter()
            for o in transformed:
                demand[o['delivery_batch_id']] += o['container_count']
            suggested = sorted({max(demand.values()), (total+max(demand.values()))//2, total})
            records.append(dict(case_id=case_id, family_id=name,
                                group='real_case' if kind == 'lade_derived_scenario' else 'single_batch' if b == 1 else 'multi_batch',
                                size=size, source_kind=kind, batch_count=b,
                                customer_count=sum(r['node_type'] == 'c' for r in rows), order_count=len(orders),
                                total_container_uses=total, default_pool=total,
                                batch_demands={f'b{i+1}': demand[f'b{i+1}'] for i in range(b)},
                                suggested_pool_sizes=suggested, case_path=reference(case_path)))
        families.append(dict(family_id=name, provenance=reference(folder/'provenance.json'), source_kind=kind, size=size))

    # Two small mechanism families: forced late vs cutoff-sensitive first return.
    demo = source_root/'data/multibatch/demo'
    demo_fleet_path = source_root/'config/routing/validation/multibatch_fleet.json'
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
    selected = [(p, None, size) for size in ('small', 'large')
                for p in sorted((zhou/size/'standard_same_day').glob('*.csv'))]
    medium_sources = ['Set2_100-5-1b', 'Set2_100-5-2', 'Set2_100-10-1']
    selected += [(zhou/'large/standard_same_day'/f'{name}.csv', n, 'medium')
                 for name in medium_sources for n in (30, 50)]
    if len(selected) != 46:
        raise ValueError('Expected 22 small, 18 large and 6 medium Zhou families')
    for source, limit, size in selected:
        rows = read_csv(source)
        customers = [r for r in rows if r['node_type'] == 'c']
        name = f'zhou_{source.stem}' + (f'_subset{limit}' if limit else '')
        customers.sort(key=lambda r: stable_key(name, r['node_id']))
        customers = customers[:limit] if limit else customers
        keep = {r['node_id'] for r in customers}
        rows = [r for r in rows if r['node_type'] != 'c' or r['node_id'] in keep]
        orders, cohorts, originals = [], {}, {}
        for index, r in enumerate(customers):
            stage, role = index % 3, (index//3) % 3
            anchor = stage*720
            oid = r['node_id']+'_order'
            dready = anchor+120+int(float(r['delivery_ready'])) % 60
            pick = (560, 600) if stage < 2 and role == 0 else (440, 560) if stage < 2 and role == 1 else (360, 420)
            orders.append(dict(order_id=oid, customer_id=r['node_id'], satellite_id=r['assigned_sat'],
                               container_count=1, goods_weight=float(r['delivery_demand']),
                               delivery_ready=dready, delivery_due=dready+120,
                               pickup_ready=anchor+pick[0], pickup_due=anchor+pick[1],
                               service_time=float(r['service_time']), min_use_time=30))
            cohorts[oid] = stage
            originals[oid] = {k: r[k] for k in ('delivery_ready', 'delivery_due', 'pickup_ready', 'pickup_due')}
        fleet_source = source_root/'config/routing/validation'/f'zhou_{"small" if size != "large" else "large"}.json'
        family(name, size, 'zhou_derived_scenario', rows, orders, cohorts,
               dict(start=0, span=720, recovery=180, tail=60, cleaning=30, trips=6),
               json.loads(fleet_source.read_text()), [source, fleet_source],
               dict(selection_rule='SHA256 order, first 30/50 for medium; no solver-dependent filtering.',
                    cohort_rule='SHA256 order index modulo 3; B2 merges first two cohorts; B1 merges all.',
                    time_unit='scenario_minutes', distance_unit='benchmark_coordinate_units_not_km',
                    preserved_fields=['locations', 'assigned_satellite', 'goods_weight', 'service_time'],
                    original_windows=originals,
                    window_rule='New scenario windows: delivery anchor+120+(source ready mod 60), width 120; '
                                'pickup early 360..420, flexible 440..560 or forced-late 560..600; '
                                'last cohort early. Each cohort anchor is 720 minutes apart.',
                    limitation='These are derived benchmark scenarios, not the original Zhou time-window instances.'))

    # Real-based families retain observed AOI/day counts, locations and the
    # already saved asymmetric road matrices; no additional road API requests.
    real = source_root/'data/real_case/processed/lade_hz_0823_0829_30aoi'
    geography = read_csv(real/'nodes_geographic.csv')
    ranking = sorted((r for r in geography if r['node_type'] == 'c'),
                     key=lambda r: (-float(r['weekly_orders']), r['node_id']))
    real_fleet = source_root/'config/routing/real_cases/lade_hangzhou.json'
    for source in sorted((real/'instances').glob('*.csv')):
        for size, n in [('small', 10), ('medium', 20), ('large', 30)]:
            keep = {r['node_id'] for r in ranking[:n]}
            rows = [r for r in read_csv(source) if r['node_type'] != 'c' or r['node_id'] in keep]
            name = f'lade_{source.stem.split("_")[2]}_{n}aoi'
            orders, cohorts, originals = [], {}, {}
            customers = sorted((r for r in rows if r['node_type'] == 'c'), key=lambda r: stable_key(name, r['node_id']))
            for index, r in enumerate(customers):
                ready, due = float(r['delivery_ready']), float(r['delivery_due'])
                allowed = [g for g in range(3) if ready <= 480+240*g+105 and due >= 480+240*g+90]
                if not allowed:
                    raise ValueError(f'{source}: no compatible fixed cohort for {r["node_id"]}')
                stage = allowed[index % len(allowed)]
                anchor = 480+stage*240
                pr, pd = (125, 140) if stage == 2 else (190, 210) if index % 3 == 0 else (145, 165)
                oid = r['node_id']+'_order'
                count = int(r['delivery_container_count'])
                orders.append(dict(order_id=oid, customer_id=r['node_id'], satellite_id=r['assigned_sat'],
                                   container_count=count, goods_weight=float(r['delivery_demand']),
                                   delivery_ready=ready, delivery_due=due, pickup_ready=anchor+pr,
                                   pickup_due=anchor+pd, service_time=float(r['service_time']), min_use_time=15))
                cohorts[oid] = stage
                originals[oid] = dict(original_pickup_count=r['pickup_container_count'],
                                      original_pickup_ready=r['pickup_ready'], original_pickup_due=r['pickup_due'])
            family(name, size, 'lade_derived_scenario', rows, orders, cohorts,
                   dict(start=480, span=240, recovery=60, tail=60, cleaning=15, trips=6),
                   json.loads(real_fleet.read_text()),
                   [source, real_fleet, real/'metadata.json', real/'nodes_geographic.csv',
                    real/'distance_km.csv', real/'duration_min.csv'],
                   dict(selection_rule='Nested top 10/20/30 AOIs by saved weekly order counts.',
                        scale_basis='AOIs within the available 30-AOI real-road snapshot; not 100/200-customer scale.',
                        observed_basis=['AOI/day delivery counts', 'AOI coordinates'],
                        derived_basis=['proxy depot/satellites', 'saved OSRM matrices', 'delivery service-band proxies'],
                        scenario_fields=['one parcel per container', 'goods weights', 'fleet', 'fixed batch assignment',
                                         'pickup windows', '100 percent same-cycle returns', 'cleaning and service times'],
                        time_unit='minutes', distance_unit='km', original_recovery=originals,
                        recovery_rule='Replace historical derived 97-percent lagged returns with balanced per-order '
                                      'scenario returns. Original inputs are preserved; this is not measured recovery.',
                        window_rule='Preserve existing delivery bands; pickup anchored to a fixed compatible cohort.',
                        source_metadata=reference(real/'metadata.json')),
                   matrices={'distance_km': real/'distance_km.csv', 'duration_min': real/'duration_min.csv'})

    counts = Counter((r['group'], r['size']) for r in records)
    manifest = dict(dataset_version=VERSION, information_mode='static_all_known', assignment_mode='fixed_input',
                    generator=reference(Path(__file__)), generator_sha256=digest(__file__),
                    source_root=reference(source_root), family_count=len(families), case_count=len(records),
                    counts={g: {s: counts[g, s] for s in ('small', 'medium', 'large')}
                            for g in ('single_batch', 'multi_batch', 'real_case')},
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
