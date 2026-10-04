"""Frozen sequential matched-budget original/bounded label hybrid trials."""
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timezone
import argparse
import hashlib
import json
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
NIGHT = OUT.parent
OTHERS = ('alns_single_batch_overnight_20261001','m5_overnight_20261001','model5_alns_overnight_20261001')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def load_state():
    return {name:json.loads((ROOT/f'output/experiments/{name}/checkpoint.json').read_text()) for name in OTHERS}


def worker(path):
    job = json.loads(Path(path).read_text())
    sys.path.insert(0,job['snapshot'])
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.exact.single_batch_columns import solve_with_route_bound
    cfg,fleet = load_routing_case(job['case_path'])
    problem = load_instance(cfg,fleet)
    initial = json.loads(Path(job['start_path']).read_text())
    assert audit_solution(problem,initial)['valid']
    begun = time.perf_counter()
    result = solve_with_route_bound(problem,time_limit=job['budget'],root_time=job['root_time'],
        pool_time=job['pool_time'],pool_compact=True,initial_result=initial,seed=job['seed'],
        pricing_method='label',dual_cuts=True,label_bound_pruning=job['method']=='bounded')
    wall = time.perf_counter()-begun
    audit = audit_solution(problem,result)
    assert audit['valid'] and audit['periodic_replay']['valid'],audit['errors']
    result['audit'] = audit
    write(Path(job['result_path']),result)
    info = result['info']
    cert = info['route_bound_certificate']
    assert not cert['proven_optimal'] and cert['label_bound_pruning']==(job['method']=='bounded')
    assert not info.get('route_pool_primal',{}).get('global_proof',False)
    priced = [r for h in cert['history'] for r in h.get('pricing',{}).values()]
    write(Path(job['row_path']),dict(case=job['case'],customers=job['customers'],model=2,
        method=job['method'],seed=job['seed'],budget=job['budget'],wall_time=wall,
        objective=info['objective'],bound=info['best_bound'],gap=info['mip_gap'],status=info['status'],
        proven_optimal=info['proven_optimal'],proven_infeasible=info['proven_infeasible'],audited=True,replay=True,
        original_work=info.get('mip_work'),original_mip_runtime=info.get('mip_runtime'),
        root_runtime=cert['runtime'],root_status=cert['status'],root_complete=cert['relaxation_complete'],
        root_bound=cert['bound'],iterations=cert['iterations'],columns=cert['columns'],
        pricing_runtime=cert['pricing_runtime'],pricing_calls=len(priced),
        generated_labels=sum(r['generated_labels'] for r in priced),
        expanded_labels=sum(r['expanded_labels'] for r in priced),
        bound_closures=sum(r.get('bound_closed',False) for r in priced),
        pool=info.get('route_pool_primal')))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker')
    parser.add_argument('--budget',type=float,default=60)
    parser.add_argument('--root-time',type=float,default=30)
    parser.add_argument('--customers',nargs='+',type=int,default=[20,30])
    parser.add_argument('--output',default='full60')
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker)
    sys.path.insert(0,str(ROOT))
    from configuration.loader import load_routing_case,resolve_project_path
    from experiments.runs.run_multibatch_cycle import INPUT_KEYS,freeze_inputs,source_snapshot
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    out = OUT/args.output
    out.mkdir()
    variants,starts = [],[]
    for size in args.customers:
        origin = ROOT/f'output/experiments/single_batch_site_flow_20261001/final/zhou_Set2_100-5-1b_subset{size}_M2'
        cfg,fleet = load_routing_case(next((origin/'inputs').glob('00_*.json')))
        problem = load_instance(cfg,fleet)
        initial = json.loads((origin/'result.json').read_text())
        assert audit_solution(problem,initial)['valid']
        sources = {resolve_project_path(cfg[k]).resolve() for k in INPUT_KEYS if isinstance(cfg.get(k),str)}
        variants.append(dict(config=deepcopy(cfg),case=origin.name,customers=size,
            _source_hashes={str(p):sha(p) for p in sources}))
        starts.append(initial)
    inputs = freeze_inputs(variants,out)
    source = source_snapshot(out)
    jobs,rows = [],[]
    for variant,start in zip(variants,starts):
        folder = out/variant['case'];folder.mkdir()
        case_path,start_path = folder/'case.json',folder/'start.json'
        write(case_path,variant['config']);write(start_path,start)
        for seed in (42,43):
            for method in (('baseline','bounded') if seed==42 else ('bounded','baseline')):
                run = folder/f'{method}_{seed}';run.mkdir()
                job = dict(case=variant['case'],customers=variant['customers'],method=method,seed=seed,
                    budget=args.budget,root_time=args.root_time,pool_time=5,
                    snapshot=str(out/'source_snapshot'),case_path=str(case_path),start_path=str(start_path),
                    result_path=str(run/'result.json'),row_path=str(run/'row.json'))
                path = run/'job.json';write(path,job);jobs.append((path,job))
    write(out/'manifest.json',dict(arguments=vars(args),input_sha256=inputs,source_sha256=source,
        script_sha256=sha(Path(__file__)),job_count=len(jobs),threads=1,seeds=[42,43],methods=['baseline','bounded'],
        execution='sequential isolated subprocesses; reverse seed 43; same audited frozen start; no retries',
        scope='M2 original CG/primal/full-kernel hybrid, only optional label bound pruning differs',
        budget_scope='shared total wall budget includes original preparation, CG root, compact integer pool, full MILP and internal audit',
        other_night_tasks=load_state(),external_load='Other chats are not controlled. Per-run checkpoints recorded; treat overlaps as exploratory, not clean CPU or default-adoption evidence.'))
    for index,(path,job) in enumerate(jobs,1):
        begun = datetime.now(timezone.utc).isoformat()
        state = load_state()
        checkpoint = json.loads((NIGHT/'checkpoint.json').read_text())
        checkpoint.update(status='RUNNING_LABEL_BOUND_PAIRED_TRIALS',
            active_process=dict(conductor_pid=__import__('os').getpid(),job=job,job_index=index,total=len(jobs),started_utc=begun),
            next_action='Inspect current conductor/tool session and comparison.json; never relaunch an active batch. After all workers finish independently audit records and run necessary regression.')
        write(NIGHT/'checkpoint.json',checkpoint)
        proc = subprocess.run([sys.executable,str(Path(__file__)),'--worker',str(path)],
            cwd=out/'source_snapshot',capture_output=True,text=True,timeout=job['budget']+30)
        path.with_name('solver.log').write_text(proc.stdout+'\n'+proc.stderr)
        write(path.with_name('external_load.json'),dict(started_utc=begun,finished_utc=datetime.now(timezone.utc).isoformat(),
            before=state,after=load_state()))
        if proc.returncode:
            write(out/'failure.json',dict(job=job,returncode=proc.returncode,stderr=proc.stderr))
            raise RuntimeError('Worker failed; preserve evidence, do not retry')
        row = json.loads(Path(job['row_path']).read_text());rows.append(row);write(out/'comparison.json',rows)
        print(f'{index}/{len(jobs)} n={job["customers"]} {job["method"]}/{job["seed"]}: '
              f'obj={row["objective"]} bound={row["bound"]} root={row["root_runtime"]:.3f}s '
              f'labels={row["expanded_labels"]}',flush=True)
    checkpoint = json.loads((NIGHT/'checkpoint.json').read_text())
    checkpoint.update(status='LABEL_BOUND_PAIRED_TRIALS_COMPLETE_VALIDATION_PENDING',active_process=None)
    write(NIGHT/'checkpoint.json',checkpoint)


if __name__=='__main__':
    main()
