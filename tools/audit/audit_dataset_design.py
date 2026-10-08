"""Audit spatial, temporal and resource design; never optimize or rewrite inputs.

Bounds use relaxed shortest travel times. Constructive schedules are feasibility
witnesses, not optimal solutions. All output statistics are descriptive.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import replace
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

from tools.configuration.loader import load_routing_case, resolve_project_path
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.instance import MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.precheck import check_necessary_conditions, travel_lower_bounds
from models.routing.solvers.alns.evaluator import evaluate
from tools.audit.audit_static_batch_suite import constructive_state
from tools.data.build_static_batch_suite import read_csv
from tools.data.static_batch_dataset import load_manifest, check_pairing, verify_artifacts


def describe(values):
    values = [float(v) for v in values if v is not None]
    if not values:
        return dict(n=0, min=None, median=None, max=None, mean=None)
    return dict(n=len(values), min=min(values), median=float(np.median(values)),
                max=max(values), mean=float(np.mean(values)))


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def save_csv(path, rows):
    if not rows:
        return
    with path.open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def audit_design(manifest_path, output):
    manifest_path = resolve_project_path(manifest_path)
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Refusing to overwrite nonempty design audit')
    manifest, records = load_manifest(manifest_path)
    verify_artifacts(manifest, manifest_path.parent)
    paired = check_pairing(records)
    output.mkdir(parents=True, exist_ok=True)
    family_rows, order_rows, case_rows, witnesses, pool_checks = [], [], [], [], []
    plots = {}
    for family in manifest['families']:
        fid, kind = family['family_id'], family['source_kind']
        cfg, fleet = load_routing_case(family['cases']['b3'], use_pool_config=False)
        instance = load_instance(cfg, fleet)
        p, f = instance.operating_policy, instance.fleet
        minutes = p.minutes
        raw = json.loads(resolve_project_path(cfg['orders']).read_text())
        provenance = json.loads(resolve_project_path(cfg['dataset_metadata']).read_text())
        nodes = read_csv(resolve_project_path(cfg['data']))
        xy = {r['node_id']: (float(r['x']), float(r['y'])) for r in nodes}
        node_types = {r['node_id']: r['node_type'] for r in nodes}
        cs, ss = [r['node_id'] for r in nodes if r['node_type'] == 'c'], instance.satellites
        coordinate_groups = defaultdict(list)
        for node, point in xy.items():
            coordinate_groups[point].append(node)
        duplicated = [ids for ids in coordinate_groups.values() if len(ids) > 1]
        assigned_counts = Counter(o.satellite for o in instance.orders)
        cohort_counts = Counter((o.satellite, o.batch) for o in instance.orders)
        bounds = travel_lower_bounds(instance)
        necessary = check_necessary_conditions(instance, bounds)
        geometry = sorted((r['node_id'], r['node_type'], r['x'], r['y']) for r in nodes)
        geometry_sha = hashlib.sha256(json.dumps(geometry).encode()).hexdigest()
        raw_by_id = {o['order_id']: o for o in raw}
        family_orders = []
        for o in instance.orders:
            r = raw_by_id[o.id]
            b = instance.batch_map[o.batch]
            ranked = sorted((instance.distance(s, o.customer), s) for s in ss)
            assigned = instance.distance(o.satellite, o.customer)
            second = ranked[1][0] if len(ranked) > 1 else None
            # Direct dedicated E1 and E2 paths give an attainable per-order
            # arrival when competing orders/vehicles are ignored.
            arrival = (b.start+p.e1_load_time+instance.travel(instance.depot, o.satellite)
                       +instance.service(o.satellite)+p.e2_dispatch_prep_time
                       +instance.travel(o.satellite, o.customer))
            earliest_d = max(o.delivery_window[0], arrival)
            earliest_p = max(o.pickup_window[0], earliest_d+o.service_time+o.min_use_time)
            earliest_u = earliest_p+o.service_time+instance.travel(o.customer, o.satellite)+p.e2_return_unload_time
            latest_direct_u = o.pickup_window[1]+o.service_time+instance.travel(o.customer, o.satellite)+p.e2_return_unload_time
            forced_late = earliest_u > b.cutoff
            cutoff_sensitive = earliest_u <= b.cutoff < latest_direct_u
            feasible_sites = 0
            for s in ss:
                supply = b.start+p.e1_load_time+bounds['outward'][instance.depot][s]+instance.service(s)+p.e2_dispatch_prep_time
                d = max(o.delivery_window[0], supply+bounds['outward'][s][o.customer])
                pickup = max(o.pickup_window[0], d+o.service_time+o.min_use_time)
                unload = pickup+o.service_time+bounds['inward'][s][o.customer]+p.e2_return_unload_time
                feasible_sites += int(d <= o.delivery_window[1] and pickup <= o.pickup_window[1]
                                      and unload <= p.batches[-1].cutoff)
            row = dict(family_id=fid, source_kind=kind, customer_count=len(cs), order_id=o.id,
                       satellite_id=o.satellite, assigned_distance=assigned,
                       nearest_distance=ranked[0][0], assigned_nearest=assigned <= ranked[0][0]+1e-6,
                       second_nearest_ratio=second/ranked[0][0] if ranked[0][0] > 1e-9 else None,
                       assigned_travel_min=minutes(instance.travel(o.satellite, o.customer)),
                       container_count=o.count, goods_weight=o.goods_weight,
                       e2_order_weight_utilization=(o.goods_weight+o.count*f.container_weight)/f.e2_weight_cap,
                       e2_order_volume_utilization=o.count*f.container_volume/f.e2_volume_cap,
                       delivery_ready=r['delivery_ready'], delivery_due=r['delivery_due'],
                       pickup_ready=r['pickup_ready'], pickup_due=r['pickup_due'],
                       delivery_width_min=r['delivery_due']-r['delivery_ready'],
                       pickup_width_min=r['pickup_due']-r['pickup_ready'],
                       delivery_direct_slack_min=minutes(o.delivery_window[1]-earliest_d),
                       delivery_wait_before_ready_min=minutes(max(0, o.delivery_window[0]-arrival)),
                       earliest_use_min=minutes(max(o.min_use_time, o.pickup_window[0]-o.delivery_window[1]-o.service_time)),
                       latest_use_min=minutes(o.pickup_window[1]-o.delivery_window[0]-o.service_time),
                       min_use_redundant_for_windows=o.pickup_window[0]>=o.delivery_window[1]+o.service_time+o.min_use_time,
                       pickup_tightens_delivery_due=o.pickup_window[1]-o.service_time-o.min_use_time<o.delivery_window[1],
                       b3_earliest_direct_unload_min=minutes(earliest_u), b3_cutoff_min=minutes(b.cutoff),
                       b3_forced_late_direct=forced_late, b3_cutoff_sensitive_direct=cutoff_sensitive,
                       relaxed_feasible_satellite_count=feasible_sites)
            family_orders.append(row)
            order_rows.append(row)
        original_windows = provenance.get('original_windows', {})
        same_original_delivery = sum(all(float(original_windows.get(o['order_id'], {}).get(k, -1)) == o[k]
                                         for k in ('delivery_ready', 'delivery_due')) for o in raw)
        row = dict(family_id=fid, source_kind=kind, size=family['size'], customer_count=len(cs),
                   order_count=len(raw), satellite_count=len(ss), empty_satellites=sum(assigned_counts[s]==0 for s in ss),
                   satellites_at_most_two_orders=sum(assigned_counts[s]<=2 for s in ss),
                   empty_satellite_batch_cells=sum(cohort_counts[s,b.id]==0 for s in ss for b in p.batches),
                   duplicate_coordinate_groups=len(duplicated), geometry_sha256=geometry_sha,
                   total_containers=instance.total, container_counts_unique=len({o.count for o in instance.orders}),
                   e1_vehicles=f.e1_count, e2_vehicles_per_sat=f.e2_per_sat_count,
                   e2_vehicles_total=f.e2_per_sat_count*len(ss), e2_vehicles_per_order=f.e2_per_sat_count*len(ss)/len(raw),
                   e1_volume_supply_ratio=instance.total*f.container_volume/(f.e1_count*f.e1_volume_cap),
                   e2_total_volume_supply_ratio=instance.total*f.container_volume/(len(ss)*f.e2_per_sat_count*f.e2_volume_cap),
                   fleet_covers_one_vehicle_per_cohort_order=all(cohort_counts[s,b.id]<=f.e2_per_sat_count for s in ss for b in p.batches),
                   pool=instance.operating_policy.container_pool_size, pool_to_demand=instance.operating_policy.container_pool_size/instance.total,
                   cycle_hours=minutes(p.cycle_end-p.cycle_start)/60,
                   min_delivery_time=min(o['delivery_ready'] for o in raw), max_delivery_due=max(o['delivery_due'] for o in raw),
                   customer_service_min=min(minutes(o.service_time) for o in instance.orders),
                   satellite_service_min=min(minutes(instance.service(s)) for s in ss),
                   satellite_service_max=max(minutes(instance.service(s)) for s in ss),
                   source_delivery_windows_preserved=same_original_delivery if original_windows else None,
                   assigned_nearest_fraction=sum(o['assigned_nearest'] for o in family_orders)/len(raw),
                   min_use_redundant_fraction=sum(o['min_use_redundant_for_windows'] for o in family_orders)/len(raw),
                   pickup_tightens_delivery_fraction=sum(o['pickup_tightens_delivery_due'] for o in family_orders)/len(raw),
                   b3_forced_late_direct_orders=sum(o['b3_forced_late_direct'] for o in family_orders),
                   b3_cutoff_sensitive_direct_orders=sum(o['b3_cutoff_sensitive_direct'] for o in family_orders),
                   b3_necessary_conditions_passed=necessary['passed'], b3_pool_lower_bound=necessary['pool_lower_bound'],
                   median_assigned_travel_min=describe(o['assigned_travel_min'] for o in family_orders)['median'],
                   median_delivery_slack_min=describe(o['delivery_direct_slack_min'] for o in family_orders)['median'],
                   median_minimum_use_min=describe(o['earliest_use_min'] for o in family_orders)['median'])
        family_rows.append(row)
        for bid, path in family['cases'].items():
            bc, bf = load_routing_case(path, use_pool_config=False)
            problem = load_instance(bc, bf)
            _, result, issues = evaluate(problem, constructive_state(problem, provenance))
            a = audit_solution(problem, result)
            valid = not issues and a['valid']
            e1_work = a.get('time_metrics', {}).get('working_ticks', {}).get('e1', 0)
            e1_wait = a.get('time_metrics', {}).get('waiting_ticks', {}).get('e1', 0)
            e2_work = a.get('time_metrics', {}).get('working_ticks', {}).get('e2', 0)
            e2_wait = a.get('time_metrics', {}).get('waiting_ticks', {}).get('e2', 0)
            pool_min = a.get('minimum_pool_for_schedule')
            # Recheck the same schedule at its reported pool requirement. A
            # matching necessary bound then certifies inventory, not distance.
            necessary_case = check_necessary_conditions(problem, bounds)
            lower = necessary_case['pool_lower_bound']
            reduced = MultiBatchInstance(problem.network, problem.orders,
                                         replace(problem.operating_policy, container_pool_size=pool_min),
                                         model=problem.model_number)
            # Only the pool and its policy snapshot change; all explicit trip
            # times, routes and assignments remain the original witness.
            reduced_result = {**result, 'operating_policy': reduced.operating_policy.as_dict()}
            reduced_audit = audit_solution(reduced, reduced_result)
            certified = valid and reduced_audit['valid'] and lower == pool_min
            below = MultiBatchInstance(problem.network, problem.orders,
                                       replace(problem.operating_policy, container_pool_size=max(1, pool_min-1)),
                                       model=problem.model_number)
            below_check = check_necessary_conditions(below, bounds)
            pool_checks.append(dict(case_id=bc['case_name'], lower_bound=lower, witness_upper_bound=pool_min,
                                    witness_valid_at_upper_bound=reduced_audit['valid'],
                                    minimum_pool_certified=certified,
                                    lower_bound_details=necessary_case['stock_bounds'],
                                    errors_at_upper_bound=reduced_audit['errors'],
                                    one_less_pool=max(1, pool_min-1),
                                    one_less_rejected=any(e['code']=='POOL_BELOW_NECESSARY_BOUND' for e in below_check['errors']),
                                    proof_scope='Minimum pool under current fixed assignment/calendar/resources; no distance optimality.'))
            case_rows.append(dict(family_id=fid, case_id=bc['case_name'], source_kind=kind,
                                  customer_count=len(cs), batches=len(problem.operating_policy.batches),
                                  valid=valid, errors=json.dumps(issues+a['errors']),
                                  pool=problem.operating_policy.container_pool_size, total=problem.total,
                                  minimum_pool_for_witness=pool_min, witness_pool_ratio=pool_min/problem.total if pool_min else None,
                                  pool_lower_bound=lower, witness_valid_at_minimum_pool=reduced_audit['valid'],
                                  minimum_pool_certified=certified,
                                  objective=a.get('objective'), e1_distance=a.get('distances', {}).get('e1'),
                                  e2_distance=a.get('distances', {}).get('e2'),
                                  e1_wait_fraction=e1_wait/e1_work if e1_work else None,
                                  e2_wait_fraction=e2_wait/e2_work if e2_work else None))
            if fid in ('zhou_Set1_7-2-1','zhou_Set2_100-10-1_subset20','zhou_Set2_100-5-1b','lade_0823_30aoi'):
                plots[fid] = dict(nodes=nodes, orders=raw, policy=p)
                if bid in ('b1','b3'):
                    witnesses.append(dict(case_id=bc['case_name'], e1_trips=result['e1_trips'],
                                          e2_trips=result['e2_trips'], time_unit='ticks',
                                          time_tick_seconds=problem.operating_policy.time_tick_seconds,
                                          minimum_pool_for_schedule=pool_min, valid=valid))
    summaries = {}
    for kind in sorted({r['source_kind'] for r in family_rows}):
        fs = [r for r in family_rows if r['source_kind']==kind]
        os = [r for r in order_rows if r['source_kind']==kind]
        cs = [r for r in case_rows if r['source_kind']==kind]
        summaries[kind] = dict(
            families=len(fs), orders_counting_scenarios=len(os), unique_geometries=len({r['geometry_sha256'] for r in fs}),
            families_with_empty_satellites=sum(r['empty_satellites']>0 for r in fs),
            families_with_duplicate_coordinates=sum(r['duplicate_coordinate_groups']>0 for r in fs),
            assigned_nearest_fraction=sum(r['assigned_nearest'] for r in os)/len(os),
            minimum_use_redundant_fraction=sum(r['min_use_redundant_for_windows'] for r in os)/len(os),
            pickup_tightens_delivery_fraction=sum(r['pickup_tightens_delivery_due'] for r in os)/len(os),
            families_with_zero_cutoff_sensitive_orders=sum(r['b3_cutoff_sensitive_direct_orders']==0 for r in fs),
            orders_with_alternative_site_fraction=sum(r['relaxed_feasible_satellite_count']>1 for r in os)/len(os),
            pool_certificates_by_batch={str(b):sum(r['minimum_pool_certified'] for r in cs if r['batches']==b)
                                       for b in (1,2,3)},
            family_metrics={k:describe(r[k] for r in fs) for k in ('e2_vehicles_total','e2_vehicles_per_order',
                'e1_volume_supply_ratio','e2_total_volume_supply_ratio','cycle_hours','empty_satellites','pool_to_demand')},
            order_metrics={k:describe(r[k] for r in os) for k in ('assigned_travel_min','second_nearest_ratio',
                'delivery_direct_slack_min','delivery_wait_before_ready_min','earliest_use_min','latest_use_min',
                'delivery_width_min','pickup_width_min','e2_order_weight_utilization','e2_order_volume_utilization')},
            witness_by_batch={str(b):{k:describe(r[k] for r in cs if r['batches']==b) for k in
                ('witness_pool_ratio','e1_wait_fraction','e2_wait_fraction')} for b in (1,2,3)})
    by_customer_count = {}
    for n in (7,10,12,20,30,50,100):
        fs = [r for r in family_rows if r['source_kind']=='zhou_derived_scenario' and r['customer_count']==n]
        by_customer_count[str(n)] = dict(cases=len(fs), unique_geometries=len({r['geometry_sha256'] for r in fs}),
            empty_satellite_cases=sum(r['empty_satellites']>0 for r in fs),
            metrics={k:describe(r[k] for r in fs) for k in ('satellite_count','e2_vehicles_total','e2_vehicles_per_order',
                'empty_satellites','median_assigned_travel_min','median_delivery_slack_min','b3_pool_lower_bound')})
    pair_resource_changes = []
    for fam in manifest['families']:
        if fam.get('scenario_index')!=2:
            continue
        other = next(f for f in manifest['families'] if f['family_id']==fam['base_family_id'])
        cfg2, f2 = load_routing_case(fam['cases']['b1'], use_pool_config=False)
        cfg1, f1 = load_routing_case(other['cases']['b1'], use_pool_config=False)
        if f1 != f2:
            pair_resource_changes.append(dict(base_family_id=other['family_id'], scenario02=fam['family_id'],
                changed_keys=[k for k in f1.keys()|f2.keys() if f1.get(k)!=f2.get(k)]))
    result = dict(scope='Descriptive design audit, no optimization or operational validation',
                  manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(), families=paired,
                  cases=len(records), valid_constructive_witnesses=sum(r['valid'] for r in case_rows),
                  sources=summaries, zhou_by_customer_count=by_customer_count,
                  scenario_pairs_with_changed_resources=pair_resource_changes,
                  limitations=['Shortest-path feasible-site count is a relaxation, not a joint assignment certificate.',
                               'Cutoff sensitivity is for direct return after service; route detours can create additional late returns.',
                               'Witness waiting and pool values describe constructed schedules, not optimized solutions.',
                               'Family/order aggregates include correlated scenarios and repeated source geometries; no inferential statistics.'])
    save_csv(output/'families.csv', family_rows)
    save_csv(output/'orders.csv', order_rows)
    save_csv(output/'cases.csv', case_rows)
    save_json(output/'metrics.json', result)
    save_json(output/'selected_witnesses.json', witnesses)
    save_json(output/'pool_certificates.json', pool_checks)
    verify_artifacts(manifest, manifest_path.parent)
    save_json(output/'audit_sources.json', {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [Path(__file__), ROOT/'tools/data/build_static_batch_suite.py',
                  ROOT/'models/routing/common/cycle/instance.py', ROOT/'models/routing/common/cycle/policy.py',
                  ROOT/'models/routing/common/cycle/precheck.py', ROOT/'models/routing/common/cycle/audit.py',
                  ROOT/'models/routing/solvers/alns/evaluator.py', ROOT/'config/containers/pool_ratio.json']})
    make_figures(plots, family_rows, output)
    return result


def make_figures(plots, families, output):
    os.environ.setdefault('MPLCONFIGDIR', str(output/'matplotlib_cache'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 2, figsize=(13, 10), constrained_layout=True)
    for ax, (fid, data) in zip(axes.flat, plots.items()):
        rows = data['nodes']; xy = {r['node_id']:(float(r['x']),float(r['y'])) for r in rows}
        sats = [r['node_id'] for r in rows if r['node_type']=='s']
        colors = {s:plt.cm.tab10(i%10) for i,s in enumerate(sats)}
        for r in rows:
            x,y=xy[r['node_id']]
            if r['node_type']=='c':
                site=r['assigned_sat']; sx,sy=xy[site]
                ax.plot([x,sx],[y,sy],color=colors[site],alpha=.23,lw=.65)
                ax.scatter(x,y,color=colors[site],s=16,zorder=3)
            elif r['node_type']=='s':
                ax.scatter(x,y,c=[colors[r['node_id']]],marker='s',s=80,edgecolors='black',zorder=4)
                ax.annotate(r['node_id'],(x,y),xytext=(4,4),textcoords='offset points',fontsize=8)
            else:
                ax.scatter(x,y,c='black',marker='*',s=170,zorder=5)
                ax.annotate('Depot',(x,y),xytext=(4,4),textcoords='offset points',fontsize=8)
        ax.set_title(fid.replace('zhou_','').replace('_subset',' / n='),fontsize=11)
        unit='local km (road distances used by solver)' if fid.startswith('lade_') else 'benchmark coordinate units'
        ax.set_xlabel(unit); ax.set_ylabel(unit); ax.set_aspect('equal',adjustable='datalim'); ax.grid(alpha=.2)
    fig.suptitle('Spatial audit: stars = depot; squares = satellites; lines = fixed assignments',fontsize=13)
    fig.savefig(output/'spatial_audit.png',dpi=170); plt.close(fig)
    data=plots['zhou_Set1_7-2-1']; orders=sorted(data['orders'],key=lambda o:o['delivery_ready'])
    fig, ax=plt.subplots(figsize=(13,5),constrained_layout=True)
    for i,o in enumerate(orders):
        ax.broken_barh([(o['delivery_ready']/60,(o['delivery_due']-o['delivery_ready'])/60)],(i-.18,.32),facecolors='#2878B5')
        ax.broken_barh([(o['pickup_ready']/60,(o['pickup_due']-o['pickup_ready'])/60)],(i-.18,.32),facecolors='#D95319')
        ax.plot([o['delivery_due']/60,o['pickup_ready']/60],[i-.02,i-.02],color='#888888',linestyle=':',lw=1)
    for b in data['policy'].batches:
        t=data['policy'].minutes(b.cutoff)/60
        ax.axvline(t,c='#993399',linestyle='--',lw=1)
        ax.text(t+.1,.96,f'{b.id} cutoff',fontsize=8,rotation=90,
                va='top',transform=ax.get_xaxis_transform())
    start = data['policy'].minutes(data['policy'].cycle_start)/60
    end = data['policy'].minutes(data['policy'].cycle_end)/60
    ax.set_yticks(range(len(orders)),[o['customer_id'] for o in orders]); ax.set_xlim(start,end)
    ax.set_xticks(np.arange(start, end+.1, 1 if end-start <= 24 else 3))
    ax.set_xlabel(f'Input clock hours (cycle duration = {end-start:g} h)')
    ax.set_title('Same customer windows in B1/B2/B3: blue = delivery; orange = return pickup')
    ax.grid(axis='x',alpha=.2); fig.savefig(output/'window_audit.png',dpi=170); plt.close(fig)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',default='data/manifest.json')
    parser.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    result=audit_design(args.manifest,args.output)
    print(json.dumps({k:result[k] for k in ('families','cases','valid_constructive_witnesses')},ensure_ascii=False))


if __name__=='__main__':
    main()
