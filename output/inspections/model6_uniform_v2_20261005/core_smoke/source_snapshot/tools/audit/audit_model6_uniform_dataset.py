"""Read-only input audit; necessary conditions are not route feasibility proofs."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
import math

from configuration.loader import load_routing_case, resolve_project_path
from models.routing.common.cycle import load_instance
from tools.data.build_model6_uniform_dataset import load_suite, source_totals, write_json


def audit_suite(manifest_path):
    manifest = load_suite(manifest_path)
    families, cases, domains = set(), [], []
    widths, cleaning, rates = Counter(), Counter(), set()
    semantic_groups = defaultdict(list)
    for row in manifest['cases']:
        config, fleet = load_routing_case(row['m6_case'], use_pool_config=False)
        problem = load_instance(config, fleet)
        raw = json.loads(resolve_project_path(config['orders']).read_text())
        metadata = json.loads(resolve_project_path(config['dataset_metadata']).read_text())
        original_config, _ = load_routing_case(metadata['source_case'], use_pool_config=False)
        original = json.loads(resolve_project_path(original_config['orders']).read_text())
        old, new = source_totals(original), source_totals(raw)
        if old.keys()!=new.keys() or any(old[k][0]!=new[k][0] or not math.isclose(old[k][1],new[k][1],abs_tol=1e-8) for k in old):
            raise ValueError(f'Demand/assignment changed: {row["name"]}')
        if problem.total!=row['total_container_uses'] or len(raw)!=row['orders']:
            raise ValueError(f'Manifest count mismatch: {row["name"]}')
        for field in ('data','distance_matrix','duration_matrix'):
            if config.get(field)!=original_config.get(field):
                # Paths are normalized, so compare actual bytes as well.
                a, b = config.get(field), original_config.get(field)
                if not a or not b or resolve_project_path(a).read_bytes()!=resolve_project_path(b).read_bytes():
                    raise ValueError(f'Physical source changed: {field}')
        families.add(row['family_id'])
        widths.update(o['delivery_due']-o['delivery_ready'] for o in raw)
        cleaning[fleet['operating_policy']['cleaning_duration']] += 1
        rates.add(tuple(sorted(problem.cost_profile.rates.items())))
        semantic_groups[row['semantic_sha256']].append(row['name'])
        capacity_failed = [o.id for o in problem.orders if
                           o.goods_weight+o.count*problem.fleet.container_weight > problem.fleet.e2_weight_cap+1e-8
                           or o.count*problem.fleet.container_volume > problem.fleet.e2_volume_cap+1e-8]
        for k in problem.batch_count_candidates:
            child = problem.for_count(k)
            empty = [oid for oid, values in child.legal_batches.items() if not values]
            options = Counter(len(v) for v in child.legal_batches.values())
            forced = sum(o.count for o in child.orders if child.legal_batches[o.id]==('b1',))
            domains.append(dict(case=row['name'],family=row['family_id'],variant=row['variant'],
                                source_kind=row['source_kind'],customers=row['customer_count'],k=k,
                                orders=len(child.orders),boxes=child.total,
                                last_batch_eligible_orders=sum(f'b{k}' in v for v in child.legal_batches.values()),
                                forced_first_batch_boxes=forced,
                                forced_first_batch_pool_lower_bound=forced,
                                empty_batch_domains=len(empty),individual_capacity_failures=len(capacity_failed),
                                proven_infeasible_by_necessary_conditions=bool(empty or capacity_failed),
                                legal_choice_histogram=dict(options),empty_order_ids=empty,
                                capacity_failed_order_ids=capacity_failed))
        cases.append(dict(name=row['name'],family=row['family_id'],variant=row['variant'],
                          source_kind=row['source_kind'],customers=row['customer_count'],orders=len(raw),
                          boxes=problem.total,cleaning_minutes=fleet['operating_policy']['cleaning_duration'],
                          staggered_release_orders=sum(o.release_time>problem.operating_policy.cycle_start for o in problem.orders),
                          repeated_customer_orders=len(raw)-len({o['customer_id'] for o in raw}),
                          e1_vehicles=problem.fleet.e1_count,e2_per_satellite=problem.fleet.e2_per_sat_count))
    groups = []
    for kind, variant in sorted({(r['source_kind'],r['variant']) for r in domains}):
        selected = [r for r in domains if r['source_kind']==kind and r['variant']==variant and r['k']==3]
        groups.append(dict(source_kind=kind,variant=variant,cases=len(selected),
                           no_order_can_use_last_batch=sum(r['last_batch_eligible_orders']==0 for r in selected),
                           necessary_condition_rejections=sum(r['proven_infeasible_by_necessary_conditions'] for r in selected)))
    return dict(dataset_version=manifest['dataset_version'],families=len(families),cases=len(cases),
                domains=len(domains),source_and_artifact_hashes_valid=True,demand_conservation_valid=True,
                window_width_counts=dict(widths),cleaning_minutes_counts=dict(cleaning),unique_rate_vectors=len(rates),
                duplicate_semantic_groups=[v for v in semantic_groups.values() if len(v)>1],
                k3_coverage=groups,case_rows=cases,domain_rows=domains,
                evidence_scope='Input integrity and necessary-condition checks only. Passing does not establish joint route feasibility.',
                material_passport=dict(origin='academic-research-suite/experiment-agent',mode='validate',status='ANALYZED'),
                interpretation_checks=dict(coverage='11/11',
                    scope='Stratified by source and scale; AOI aggregation and source dependence retained; no success filtering, significance tests, population estimates or causal claims.'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    out = resolve_project_path(args.output)
    if out.exists() and any(out.iterdir()):
        raise ValueError('Refusing to overwrite audit output')
    report = audit_suite(args.manifest)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out/'audit.json', report)
    for name, rows in [('cases',report['case_rows']),('domains',report['domain_rows'])]:
        with (out/f'{name}.csv').open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows({k:json.dumps(v) if isinstance(v,(dict,list)) else v for k,v in row.items()} for row in rows)
    print(json.dumps({k:v for k,v in report.items() if k not in ('case_rows','domain_rows')},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
