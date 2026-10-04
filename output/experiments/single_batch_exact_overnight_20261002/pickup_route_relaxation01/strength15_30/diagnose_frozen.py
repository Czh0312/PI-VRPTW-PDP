"""Frozen all-task projection and short/long lower-bound strength diagnostic."""
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timezone
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
NIGHT = OUT.parent


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text())
def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def external():
    return dict(load=os.getloadavg(),checkpoints={name:read(ROOT/'output/experiments'/name/'checkpoint.json')
        for name in ('alns_single_batch_overnight_20261001','m5_overnight_20261001','model5_alns_overnight_20261001')},
        process_inventory='ps unavailable in sandbox; no clean-load claim')


def worker(path):
    job = read(Path(path));sys.path.insert(0,job['snapshot'])
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.exact.single_batch_pickup_bound import root_pickup_route_bound
    cfg,fleet = load_routing_case(job['case_path']);p = load_instance(cfg,fleet)
    start = read(Path(job['start_path']));audit = audit_solution(p,start)
    assert audit['valid'] and audit['periodic_replay']['valid']
    begun = time.perf_counter()
    result = root_pickup_route_bound(p,time_limit=job['budget'],seed=job['seed'],initial_result=start)
    wall = time.perf_counter()-begun
    assert result['bound']<=audit['objective']+1e-6
    assert not result['proven_optimal'] and not result['proven_infeasible']
    assert result['all_task_count']==2*len(p.orders)
    write(Path(job['result_path']),result)
    priced = [r for h in result['history'] for r in h['pricing'].values()]
    write(Path(job['row_path']),dict(model=job['model'],seed=job['seed'],budget=job['budget'],case=job['case'],
        bound=result['bound'],e1_floor=result['e1_floor'],e2_bound=result['e2_bound'],
        status=result['status'],lp_complete=result['relaxation_complete'],runtime=wall,
        iterations=result['iterations'],columns=result.get('columns'),full_tasks=result['all_task_count'],
        full_original_upper=audit['objective'],proven_integer_optimal=False,pricing_calls=len(priced),
        pricing_runtime=sum(r['runtime'] for r in priced),start_sha256=sha(Path(job['start_path']))))


