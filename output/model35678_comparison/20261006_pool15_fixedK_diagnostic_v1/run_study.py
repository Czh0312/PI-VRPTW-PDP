"""Nine fresh cost searches: one case, M6/M7/M8, each fixed K has 300 seconds."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import csv, hashlib, json, math, os, random, subprocess, sys, threading, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
PRIOR=OUT.parent/'20261006_truck_tricycle_pool15_5min_v1'
MODES={6:'uniform_cycle',7:'uniform_independent_cutoffs',8:'flexible_cycle'}
BUDGET,ITERATIONS,SEED,WORKERS=300.0,1000000,42,3

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def write(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    tmp.replace(path)

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def register():
    sys.path.insert(0,str(ROOT))
    from experiments.runs.run_model6_comparison import freeze_case,snapshot_source
    from configuration.loader import load_routing_case
    from models.routing.common.cycle.instance import load_instance
    if (OUT/'experiment.json').exists(): raise ValueError('Refusing to overwrite study')
    for folder in ('inputs','cases','results','logs'): (OUT/folder).mkdir(exist_ok=True)
    base,hashes=freeze_case(PRIOR/'cases/0_M6.json',OUT,'shared')
    original=load_instance(*load_routing_case(PRIOR/'cases/0_M6.json',use_pool_config=False))
    old_plan=read(PRIOR/'experiment.json')
    assert original.total==78 and len(original.orders)==50
    profile=original.cost_profile
    write(OUT/'inputs/common_economic.json',profile.config)
    base['economic_config']=str(OUT/'inputs/common_economic.json')
    base.update(alns_time=BUDGET,alns_iter=ITERATIONS,seed=SEED,save_plots=False)
    jobs=[]
    for model in (6,7,8):
        for k in (1,2,3):
            cfg=deepcopy(base)
            cfg.update(model=model,case_name=f'zhou_Set2_100-10-1_subset50_M{model}_fixedK{k}')
            cfg['joint_batch_options'].update(schedule_mode=MODES[model],batch_count_candidates=[k])
            case=OUT/'cases'/f'M{model}_K{k}.json'; write(case,cfg)
            problem=load_instance(*load_routing_case(case,use_pool_config=False))
            assert problem.orders==original.orders
            assert problem.operating_policy==original.operating_policy
            assert problem.cost_profile.hash==profile.hash
            assert problem.batch_count_candidates==(k,)
            assert all(sha(cfg[key])==sha(base[key]) for key in ('data','orders','routing_config','operating_policy'))
            jobs.append(dict(id=f'M{model}_K{k}_seed{SEED}',family='zhou_Set2_100-10-1_subset50',
                model=model,fixed_k=k,method='alns',seed=SEED,budget=BUDGET,iterations=ITERATIONS,case_path=str(case)))
    random.Random(20261006).shuffle(jobs)
    sources=snapshot_source(OUT)
    assert sources==old_plan['source_hashes']
    write(OUT/'experiment.json',dict(registered_at=datetime.now(timezone(timedelta(hours=8))).isoformat(),
        jobs=jobs,workers=WORKERS,alns_seconds=BUDGET,seeds=[SEED],models=[6,7,8],counts=[1,2,3],
        family='zhou_Set2_100-10-1_subset50',customers=50,demand=78,economic_profile_hash=profile.hash,
        economic_profile=profile.config,source_hashes=sources,input_hashes=hashes,
        common_economic_sha256=sha(OUT/'inputs/common_economic.json'),runner_sha256=sha(__file__),
        prior_study=str(PRIOR),prior_experiment_sha256=sha(PRIOR/'experiment.json'),
        origin_skill='academic-research-suite / experiment-agent',execution_mode='continuous',
        python=sys.version,interpreter=sys.executable,
        differences_from_prior=['batch_count_candidates=[K] instead of [1,2,3]',
            '300 seconds and 1000000 iterations independently per K', '3 concurrent processes rather than 4'],
        unchanged=['50-point physical input bytes','orders and windows','cost profile pool15','solver source bytes','seed42'],
        limitations=['K fixes calendar segments, not nonempty deliveries; actual delivery count is audited.',
            'No new nonempty-batch or forced-reuse constraint.', 'Single seed and wall-time limit, no optimality proof.',
            'Benchmark speed retained; not empirical Shenzhen operation.']))
    print('REGISTERED 9 jobs / 300 seconds EACH K / 3 workers',flush=True)

def worker(job_id):
    plan = read(OUT/'experiment.json')
    job = next(r for r in plan['jobs'] if r['id'] == job_id)
    target = OUT/'results'/job_id
    if target.exists():
        raise ValueError('Refusing to overwrite a run')
    target.mkdir()
    sys.path.insert(0, str(OUT/'source_snapshot'))
    from configuration.loader import load_routing_case
    from models.routing.common.cycle.instance import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.cost.comparison import evaluate_comparison_cost
    from models.routing.service import solve_routing
    for name, digest in plan['source_hashes'].items():
        assert sha(OUT/'source_snapshot'/name) == digest
    assert sha(OUT/'inputs/common_economic.json') == plan['common_economic_sha256']
    config, fleet = load_routing_case(job['case_path'], use_pool_config=False)
    problem = load_instance(config, fleet)
    write(target/'input_manifest.json', dict(config=config, fleet=fleet,
        input_sha256={key:sha(value) for key,value in config.items()
                      if isinstance(value,str) and Path(value).is_file()}))
    begin = time.perf_counter()
    stop = threading.Event()
    def heartbeat():
        import resource
        while not stop.is_set():
            usage=resource.getrusage(resource.RUSAGE_SELF)
            write(target/'live_status.json',dict(job_id=job_id,stage='solving',
                elapsed_seconds=time.perf_counter()-begin,
                memory_MiB=usage.ru_maxrss/2**20,cpu_seconds=usage.ru_utime+usage.ru_stime))
            stop.wait(15)
    monitor=threading.Thread(target=heartbeat,daemon=True)
    monitor.start()
    try:
        result = solve_routing(model=job['model'], method='alns', problem=problem,
            alns_time=job['budget'], alns_iter=job['iterations'], seed=job['seed'])
    finally:
        stop.set()
        monitor.join(timeout=2)
    elapsed = time.perf_counter()-begin
    write(target/'result.json', result)
    info = result['info']
    row = {key:value for key,value in job.items() if key!='case_path'}
    row.update(fixed_k=job['fixed_k'], status=info['status'], valid=False, cost=None, distance=None, pool=None,
        minimum_pool=None, minimum_pool_reference_cost=None, cost_components=None,
        objective=info.get('objective'), proven_optimal=bool(info.get('proven_optimal')),
        proven_infeasible=bool(info.get('proven_infeasible')), best_bound=info.get('best_bound'),
        solve_wall_seconds=elapsed, iterations_completed=info.get('iterations'),
        economic_profile_hash=None, k=None, delivery_batches=None, audit_errors=[])
    if info.get('objective') is not None:
        audit = audit_solution(problem, result)
        write(target/'independent_audit.json', audit)
        row.update(valid=bool(audit['valid']), audit_errors=audit['errors'])
        if audit['valid']:
            assert audit['periodic_replay']['valid']
            cost = evaluate_comparison_cost(problem, result, config['economic_config'], audit=audit)
            assert cost['economic_profile_hash'] == plan['economic_profile_hash']
            assert result['cycle_batch_count'] == job['fixed_k']
            assert len(result['count_search']) == 1
            assert result['count_search'][0]['batch_count'] == job['fixed_k']
            assert result['count_search'][0]['budget'] > 299
            assert math.isclose(cost['total'], sum(cost['components'].values()), abs_tol=1e-6)
            if job['model'] in (6,7,8):
                assert math.isclose(cost['total'], info['objective'], abs_tol=1e-5, rel_tol=1e-7)
            write(target/'comparison_cost.json', cost)
            row.update(cost=cost['total'], cost_components=cost['components'],
                economic_profile_hash=cost['economic_profile_hash'],
                distance=sum(audit['distances'].values()), pool=cost['container_pool_size'],
                minimum_pool=audit['minimum_pool_for_schedule'],
                minimum_pool_reference_cost=cost.get('schedule_minimum_pool_reference',{}).get('total'),
                k=result.get('cycle_batch_count',len(problem.operating_policy.batches)),
                delivery_batches=result.get('delivery_batch_count',sum(bool(v) for v in problem.batch_demands.values())),
                e1_trips=audit['e1_trip_count'], e2_trips=audit['e2_trip_count'],
                e1_vehicles=audit['e1_vehicle_count'], e2_vehicles=audit['e2_vehicle_count'])
        else:
            row.update(status='AUDIT_FAILED', proven_optimal=False)
    write(target/'row.json', row)
    write(target/'live_status.json',dict(job_id=job_id,stage='complete',elapsed_seconds=elapsed,
                                      status=row['status'],valid=row['valid']))
    print(json.dumps(row,ensure_ascii=False),flush=True)

def execute(job):
    env = {**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONUTF8':'1','PYTHONHASHSEED':'0'}
    try:
        process = subprocess.run([sys.executable,str(Path(__file__).resolve()),'--job',job['id']],
            cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',
            timeout=job['budget']+180)
        (OUT/'logs'/f"{job['id']}.log").write_text(process.stdout+'\n'+process.stderr,encoding='utf-8')
        if process.returncode:
            raise RuntimeError(f'Worker exited {process.returncode}; logs/{job["id"]}.log')
        return read(OUT/'results'/job['id']/'row.json')
    except Exception as exc:
        if isinstance(exc,subprocess.TimeoutExpired):
            stdout=exc.stdout.decode('utf-8',errors='replace') if isinstance(exc.stdout,bytes) else exc.stdout or ''
            stderr=exc.stderr.decode('utf-8',errors='replace') if isinstance(exc.stderr,bytes) else exc.stderr or ''
            (OUT/'logs'/f"{job['id']}.log").write_text(stdout+'\n'+stderr,encoding='utf-8')
        return dict(**{k:v for k,v in job.items() if k!='case_path'},valid=False,cost=None,distance=None,
            status='HARD_TIMEOUT' if isinstance(exc,subprocess.TimeoutExpired) else 'ERROR',
            proven_optimal=False,error=str(exc))


def run():
    plan=read(OUT/'experiment.json')
    if (OUT/'rows.json').exists(): raise ValueError('Refusing to overwrite rows')
    rows=[]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures=[pool.submit(execute,j) for j in plan['jobs']]
        for f in as_completed(futures):
            r=f.result(); rows.append(r); write(OUT/'rows.json',rows)
            print(f"{len(rows)}/9 {r['id']}: {r['status']}, cost={r['cost']}, N={r.get('pool')}, actual={r.get('delivery_batches')}",flush=True)
    write(OUT/'summary.json',dict(planned=9,completed=len(rows),valid=sum(r['valid'] for r in rows),
        failures=[r for r in rows if not r['valid']],rows=sorted(rows,key=lambda r:(r['model'],r['fixed_k'])),
        economic_profile_hash=plan['economic_profile_hash']))
    print('COMPLETE 9 runs',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--job'); args=parser.parse_args()
    if args.job: worker(args.job)
    else: register(); run()
