"""Registered fresh short/long paired all-task M3 root diagnostics."""
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timezone
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[4];OUT=Path(__file__).resolve().parent;NIGHT=OUT.parent
def read(p):return json.loads(p.read_text())
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def external():
    return dict(load=os.getloadavg(),checkpoints={n:read(ROOT/'output/experiments'/n/'checkpoint.json')
        for n in ('alns_single_batch_overnight_20261001','m5_overnight_20261001','model5_alns_overnight_20261001')},
        process_inventory='Unavailable in sandbox; external load uncontrolled')

def worker(path):
    job=read(Path(path));sys.path.insert(0,job['snapshot'])
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.exact.single_batch_pickup_bound import root_pickup_route_bound
    cfg,fleet=load_routing_case(job['case_path']);p=load_instance(cfg,fleet)
    start=read(Path(job['start_path']));audit=audit_solution(p,start)
    assert audit['valid'] and audit['periodic_replay']['valid']
    result=root_pickup_route_bound(p,time_limit=job['budget'],seed=job['seed'],initial_result=start,
        e1_route_cover=job['e1_cover'])
    assert result['bound']<=audit['objective']+1e-6 and not result['proven_optimal']
    write(Path(job['result_path']),result)
    priced=[r for h in result['history'] for r in h['pricing'].values()]
    row=dict(case=job['case'],model=3,seed=job['seed'],budget=job['budget'],e1_cover=job['e1_cover'],
        bound=result['bound'],e2_bound=result['e2_bound'],e1_floor=result['e1_floor'],status=result['status'],
        runtime=result['runtime'],iterations=result['iterations'],columns=result.get('columns'),
        e1_cover_runtime=result.get('e1_route_cover',{}).get('runtime',0),e1_cover_complete=result.get('e1_route_cover',{}).get('relaxation_complete',False),
        pricing_calls=len(priced),pricing_runtime=sum(r['runtime'] for r in priced),
        full_original_upper=audit['objective'],start_sha256=sha(Path(job['start_path'])),
        lp_complete=result['relaxation_complete'],proven_integer_optimal=False)
    write(Path(job['row_path']),row)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--worker');args=parser.parse_args()
    if args.worker:return worker(args.worker)
    sys.path.insert(0,str(ROOT))
    from configuration.loader import load_routing_case,resolve_project_path
    from experiments.runs.run_multibatch_cycle import freeze_inputs,source_snapshot,INPUT_KEYS
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.exact.single_batch_pickup_bound import pickup_contexts,project_original_schedule
    from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
    from models.routing.solvers.exact.primal import construct_incumbent
    from models.routing.solvers.exact.single_batch_e1_bound import enumerate_e1_routes,project_e1_schedule
    batch=OUT/'paired15_30';batch.mkdir()
    variants,starts,projections,preparation=[],[],[],[]
    for source in ('zhou_Set2_100-5-1b_subset20','zhou_Set2_100-5-2_subset20'):
        if source.endswith('1b_subset20'):
            prior=NIGHT/'pickup_route_relaxation01/strength15_30'/f'{source}_M3'
            cfg,fleet=load_routing_case(prior/'case.json');p=load_instance(cfg,fleet)
            start=read(prior/'start.json');origin=dict(path=str(prior/'start.json'),sha256=sha(prior/'start.json'))
        else:
            prior=OUT/'independent_setup20'
            cfg,fleet=load_routing_case(prior/'case.json');p=load_instance(cfg,fleet)
            start=read(prior/'result.json') if (prior/'audit.json').exists() and read(prior/'audit.json')['valid'] else None
            origin=dict(method='one complete original integer solve',budget=20,path=str(prior/'result.json'))
        domains,contexts,check=pickup_contexts(p)
        if start is None or (check is not None and not check['passed']):
            preparation.append(dict(source=source,origin=origin,usable=False,domain=(dict(passed=check["passed"],errors=check["errors"]) if check is not None else None),
                limitation='No audited initial schedule or necessary domain failure; no business data adjustment'))
            write(batch/'preparation.json',preparation);continue
        audit=audit_solution(p,start);assert audit['valid'] and audit['periodic_replay']['valid']
        cols=project_original_schedule(p,start,contexts)
        e1_routes,universe=enumerate_e1_routes(p);assert universe['complete']
        e1_projection=project_e1_schedule(p,start,e1_routes)
        projections.append(dict(case=source,model=3,all_tasks=2*len(p.orders),problem_sha256=problem_fingerprint(p),
            projected_tasks=sum(len(c['tasks']) for _,c in cols),projected_e2=sum(c['cost'] for _,c in cols),
            original_upper=audit['objective'],original_e1=audit['distances']['e1'],e1_projection=e1_projection,columns=[[s,c] for s,c in cols]))
        preparation.append(dict(source=source,origin=origin,usable=True,domain=(dict(passed=check["passed"],errors=check["errors"]) if check is not None else None),original_upper=audit['objective']))
        inputs={resolve_project_path(cfg[k]).resolve() for k in INPUT_KEYS if isinstance(cfg.get(k),str)}
        variants.append(dict(config=deepcopy(cfg),case=source,model=3,_source_hashes={str(p):sha(p) for p in inputs}))
        starts.append(start)
    write(batch/'preparation.json',preparation);write(batch/'projection.json',projections)
    assert variants,'Retain failure; no source supports this diagnostic'
    inputs=freeze_inputs(variants,batch);source=source_snapshot(batch);shutil.copy2(Path(__file__),batch/'compare_frozen.py')
    jobs=[]
    for variant,start in zip(variants,starts):
        folder=batch/variant['case'];folder.mkdir();write(folder/'case.json',variant['config']);write(folder/'start.json',start)
        for seed in (42,43):
            for budget in ((15,30) if seed==42 else (30,15)):
                for e1_cover in ((False,True) if seed==42 else (True,False)):
                    run=folder/f'{"candidate" if e1_cover else "baseline"}_{budget}_{seed}';run.mkdir()
                    job=dict(case=variant['case'],model=3,seed=seed,budget=budget,e1_cover=e1_cover,
                        case_path=str(folder/'case.json'),start_path=str(folder/'start.json'),snapshot=str(batch/'source_snapshot'),
                        result_path=str(run/'result.json'),row_path=str(run/'row.json'))
                    path=run/'job.json';write(path,job);jobs.append((path,job))
    write(batch/'manifest.json',dict(input_sha256=inputs,source_sha256=source,driver_sha256=sha(Path(__file__)),
        job_count=len(jobs),seeds=[42,43],budgets=[15,30],threads=1,external=external(),
        scope='Fresh root-strength paired comparison only; all preprocessing/E1 cover/exact pricing charged to total root budget',
        limitation='No end-to-end original integer kernel integration or default adoption. Other threads may load machine.'))
    rows=[]
    for index,(path,job) in enumerate(jobs,1):
        before=external();begun=datetime.now(timezone.utc).isoformat()
        cp=read(NIGHT/'checkpoint.json');cp.update(status='RUNNING_E1_ROUTE_COVER_PAIRS',updated_utc=begun,
            current_driver=str(Path(__file__)),active_process=dict(conductor_pid=os.getpid(),index=index,total=len(jobs),job=job),
            next_action='Resume recorded session, never restart driver. Independently verify certified bounds and full regression after all paired jobs.')
        write(NIGHT/'checkpoint.json',cp)
        process=subprocess.run([sys.executable,str(Path(__file__)),'--worker',str(path)],cwd=batch/'source_snapshot',
            capture_output=True,text=True,timeout=job['budget']+30)
        path.with_name('solver.log').write_text(process.stdout+'\n'+process.stderr)
        write(path.with_name('external_load.json'),dict(started_utc=begun,finished_utc=datetime.now(timezone.utc).isoformat(),before=before,after=external()))
        if process.returncode:
            write(batch/'failure.json',dict(job=job,returncode=process.returncode,stderr=process.stderr))
            raise RuntimeError('Retain failed pair; do not retry timing')
        row=read(Path(job['row_path']));rows.append(row);write(batch/'comparison.json',rows)
        print(f'{index}/{len(jobs)} {job["case"]} {job["seed"]}/{job["budget"]} e1_cover={job["e1_cover"]}: '
            f'bound={row["bound"]:.6f} E2={row["e2_bound"]:.6f} {row["status"]} columns={row["columns"]}',flush=True)
    cp=read(NIGHT/'checkpoint.json');cp.update(status='E1_ROUTE_COVER_PAIRS_COMPLETE',active_process=None)
    write(NIGHT/'checkpoint.json',cp)

if __name__=='__main__':main()
