"""Frozen total-budget complete M3 kernel comparisons, ambient-load gate."""
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

ROOT=Path(__file__).resolve().parents[4];OUT=Path(__file__).resolve().parent;NIGHT=OUT.parent;BATCH=OUT/'full15_30'
NAMES=('alns_single_batch_overnight_20261001','m5_overnight_20261001','model5_alns_overnight_20261001')
def read(p):return json.loads(p.read_text())
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def external():
    return dict(load=os.getloadavg(),checkpoints={n:read(ROOT/'output/experiments'/n/'checkpoint.json') for n in NAMES},
        process_inventory='Sandbox ps unavailable; uncontrolled global load remains')
def checkpoint(status,active=None,**fields):
    c=read(NIGHT/'checkpoint.json');c.update(status=status,active_process=active,
        updated_utc=datetime.now(timezone.utc).isoformat(),**fields)
    if active is None:c['active_tool_session_id']=None
    write(NIGHT/'checkpoint.json',c)

def worker(path):
    job=read(Path(path));sys.path.insert(0,job['snapshot'])
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.exact.solver import solve_exact
    from models.routing.solvers.exact.single_batch_e1_bound import solve_exact_with_e1_cover
    from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
    cfg,fleet=load_routing_case(job['case_path']);p=load_instance(cfg,fleet);start=read(Path(job['start_path']))
    a=audit_solution(p,start);assert a['valid'] and a['periodic_replay']['valid']
    solver=solve_exact_with_e1_cover if job['candidate'] else solve_exact
    options=dict(clocked=True) if job['candidate'] else {}
    begun=time.perf_counter();r=solver(p,time_limit=job['budget'],seed=job['seed'],verbose=True,initial_result=start,**options)
    wall=time.perf_counter()-begun;audit=audit_solution(p,r);assert audit['valid'] and audit['periodic_replay']['valid']
    info=r['info'];assert info['best_bound']<=audit['objective']+1e-6
    write(Path(job['result_path']),r)
    row=dict(case=job['case'],model=3,seed=job['seed'],budget=job['budget'],candidate=job['candidate'],
        UB=audit['objective'],LB=info['best_bound'],Gap=info['mip_gap'],status=info['status'],
        proven_optimal=info['proven_optimal'],proof_scope=info['proof_scope'],runtime=info['runtime'],wall=wall,
        mip_runtime=info.get('mip_runtime'),mip_work=info.get('mip_work'),nodes=info.get('mip_node_count'),
        variables=info.get('variables'),constraints=info.get('constraints'),
        component_runtime=info.get('e1_cover_stage_runtime',0),component_bound=info.get('e1_cover_certificate',{}).get('bound'),
        component_applied=info.get('single_batch_e1_cover_floor',{}).get('applied',False),
        original_start_objective=a['objective'],original_start_optimal=start['info'].get('proven_optimal',False),
        start_sha256=sha(Path(job['start_path'])),problem_sha256=problem_fingerprint(p),full_tasks=2*len(p.orders),
        audited=True,periodic_replay=True)
    write(Path(job['row_path']),row)