def main():
    parser = argparse.ArgumentParser();parser.add_argument('--worker');args=parser.parse_args()
    if args.worker:return worker(args.worker)
    sys.path.insert(0,str(ROOT))
    from configuration.loader import load_routing_case,resolve_project_path
    from experiments.runs.run_multibatch_cycle import freeze_inputs,source_snapshot,INPUT_KEYS
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
    from models.routing.solvers.exact.single_batch_pickup_bound import pickup_contexts,project_original_schedule
    batch = OUT/'strength15_30';batch.mkdir()
    variants,starts,origins,projections = [],[],{},[]
    for model in (3,4):
        case = f'zhou_Set2_100-5-1b_subset20_M{model}'
        origin = ROOT/'output/experiments/single_batch_short_cycles_20261001/causal20'/case
        cfg,fleet = load_routing_case(origin/'case.json');p = load_instance(cfg,fleet)
        domains,contexts,_ = pickup_contexts(p)
        available = []
        for seed in (42,43):
            path=origin/f'baseline_{seed}/result.json';result=read(path);audit=audit_solution(p,result)
            assert audit['valid'] and audit['periodic_replay']['valid']
            columns = project_original_schedule(p,result,contexts)
            projections.append(dict(model=model,seed=seed,result_path=str(path),result_sha256=sha(path),
                problem_sha256=problem_fingerprint(p),original_upper=audit['objective'],all_tasks=2*len(p.orders),
                original_trips=len(result['e2_trips']),projected_trips=len(columns),
                projected_distance=sum(c['cost'] for _,c in columns),projected_tasks=sum(len(c['tasks']) for _,c in columns),
                columns=[[s,c] for s,c in columns],audited=True,replay=True))
            available.append((audit['objective'],str(path),result))
        upper,path,start = min(available,key=lambda r:(r[0],r[1]))
        sources = {resolve_project_path(cfg[k]).resolve() for k in INPUT_KEYS if isinstance(cfg.get(k),str)}
        variants.append(dict(config=deepcopy(cfg),case=case,model=model,_source_hashes={str(p):sha(p) for p in sources}))
        starts.append(start);origins[case]=dict(path=path,sha256=sha(Path(path)),upper=upper,
            problem_sha256=problem_fingerprint(p),prior_integer_records=[dict(seed=seed,path=str(origin/f'baseline_{seed}/result.json'),
                bound=read(origin/f'baseline_{seed}/result.json')['info']['best_bound']) for seed in (42,43)])
    # Every registered full original schedule projects before any timed solve.
    write(batch/'projection.json',projections)
    inputs=freeze_inputs(variants,batch);source=source_snapshot(batch)
    __import__('shutil').copy2(Path(__file__),batch/'diagnose_frozen.py')
    jobs,rows = [],[]
    for variant,start in zip(variants,starts):
        folder=batch/variant['case'];folder.mkdir()
        write(folder/'case.json',variant['config']);write(folder/'start.json',start)
        for seed in (42,43):
            for budget in ((15,30) if seed==42 else (30,15)):
                run=folder/f'bound_{budget}_{seed}';run.mkdir()
                job=dict(case=variant['case'],model=variant['model'],seed=seed,budget=budget,
                    case_path=str(folder/'case.json'),start_path=str(folder/'start.json'),snapshot=str(batch/'source_snapshot'),
                    result_path=str(run/'result.json'),row_path=str(run/'row.json'))
                path=run/'job.json';write(path,job);jobs.append((path,job))
    write(batch/'manifest.json',dict(input_sha256=inputs,source_sha256=source,driver_sha256=sha(Path(__file__)),
        originals=origins,job_count=len(jobs),seeds=[42,43],budgets=[15,30],threads=1,external=external(),
        scope='M3/M4 bound-strength diagnostic only; 20 customers one frozen source block; no full-kernel hook',
        limitation='Past complete-kernel20s bounds are context only, not matched-budget speed comparisons. No default adoption.'))
    for index,(path,job) in enumerate(jobs,1):
        before=external();begun=datetime.now(timezone.utc).isoformat()
        cp=read(NIGHT/'checkpoint.json');cp.update(status='RUNNING_M3_M4_PICKUP_BOUND_STRENGTH',
            current_experiment='pickup_route_relaxation01/strength15_30',updated_utc=begun,
            active_process=dict(conductor_pid=os.getpid(),index=index,total=len(jobs),job=job),
            next_action='Resume recorded tool session/rows, never repeat existing diagnostic. No kernel integration yet. Verify all bounds/source/projection after all8 runs; full regression sequential afterward.')
        write(NIGHT/'checkpoint.json',cp)
        process=subprocess.run([sys.executable,str(Path(__file__)),'--worker',str(path)],
            cwd=batch/'source_snapshot',capture_output=True,text=True,timeout=job['budget']+30)
        path.with_name('solver.log').write_text(process.stdout+'\n'+process.stderr)
        write(path.with_name('external_load.json'),dict(started_utc=begun,finished_utc=datetime.now(timezone.utc).isoformat(),
            before=before,after=external()))
        if process.returncode:
            write(batch/'failure.json',dict(job=job,returncode=process.returncode,stderr=process.stderr))
            raise RuntimeError('Preserve failed diagnostic, do not retry')
        row=read(Path(job['row_path']));rows.append(row);write(batch/'comparison.json',rows)
        print(f'{index}/{len(jobs)} M{job["model"]} seed{job["seed"]} budget{job["budget"]}: '
            f'bound={row["bound"]:.6f} E2={row["e2_bound"]:.6f} {row["status"]} iter={row["iterations"]}',flush=True)
    cp=read(NIGHT/'checkpoint.json');cp.update(status='M3_M4_PICKUP_BOUND_DIAGNOSTIC_COMPLETE',active_process=None)
    write(NIGHT/'checkpoint.json',cp)


if __name__=='__main__':main()
