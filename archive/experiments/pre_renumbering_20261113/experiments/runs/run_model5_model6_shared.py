"""Run M5 distance, M6 joint cost, and a same-fixed-policy cost control in pairs."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import shutil

from tools.configuration.loader import resolve_project_path
from experiments.analysis.summarize_model6_costs import summarize as summarize_costs
from tools.data.build_model5_model6_shared_dataset import load_shared_suite, verify_pair
from .run_model6_comparison import execute_jobs, freeze_case, snapshot_source, write_json

TREATMENTS={'M5_distance':'m5_case','M6_uniform':'m6_case','M6_fixed_policy_cost':'m6_fixed_policy_case'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',default='data/model5_model6/shared_v1_20261005/manifest.json')
    parser.add_argument('--output',required=True)
    parser.add_argument('--families',nargs='+')
    parser.add_argument('--variants',nargs='+',default=['spread_medium'])
    parser.add_argument('--sizes',nargs='+',choices=['small','medium','large'])
    parser.add_argument('--anchors-only',action='store_true')
    parser.add_argument('--treatments',nargs='+',choices=TREATMENTS,default=list(TREATMENTS))
    parser.add_argument('--solvers',nargs='+',choices=['alns','exact'],default=['alns'])
    parser.add_argument('--seeds',nargs='+',type=int,default=[42])
    parser.add_argument('--time',type=float,default=10)
    parser.add_argument('--exact-time',type=float,default=30)
    parser.add_argument('--iterations',type=int,default=5000)
    parser.add_argument('--workers',type=int,default=1)
    parser.add_argument('--timeout-overhead',type=float,default=20)
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args(argv)
    if (any(not math.isfinite(v) or v<=0 for v in (args.time,args.exact_time,args.timeout_overhead))
            or args.iterations<0 or not 1<=args.workers<=8 or any(not 0<=s<=2_000_000_000 for s in args.seeds)):
        parser.error('Invalid budgets, worker count or seeds')
    manifest=load_shared_suite(args.manifest)
    records=[r for r in manifest['cases'] if (not args.families or r['family_id'] in args.families)
             and r['variant'] in args.variants and (not args.sizes or r['size'] in args.sizes)
             and (not args.anchors_only or r['sensitivity_anchor'])]
    if not records:
        raise ValueError('No paired cases selected')
    checks=[verify_pair(r) for r in records]
    jobs=[]
    for index,record in enumerate(records):
        for treatment in dict.fromkeys(args.treatments):
            methods=([('exact',args.seeds[0],args.exact_time)] if 'exact' in args.solvers else [])
            if 'alns' in args.solvers:
                methods += [('alns',s,args.time) for s in dict.fromkeys(args.seeds)]
            for solver,seed,budget in methods:
                key=f'{index:03d}_{treatment}'
                jobs.append(dict(case=record['name'],family_id=record['family_id'],variant=record['variant'],
                                 source_kind=record['source_kind'],customer_count=record['customer_count'],
                                 treatment=treatment,solver=solver,seed=seed,budget=budget,
                                 config_key=key,run=f'{key}_{solver}_{seed}'))
    plan=dict(case_count=len(records),job_count=len(jobs),jobs=jobs,budgets=vars(args),
              semantic_dataset_sha256=manifest['semantic_dataset_sha256'],pair_checks=checks)
    if args.dry_run:
        print(json.dumps({k:v for k,v in plan.items() if k!='pair_checks'},ensure_ascii=False,indent=2))
        return plan
    out=resolve_project_path(args.output).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('Refusing to overwrite paired study output')
    for folder in ('inputs','cases','logs'):
        (out/folder).mkdir(parents=True,exist_ok=True)
    shutil.copy2(resolve_project_path(args.manifest),out/'inputs/manifest.json')
    hashes={}
    for index,record in enumerate(records):
        for treatment in dict.fromkeys(args.treatments):
            key=f'{index:03d}_{treatment}'
            config,evidence=freeze_case(record[TREATMENTS[treatment]],out,key)
            hashes.update(evidence)
            config.update(alns_iter=args.iterations,save_plots=False)
            write_json(out/'cases'/f'{key}.json',config)
        # Verify the actual frozen files, not just source-manifest promises.
        frozen_record={**record,**{field:str(out/'cases'/f'{index:03d}_{treatment}.json')
                                   for treatment,field in TREATMENTS.items() if treatment in args.treatments}}
        verify_pair(frozen_record)
    for job in jobs:
        job['case_path']=str(out/'cases'/f'{job["config_key"]}.json')
    plan.update(input_hashes=hashes,source_hashes=snapshot_source(out))
    write_json(out/'experiment.json',plan)
    rows=execute_jobs(jobs,out,args)
    for row in rows:
        path=out/row['run']/'result.json'
        result=json.loads(path.read_text()) if path.exists() else {}
        row.update(proven_infeasible=bool(result.get('info',{}).get('proven_infeasible')),
                   proof_objective='distance' if row['treatment']=='M5_distance' else 'cost',
                   cycle_batch_count=result.get('cycle_batch_count'))
    write_json(out/'rows.json',rows)
    priced=summarize_costs(out,rows,records)
    control_pairs=[]
    for case,solver,seed in sorted({(r['case'],r['solver'],r['seed']) for r in rows}):
        group={r['treatment']:r for r in rows if (r['case'],r['solver'],r['seed'])==(case,solver,seed)}
        base=group.get('M5_distance');control=group.get('M6_fixed_policy_cost');joint=group.get('M6_uniform')
        valid_cost=lambda r:r['cost'] if r and r['valid'] else None
        a,b,c=map(valid_cost,(base,control,joint))
        control_pairs.append(dict(case=case,solver=solver,seed=seed,m5_postpriced_cost=a,
                                  fixed_policy_optimized_cost=b,joint_cost=c,
                                  objective_change_observed_savings=a-b if a is not None and b is not None else None,
                                  joint_decision_observed_savings=b-c if b is not None and c is not None else None))
    write_json(out/'control_pairs.json',control_pairs)
    summary=dict(planned_runs=len(jobs),completed_rows=len(rows),valid_runs=sum(r['valid'] for r in rows),
                 statuses=dict(Counter(r['status'] for r in rows)),
                 proven_infeasible_runs=sum(r['proven_infeasible'] for r in rows),
                 paired_cost_comparisons=len(priced['paired_runs']),
                 limitations=manifest['limitations']+['Short-budget engineering verification; no convergence or population-effect claim'])
    write_json(out/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return summary


if __name__=='__main__':
    main()