def prepare():
    BATCH.mkdir();sys.path.insert(0,str(ROOT))
    from configuration.loader import load_routing_case,resolve_project_path
    from experiments.runs.run_multibatch_cycle import freeze_inputs,source_snapshot,INPUT_KEYS
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
    from models.routing.solvers.exact.single_batch_e1_bound import e1_route_cover_bound,apply_e1_cover_bound,enumerate_e1_routes,project_e1_schedule
    from models.routing.solvers.exact.single_batch_pickup_bound import pickup_contexts,project_original_schedule
    from models.routing.solvers.exact.formulation import build_model
    from models.routing.solvers.exact.solver import _set_start
    variants=[];starts=[];projection=[]
    for case in ('zhou_Set2_100-5-1b_subset20','zhou_Set2_100-5-2_subset20'):
        origin=NIGHT/'e1_route_cover01/paired15_30'/case
        cfg,fleet=load_routing_case(origin/'case.json');p=load_instance(cfg,fleet);start=read(origin/'start.json')
        a=audit_solution(p,start);assert a['valid'] and a['periodic_replay']['valid']
        _,ctx,_=pickup_contexts(p);columns=project_original_schedule(p,start,ctx)
        routes,e=enumerate_e1_routes(p,clocked=True);assert e['complete'];e1=project_e1_schedule(p,start,routes,clocked=True)
        certificate=e1_route_cover_bound(p,2,clocked=True);m,context=build_model(p)
        try:
            added=apply_e1_cover_bound(m,p,context,certificate);assert _set_start(p,m,context,start);m.update()
            row=m.getRow(m.getConstrByName('B1_M3_certified_E1_distance_floor'))
            raw=sum(row.getCoeff(i)*row.getVar(i).Start for i in range(row.size()))
            assert abs(raw-a['distances']['e1'])<1e-6 and raw>=certificate['bound']-1e-6
        finally:m.dispose()
        sources={resolve_project_path(cfg[k]).resolve() for k in INPUT_KEYS if isinstance(cfg.get(k),str)}
        variants.append(dict(config=deepcopy(cfg),case=case,model=3,_source_hashes={str(p):sha(p) for p in sources}));starts.append(start)
        projection.append(dict(case=case,origin=str(origin/'start.json'),origin_sha256=sha(origin/'start.json'),
            problem_sha256=problem_fingerprint(p),original_upper=a['objective'],original_start_optimal=start['info'].get('proven_optimal',False),
            original_e1=a['distances']['e1'],actual_row_value=raw,e1_certificate=certificate,e1_projection=e1,
            projected_e2_tasks=sum(len(c['tasks']) for _,c in columns),all_tasks=2*len(p.orders),row=added,audited=True,replay=True))
    write(BATCH/'projection.json',projection);inputs=freeze_inputs(variants,BATCH);source=source_snapshot(BATCH)
    # The independent fixed-path clock oracle is archived with each batch;
    # post-timing verification must never import a later live test revision.
    for name in ('tests/__init__.py','tests/routing/__init__.py',
                 'tests/routing/test_single_batch_solver_audit.py','tests/routing/test_single_batch_e1_clocked.py'):
        target=BATCH/'source_snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/name,target);source[name]=sha(target)
    shutil.copy2(Path(__file__),BATCH/'compare_frozen.py');jobs=[]
    for variant,start in zip(variants,starts):
        folder=BATCH/variant['case'];folder.mkdir();write(folder/'case.json',variant['config']);write(folder/'start.json',start)
        for seed in (42,43,44):
            for budget in ((15,30) if seed!=43 else (30,15)):
                for candidate in ((False,True) if seed!=43 else (True,False)):
                    run=folder/f'{"candidate" if candidate else "baseline"}_{budget}_{seed}';run.mkdir()
                    job=dict(case=variant['case'],seed=seed,budget=budget,candidate=candidate,case_path=str(folder/'case.json'),
                        start_path=str(folder/'start.json'),snapshot=str(BATCH/'source_snapshot'),
                        result_path=str(run/'result.json'),row_path=str(run/'row.json'))
                    path=run/'job.json';write(path,job);jobs.append(str(path))
    write(BATCH/'jobs.json',jobs);write(BATCH/'manifest.json',dict(input_sha256=inputs,source_sha256=source,
        driver_sha256=sha(Path(__file__)),job_count=len(jobs),threads=1,seeds=[42,43,44],budgets=[15,30],
        scope='Complete original B1 M3 integer model, unchanged tasks and physics; candidate adds one valid ORIGINAL_CLOCKED E1 cost row, clocked=True, defaultFalse',
        ambient_policy='Wait between jobs while another recorded active process exists; no global idle claim',external=external()))
    checkpoint('E1_CLOCKED_KERNEL_PREPARED',current_driver=str(Path(__file__)),
        next_action='Frozen24jobs ready. Run conductor once; it waits for other recorded heavy tasks. Resume existing tool session if active, never duplicate/retry completed or failed jobs.')
    print('prepared24 complete-kernel jobs; all original task/row projections passed',flush=True)

