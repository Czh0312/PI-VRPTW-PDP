"""Frozen M1-M4 full-MILP comparison: optional static opposite-arc rows."""
from pathlib import Path
from copy import deepcopy
import argparse
import hashlib
import json
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def worker(path):
    job = json.loads(Path(path).read_text())
    sys.path.insert(0,job['snapshot'])
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.exact.solver import solve_exact
    config,fleet = load_routing_case(job['case_path'])
    p = load_instance(config,fleet)
    initial = json.loads(Path(job['start_path']).read_text())
    assert p.model_number==job['model'] and len(p.operating_policy.batches)==1
    assert audit_solution(p,initial)['valid']
    begun = time.perf_counter()
    result = solve_exact(p,time_limit=job['budget'],seed=job['seed'],initial_result=initial,
                         _single_batch_cycle_cuts=job['method']=='cycles')
    wall = time.perf_counter()-begun
    checked = audit_solution(p,result)
    assert checked['valid'] and checked['periodic_replay']['valid'],checked['errors']
    result['audit'] = checked
    write(Path(job['result_path']),result)
    info = result['info']
    assert info['e2_short_cycle_cuts']==(job['method']=='cycles')
    write(Path(job['row_path']),dict(case=job['case'],model=job['model'],method=job['method'],seed=job['seed'],
        budget=job['budget'],wall_time=wall,status=info['status'],objective=info['objective'],
        bound=info['best_bound'],gap=info['mip_gap'],proven_optimal=info['proven_optimal'],
        proven_infeasible=info['proven_infeasible'],audited=True,replay=True,
        work=info['mip_work'],mip_runtime=info['mip_runtime'],build_time=info['build_time'],
        nodes=info['mip_node_count'],variables=info['variables'],constraints=info['constraints'],
        nonzeros=info['linear_nonzeros'],general_constraints=info['general_constraints'],
        cycle_rows=info['e2_short_cycle_rows'],capacity_candidates=info['capacity_cut_candidates'],
        capacity_cuts=info['capacity_cuts_added'],start_supplied=info['mip_start_supplied'],
        original_task_count=len(p.orders)*len(p.task_kinds),
        pickup_events=sum(e['kind']=='p' for t in result['e2_trips'] for e in t['events']),
        formulation_features=info['formulation_features']))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker')
    parser.add_argument('--customers',type=int,default=20)
    parser.add_argument('--budget',type=float,default=20)
    parser.add_argument('--models',type=int,nargs='+',default=[1,2,3,4])
    parser.add_argument('--output',default='full20')
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker)
    sys.path.insert(0,str(ROOT))
    from configuration.loader import load_routing_case,resolve_project_path
    from experiments.runs.run_multibatch_cycle import INPUT_KEYS,freeze_inputs,source_snapshot
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    out = OUT/args.output
    assert not out.exists(),'Never overwrite or retry a batch'
    out.mkdir()
    variants,starts = [],[]
    for model in args.models:
        origin = ROOT/f'output/experiments/single_batch_site_flow_20261001/final/zhou_Set2_100-5-1b_subset{args.customers}_M{model}'
        paths = list((origin/'inputs').glob('00_*.json'))
        assert len(paths)==1
        config,fleet = load_routing_case(paths[0])
        p = load_instance(config,fleet)
        initial = json.loads((origin/'result.json').read_text())
        assert audit_solution(p,initial)['valid'] and p.model_number==model
        sources = {resolve_project_path(config[k]).resolve() for k in INPUT_KEYS if isinstance(config.get(k),str)}
        variants.append(dict(config=deepcopy(config),case=origin.name,model=model,
            _source_hashes={str(path):sha(path) for path in sources}))
        starts.append(initial)
    inputs = freeze_inputs(variants,out)
    sources = source_snapshot(out)
    jobs,rows = [],[]
    for variant,initial in zip(variants,starts):
        folder = out/variant['case']
        folder.mkdir()
        case,start = folder/'case.json',folder/'start.json'
        write(case,variant['config'])
        write(start,initial)
        for seed in (42,43):
            for method in (('baseline','cycles') if seed==42 else ('cycles','baseline')):
                run = folder/f'{method}_{seed}'
                run.mkdir()
                job = dict(case=variant['case'],model=variant['model'],method=method,seed=seed,
                    budget=args.budget,snapshot=str(out/'source_snapshot'),case_path=str(case),start_path=str(start),
                    result_path=str(run/'result.json'),row_path=str(run/'row.json'))
                path = run/'job.json'
                write(path,job)
                jobs.append((path,job))
    write(out/'manifest.json',dict(arguments=vars(args),input_sha256=inputs,source_sha256=sources,
        script_sha256=sha(Path(__file__)),job_count=len(jobs),threads=1,
        seeds=[42,43],methods=['baseline','cycles'],
        execution='sequential isolated subprocesses; reverse order seed 43; no retries',
        shared_start='same frozen independently audited schedule for both methods and seeds per case',
        baseline='original complete MILP; same supplied incumbent; unchanged capacity callback and default formulation',
        budget_scope='one wall-clock solve budget including precheck, primal preparation, build, MIP and internal audits; no CG/pool or cached proof')))
    for index,(path,job) in enumerate(jobs,1):
        proc = subprocess.run([sys.executable,str(Path(__file__)),'--worker',str(path)],
            cwd=out/'source_snapshot',capture_output=True,text=True,timeout=args.budget+30)
        path.with_name('solver.log').write_text(proc.stdout+'\n'+proc.stderr)
        if proc.returncode:
            write(out/'failure.json',dict(job=job,exit_code=proc.returncode,stderr=proc.stderr))
            raise RuntimeError('Worker failed; artifacts retained, no retries')
        row = json.loads(Path(job['row_path']).read_text())
        rows.append(row)
        write(out/'comparison.json',rows)
        print(f'{index}/{len(jobs)} M{job["model"]} {job["method"]} seed={job["seed"]}: '
              f'{row["status"]} obj={row["objective"]} bound={row["bound"]} gap={row["gap"]} '
              f'rows={row["cycle_rows"]}',flush=True)


if __name__=='__main__':
    main()
