"""Describe benchmark structure without optimizing or selecting instances."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path

from tools.configuration.loader import load_routing_case, resolve_project_path
from models.common.cycle import load_instance
from models.common.cycle.precheck import check_necessary_conditions, travel_lower_bounds


def describe(dataset, output):
    root, out = resolve_project_path(dataset), resolve_project_path(output)
    manifest = json.loads((root/'manifest.json').read_text())
    rows, order_rows, networks = [], [], {}
    for base in manifest['base_cases']:
        cfg, fleet = load_routing_case(base['models']['5'])
        problem = load_instance(cfg, fleet)
        p, f = problem.operating_policy, problem.fleet
        bounds = travel_lower_bounds(problem)
        checks = check_necessary_conditions(problem, bounds)
        nodes = list(csv.DictReader(resolve_project_path(cfg['data']).open()))
        geometry = sorted((n['node_id'], n['node_type'], float(n['x']), float(n['y'])) for n in nodes)
        networks[base['id']] = {n[0]: n[2:] for n in geometry if n[1] == 'c'}
        counts = Counter(o.satellite for o in problem.orders)
        for o in problem.orders:
            b = problem.batch_map[o.batch]
            arrival = (b.start+p.e1_load_time+problem.travel(problem.depot, o.satellite)
                       +problem.service(o.satellite)+p.e2_dispatch_prep_time
                       +problem.travel(o.satellite, o.customer))
            delivery = max(arrival, o.delivery_window[0])
            pickup = max(o.pickup_window[0], delivery+o.service_time+o.min_use_time)
            unload = pickup+o.service_time+problem.travel(o.customer, o.satellite)+p.e2_return_unload_time
            last_unload = o.pickup_window[1]+o.service_time+problem.travel(o.customer, o.satellite)+p.e2_return_unload_time
            sites = 0
            for s in problem.satellites:
                a = (b.start+p.e1_load_time+bounds['outward'][problem.depot][s]
                     +problem.service(s)+p.e2_dispatch_prep_time+bounds['outward'][s][o.customer])
                d = max(o.delivery_window[0], a)
                v = max(o.pickup_window[0], d+o.service_time+o.min_use_time)
                u = v+o.service_time+bounds['inward'][s][o.customer]+p.e2_return_unload_time
                sites += int(d<=o.delivery_window[1] and v<=o.pickup_window[1] and u<=p.batches[-1].cutoff)
            assigned = problem.distance(o.satellite, o.customer)
            order_rows.append(dict(base_case_id=base['id'], order_id=o.id,
                assigned_nearest=assigned<=min(problem.distance(s,o.customer) for s in problem.satellites)+1e-8,
                relaxed_feasible_sites=sites,
                delivery_width_minutes=p.minutes(o.delivery_window[1]-o.delivery_window[0]),
                pickup_width_minutes=p.minutes(o.pickup_window[1]-o.pickup_window[0]),
                direct_delivery_slack_minutes=p.minutes(o.delivery_window[1]-delivery),
                min_use_redundant=o.pickup_window[0]>=o.delivery_window[1]+o.service_time+o.min_use_time,
                pickup_tightens_delivery=o.pickup_window[1]-o.service_time-o.min_use_time<o.delivery_window[1],
                cutoff_sensitive=unload<=b.cutoff<last_unload,
                forced_later_recovery=unload>b.cutoff,
                release_after_cycle_start=o.release_time is not None and o.release_time>p.cycle_start))
        current = [r for r in order_rows if r['base_case_id']==base['id']]
        rows.append(dict(base_case_id=base['id'], size=base['size'], source_block=base['source_block'],
            customers=base['customer_count'], Q=problem.total, satellites=len(problem.satellites),
            geometry_sha256=hashlib.sha256(json.dumps(geometry).encode()).hexdigest(),
            empty_satellites=sum(counts[s]==0 for s in problem.satellites),
            e1_vehicles=f.e1_count, e2_vehicles_per_satellite=f.e2_per_sat_count,
            e1_total_demand_over_single_vehicle_volume=problem.total*f.container_volume/f.e1_volume_cap,
            e1_total_goods_over_single_vehicle_weight=sum(o.goods_weight+o.count*f.container_weight for o in problem.orders)/f.e1_weight_cap,
            e2_demand_over_fleet_single_load=problem.total*f.container_volume/(len(problem.satellites)*f.e2_per_sat_count*f.e2_volume_cap),
            fixed_batch_demands=dict(problem.batch_demands),
            fixed_calendar_necessary_pool_lower_bound=checks['pool_lower_bound'],
            necessary_conditions_passed=checks['passed'],
            assigned_nearest_fraction=sum(r['assigned_nearest'] for r in current)/len(current),
            alternative_site_order_count=sum(r['relaxed_feasible_sites']>1 for r in current),
            min_use_redundant_order_count=sum(r['min_use_redundant'] for r in current),
            cutoff_sensitive_order_count=sum(r['cutoff_sensitive'] for r in current),
            forced_later_recovery_order_count=sum(r['forced_later_recovery'] for r in current),
            late_release_order_count=sum(r['release_after_cycle_start'] for r in current)))
    overlap = []
    for i, a in enumerate(rows):
        for b in rows[i+1:]:
            if a['source_block'] != b['source_block']:
                continue
            aa, bb = networks[a['base_case_id']], networks[b['base_case_id']]
            shared = [k for k in aa.keys() & bb.keys() if aa[k]==bb[k]]
            overlap.append(dict(a=a['base_case_id'], b=b['base_case_id'], source_block=a['source_block'],
                                shared_customer_coordinates=len(shared),
                                fraction_of_smaller=len(shared)/min(len(aa),len(bb))))
    result = dict(base_cases=len(rows), source_blocks=len({r['source_block'] for r in rows}),
        unique_networks=len({r['geometry_sha256'] for r in rows}),
        physical_order_records=sum(r['customers'] for r in rows),
        source_block_sizes=dict(Counter(r['source_block'] for r in rows)),
        descriptive_only=True, fixed_calendar_bounds_are_not_joint_optima=True,
        rows=rows, same_source_overlap=overlap)
    out.mkdir(parents=True, exist_ok=True)
    (out/'structure.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    for filename, records in [('case_metrics.csv', rows), ('order_metrics.csv', order_rows), ('source_overlap.csv',overlap)]:
        with (out/filename).open('w',newline='',encoding='utf-8-sig') as handle:
            w=csv.DictWriter(handle, fieldnames=list(records[0]));w.writeheader();w.writerows(records)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', default='data/formal_benchmark27_uniform_7_20_50_v1')
    parser.add_argument('--output', default='output/inspections/formal_benchmark27_review_20261015/structure')
    args=parser.parse_args()
    result=describe(args.dataset,args.output)
    print(json.dumps({k:v for k,v in result.items() if k not in ('rows','same_source_overlap')}))