def run():
    m=read(BATCH/'manifest.json');assert sha(Path(__file__))==sha(BATCH/'compare_frozen.py')==m['driver_sha256']
    for p,h in m['source_sha256'].items():assert sha(BATCH/'source_snapshot'/p)==h
    jobs=[Path(p) for p in read(BATCH/'jobs.json')];rows=[]
    for index,path in enumerate(jobs,1):
        job=read(path);failure=path.with_name('failure.json')
        if Path(job['row_path']).exists():rows.append(read(Path(job['row_path'])));continue
        if failure.exists():rows.append(read(failure));continue
        while True:
            state=external();busy={n:c.get('active_process') for n,c in state['checkpoints'].items() if c.get('active_process') is not None}
            if not busy:break
            if datetime.now(timezone.utc).isoformat()>='2026-10-01T23:00:00':
                checkpoint('E1_CLOCKED_KERNEL_MORNING_STOP',next_action='No new large run after07:00; retain unfinished24job plan and report results.');return
            checkpoint('E1_CLOCKED_KERNEL_WAITING_EXTERNAL',None,waiting_conductor_pid=os.getpid(),waiting_job_index=index,
                next_action='Conductor waiting for other recorded activity; resume current tool session, do not relaunch.')
            with (OUT/'wait_activity.jsonl').open('a') as f:f.write(json.dumps(dict(utc=datetime.now(timezone.utc).isoformat(),load=state['load'],busy=busy))+'\n')
            time.sleep(30)
        if datetime.now(timezone.utc).isoformat()>='2026-10-01T23:00:00':
            checkpoint('E1_CLOCKED_KERNEL_MORNING_STOP',next_action='No new large run after07:00; retain unfinished plan.');return
        before=external();started=datetime.now(timezone.utc).isoformat()
        checkpoint('RUNNING_E1_CLOCKED_KERNEL_PAIRS',dict(conductor_pid=os.getpid(),index=index,total=len(jobs),job=job),
            next_action='Resume current tool session/results; do not duplicate running24job driver. No own regression/audit solve while timing.')
        try:
            p=subprocess.run([sys.executable,str(Path(__file__)),'--worker',str(path)],cwd=BATCH/'source_snapshot',
                capture_output=True,text=True,timeout=job['budget']+30)
            path.with_name('solver.log').write_text(p.stdout+'\n'+p.stderr)
            if p.returncode:write(failure,dict(case=job['case'],seed=job['seed'],budget=job['budget'],candidate=job['candidate'],status='ERROR',returncode=p.returncode))
        except subprocess.TimeoutExpired as e:
            path.with_name('solver.log').write_text(str(e.stdout or '')+'\n'+str(e.stderr or ''))
            write(failure,dict(case=job['case'],seed=job['seed'],budget=job['budget'],candidate=job['candidate'],status='WORKER_TIMEOUT'))
        write(path.with_name('external_load.json'),dict(started_utc=started,finished_utc=datetime.now(timezone.utc).isoformat(),before=before,after=external()))
        row=read(failure) if failure.exists() else read(Path(job['row_path']));rows.append(row);write(BATCH/'comparison.json',rows)
        print(f'{index}/{len(jobs)} {job["case"]} {job["seed"]}/{job["budget"]} candidate{job["candidate"]}: '
            f'{row["status"]} UB{row.get("UB")} LB{row.get("LB")} Gap{row.get("Gap")}',flush=True)
    write(BATCH/'comparison.json',rows);checkpoint('E1_CLOCKED_KERNEL_PAIRS_COMPLETE',next_action='24 jobs retained. Independently audit original schedules/used certificates, full regression afterward; no default adoption until complete gate.')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--worker');parser.add_argument('--prepare',action='store_true');parser.add_argument('--run',action='store_true');args=parser.parse_args()
    if args.worker:worker(args.worker)
    elif args.prepare:prepare()
    elif args.run:run()
    else:parser.error('Choose --prepare or --run')
