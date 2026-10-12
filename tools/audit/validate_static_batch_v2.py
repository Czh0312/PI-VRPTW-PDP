"""Bounded integration checks for every public model/ALNS dataset entry.

Zero-iteration initialization checks establish executable feasibility, not
algorithm quality. Optional smoke checks run finite search budgets without
resuming any formal or historical experiment.
"""
import argparse
from importlib import import_module
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.configuration.loader import load_routing_case
from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from tools.data.static_batch_dataset import load_manifest, check_pairing


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',default='data/manifest.json')
    parser.add_argument('--output',required=True)
    parser.add_argument('--smoke',action='store_true')
    args=parser.parse_args(argv)
    out=Path(args.output)
    if out.exists(): raise ValueError('Refusing to overwrite integration results')
    out.mkdir(parents=True)
    manifest, cases=load_manifest(args.manifest)
    check_pairing(cases)
    rows=[]
    def run(record, model, solver, budget, iterations, phase):
        cfg,fleet=load_routing_case(record['case_path'],use_pool_config=False)
        problem=load_instance({**cfg,'model':model},fleet)
        entry=import_module(f'models.routing.model_{model}.{solver}.solver')
        result=(entry.solve_alns(problem,max_time=budget,max_iterations=iterations,seed=42)
                if solver=='alns' else entry.solve_exact(problem,time_limit=budget,seed=42))
        audited=audit_solution(problem,result)
        row=dict(case_id=record['case_id'], model=model, solver=solver, phase=phase, budget=budget,
                 status=result['info']['status'],valid=audited['valid'],
                 proven_optimal=bool(result['info'].get('proven_optimal')) and audited['valid'],
                 objective=result['info'].get('objective') if audited['valid'] else None,
                 gap=result['info'].get('mip_gap'), runtime=result['info'].get('runtime'),
                 strategy=result['info'].get('initial_strategy'), errors=audited['errors'])
        rows.append(row)
        if phase=='smoke':
            path=out/f"{record['case_id']}_M{model}_{solver}.json"
            path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        return row
    for i,record in enumerate(cases):
        for model in record['comparison_models']:
            run(record,model,'alns',2,0,'initialization')
        if (i+1)%50==0: print(f'Initialization: {i+1}/{len(cases)} configurations',flush=True)
    (out/'initialization.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
    if args.smoke:
        selected=set()
        for record in cases:
            if record['source_kind']=='synthetic_mechanism': continue
            key=(record['source_kind'],record['customer_count'],record['batch_count'])
            if key in selected: continue
            selected.add(key)
            for model in record['comparison_models']:
                run(record,model,'alns',1,1000,'smoke')
            print(f"Search smoke: {record['case_id']}",flush=True)
        record=next(r for r in cases if r['source_kind']=='zhou_derived_scenario' and r['customer_count']==7 and r['batch_count']==1)
        for model in (1,2,3,4):
            row=run(record,model,'exact',5,0,'smoke')
            print(f"Exact M{model}: {row['status']}; valid={row['valid']}",flush=True)
    summary=dict(dataset_version=manifest['dataset_version'],cases=len(cases),
                 initialization_runs=sum(r['phase']=='initialization' for r in rows),
                 initialization_valid=sum(r['phase']=='initialization' and r['valid'] for r in rows),
                 smoke_runs=sum(r['phase']=='smoke' for r in rows),
                 smoke_valid=sum(r['phase']=='smoke' and r['valid'] for r in rows),
                 scope='Feasible initialization and bounded integration smoke; not a formal performance comparison.',rows=rows)
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='rows'},ensure_ascii=False),flush=True)
    if not all(r['valid'] for r in rows): raise SystemExit('Some initialization/smoke checks failed; inspect summary.json')


if __name__=='__main__': main()
