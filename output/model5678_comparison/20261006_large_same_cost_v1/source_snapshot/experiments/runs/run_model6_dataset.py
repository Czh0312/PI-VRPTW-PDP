"""Select and freeze uniform-v2 cases; run equal-cost controls with honest failures."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import shutil

from configuration.loader import load_routing_case, resolve_project_path
from tools.data.build_model6_uniform_dataset import ARMS, load_suite, treatment_config
from .run_model6_comparison import execute_jobs, freeze_case, snapshot_source, write_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', default='data/model6/uniform_v2_20261005/manifest.json')
    parser.add_argument('--output', required=True)
    parser.add_argument('--families', nargs='+')
    parser.add_argument('--variants', nargs='+', default=['spread_medium'])
    parser.add_argument('--sizes', nargs='+', choices=['small','medium','large'])
    parser.add_argument('--anchors-only', action='store_true')
    parser.add_argument('--arms', nargs='+', choices=ARMS, default=['joint'])
    parser.add_argument('--solvers', nargs='+', choices=['alns','exact'], default=['alns'])
    parser.add_argument('--seeds', nargs='+', type=int, default=[42,43,44])
    parser.add_argument('--time', type=float, default=10)
    parser.add_argument('--exact-time', type=float, default=30)
    parser.add_argument('--iterations', type=int, default=5000)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--timeout-overhead', type=float, default=20)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if (any(not math.isfinite(v) or v<=0 for v in (args.time,args.exact_time,args.timeout_overhead))
            or args.iterations<0 or not 1<=args.workers<=8 or any(not 0<=s<=2_000_000_000 for s in args.seeds)):
        parser.error('Invalid budgets, worker count or seed')
    manifest = load_suite(args.manifest)
    records = [r for r in manifest['cases'] if
               (not args.families or r['family_id'] in args.families) and r['variant'] in args.variants
               and (not args.sizes or r['size'] in args.sizes) and (not args.anchors_only or r['sensitivity_anchor'])]
    if not records:
        raise ValueError('No cases selected')
    jobs, configs = [], {}
    for index, row in enumerate(records):
        config, _ = load_routing_case(row['m6_case'], use_pool_config=False)
        for arm in dict.fromkeys(args.arms):
            key = f'{index:03d}_{arm}'
            configs[key] = treatment_config({**config,'_source_case':row['m6_case']},arm)
            methods = ([('exact',args.seeds[0],args.exact_time)] if 'exact' in args.solvers else [])
            if 'alns' in args.solvers:
                methods += [('alns',seed,args.time) for seed in dict.fromkeys(args.seeds)]
            for method, seed, budget in methods:
                jobs.append(dict(case=row['name'],family_id=row['family_id'],variant=row['variant'],
                                 source_kind=row['source_kind'],customer_count=row['customer_count'],
                                 spatial_block=row['spatial_block'],treatment=arm,solver=method,seed=seed,budget=budget,
                                 run=f'{key}_{method}_{seed}',config_key=key))
    plan = dict(case_count=len(records),job_count=len(jobs),jobs=jobs,budgets=vars(args),
                semantic_dataset_sha256=manifest['semantic_dataset_sha256'])
    if args.dry_run:
        print(json.dumps(plan,ensure_ascii=False,indent=2))
        return plan
    out = resolve_project_path(args.output).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('Refusing to overwrite study output')
    for folder in ('inputs','cases','logs'):
        (out/folder).mkdir(parents=True,exist_ok=True)
    shutil.copy2(resolve_project_path(args.manifest),out/'inputs/manifest.json')
    hashes = {}
    for key, config in configs.items():
        source = out/'cases'/f'{key}_source.json'
        write_json(source,config)
        frozen,evidence = freeze_case(source,out,key)
        hashes.update(evidence)
        frozen.update(alns_iter=args.iterations,save_plots=False)
        write_json(out/'cases'/f'{key}.json',frozen)
    for job in jobs:
        job['case_path'] = str(out/'cases'/f'{job["config_key"]}.json')
    plan.update(input_hashes=hashes,source_hashes=snapshot_source(out))
    write_json(out/'experiment.json',plan)
    rows = execute_jobs(jobs,out,args)
    for row in rows:
        path = out/row['run']/'result.json'
        result = json.loads(path.read_text()) if path.is_file() else {}
        row.update(proven_infeasible=bool(result.get('info',{}).get('proven_infeasible')),
                   cycle_batch_count=result.get('cycle_batch_count'),
                   shared_cutoff_ratio=result.get('shared_cutoff_ratio'))
    write_json(out/'rows.json',rows)
    summary = dict(planned_runs=len(jobs),completed_rows=len(rows),valid_runs=sum(r['valid'] for r in rows),
                   statuses=dict(Counter(r['status'] for r in rows)),
                   proven_infeasible_runs=sum(r['proven_infeasible'] for r in rows),
                   source_manifest=str(resolve_project_path(args.manifest)),
                   limitations=['Finite-budget smoke results are not convergence or general performance evidence',
                                'Synthetic cost units; no enterprise savings claim',
                                'Necessary-condition rejection, proven infeasibility and heuristic no-solution are distinct',
                                'Same physical inputs and cost objective within each case; fixed controls have nested decision domains',
                                'Seeds and paired variants are not independent spatial samples'])
    write_json(out/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return summary


if __name__ == '__main__':
    main()
