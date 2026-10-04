"""Frozen complete-kernel ablation; offline certificates are not free speedups."""
from pathlib import Path
from collections import Counter
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


def read(path):
    return json.loads(path.read_text())


def write(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def external():
    names = ('alns_single_batch_overnight_20261001','m5_overnight_20261001','model5_alns_overnight_20261001')
    return dict(checkpoints={n:read(ROOT/'output/experiments'/n/'checkpoint.json') for n in names},
        load=os.getloadavg(),process_inventory='sandbox denies ps; external activity not fully controlled')


def fixed_schedule_planes(p,result,cert):
    from models.routing.common.cycle.audit import audit_solution
    audit = audit_solution(p,result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    assignment = audit['assignment']
    counts = Counter(t['satellite_id'] for t in result['e2_trips'])
    distances = []
    for trip in result['e2_trips']:
        site = trip['satellite_id']
        sequence = [site]+[p.order_map[e['order_id']].customer for e in trip['events']]+[site]
        distances.extend(p.distance(a,b) for a,b in zip(sequence,sequence[1:]))
    e2_distance = __import__('math').fsum(distances)
    assert abs(e2_distance-audit['distances']['e2'])<1e-7
    slacks = []
    for cut in cert['dual_cuts']:
        rhs = __import__('math').fsum(v for s,c,v in cut['assignment_terms'] if assignment[c]==s)
        rhs += __import__('math').fsum(v*counts[s] for s,v in cut['trip_terms'])-cut['margin']
        assert rhs<=e2_distance+1e-8,(rhs,e2_distance,cut)
        slacks.append(e2_distance-rhs)
    assert cert['bound']<=audit['objective']+1e-8
    return dict(rows=len(slacks),minimum_slack=min(slacks,default=None),
        original_objective=audit['objective'],e2_distance=e2_distance,audited=True,replay=True)


def worker(path):
    job = read(Path(path));sys.path.insert(0,job['snapshot'])
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.exact.solver import solve_exact
    from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
    cfg,fleet = load_routing_case(job['case_path']);p = load_instance(cfg,fleet)
    start,certificate = read(Path(job['start_path'])),read(Path(job['certificate_path']))
    assert certificate['problem_sha256']==problem_fingerprint(p)
    projection = fixed_schedule_planes(p,start,certificate)
    begun = time.perf_counter()
    result = solve_exact(p,time_limit=job['budget'],seed=job['seed'],verbose=True,
        initial_result=start,_single_batch_route_certificate=certificate)
    wall = time.perf_counter()-begun
    audit = audit_solution(p,result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert audit['objective']<=projection['original_objective']+1e-6
    assert result['info']['best_bound']<=audit['objective']+1e-6
    result['audit'] = audit;write(Path(job['result_path']),result)
    info = result['info']
    write(Path(job['row_path']),dict(customers=job['customers'],case=job['case'],seed=job['seed'],
        method=job['method'],budget=job['budget'],wall_time=wall,objective=info['objective'],
        bound=info['best_bound'],gap=info['mip_gap'],status=info['status'],
        proven_optimal=info['proven_optimal'],proven_infeasible=info['proven_infeasible'],
        proof_scope=info['proof_scope'],audited=True,replay=True,
        mip_work=info.get('mip_work'),nodes=info.get('mip_node_count'),mip_runtime=info.get('mip_runtime'),
        root_seconds=info.get('root_optimal_callback_seconds'),
        initial_objective=projection['original_objective'],start_sha256=sha(Path(job['start_path'])),
        certificate_sha256=sha(Path(job['certificate_path'])),plane_projection=projection,
        scalar_floor=certificate['bound'],dual_rows=info.get('single_batch_route_dual_cuts')))


def main():
    parser = argparse.ArgumentParser();parser.add_argument('--worker');args = parser.parse_args()
    if args.worker:return worker(args.worker)
    sys.path.insert(0,str(ROOT))
    from configuration.loader import load_routing_case,resolve_project_path
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.exact.single_batch_columns import problem_fingerprint
    from experiments.runs.run_multibatch_cycle import freeze_inputs,source_snapshot,INPUT_KEYS
    from certificate_sets import certificate_sets
    assert (OUT/'proof.json').exists()
    assert sha(OUT/'certificate_sets.py')==read(OUT/'proof.json')['helper_sha256']
    batch = OUT/'full30';batch.mkdir()
    variants,items,origins = [],[],{}
    for size in (20,30):
        case = f'zhou_Set2_100-5-1b_subset{size}_M2'
        origin = NIGHT/'label_bound01/full60_v2'/case
        cfg,fleet = load_routing_case(origin/'case.json');p = load_instance(cfg,fleet)
        left = read(origin/'baseline_42/result.json')['info']['route_bound_certificate']
        right = read(origin/'bounded_42/result.json')['info']['route_bound_certificate']
        options = certificate_sets(p,left,right)
        available = [path for name in ('full60','full60_v2') for path in
            (NIGHT/'label_bound01'/name/case).glob('*/result.json')]
        audited = []
        for path in sorted(available):
            r = read(path);a = audit_solution(p,r)
            assert a['valid'] and a['periodic_replay']['valid']
            assert r['info']['route_bound_certificate']['problem_sha256']==problem_fingerprint(p)
            audited.append((a['objective'],str(path),r))
        objective,start_origin,start = min(audited,key=lambda item:(item[0],item[1]))
        # Freeze all candidates and deterministic minimum rule before timing.
        origins[case] = dict(start_origin=start_origin,start_sha256=sha(Path(start_origin)),
            audited_candidates=[dict(path=str(path),sha256=sha(path)) for path in sorted(available)],
            common_start_objective=objective,certificate_origins={method:dict(path=str(origin/f'{method}_42/result.json'),
                sha256=sha(origin/f'{method}_42/result.json')) for method in ('baseline','bounded')})
        sources = {resolve_project_path(cfg[k]).resolve() for k in INPUT_KEYS if isinstance(cfg.get(k),str)}
        variants.append(dict(case=case,customers=size,config=deepcopy(cfg),
            _source_hashes={str(path):sha(path) for path in sources}))
        items.append((p,start,options))
    inputs = freeze_inputs(variants,batch);source = source_snapshot(batch)
    frozen = batch/'experiment_source';frozen.mkdir()
    for name in ('run.py','certificate_sets.py','prove.py'):
        __import__('shutil').copy2(OUT/name,frozen/name)
    jobs,rows,projections = [],[],{}
    for variant,(p,start,options) in zip(variants,items):
        folder = batch/variant['case'];folder.mkdir()
        case_path,start_path = folder/'case.json',folder/'start.json'
        write(case_path,variant['config']);write(start_path,start)
        for method,cert in options.items():
            write(folder/f'certificate_{method}.json',cert)
            projections[f'{variant["case"]}/{method}'] = fixed_schedule_planes(p,start,cert)
        for i,seed in enumerate((42,43,44)):
            methods = ('baseline','bounded','union');methods = methods[i:]+methods[:i]
            for method in methods:
                run = folder/f'{method}_{seed}';run.mkdir()
                job = dict(case=variant['case'],customers=variant['customers'],method=method,seed=seed,budget=30,
                    case_path=str(case_path),start_path=str(start_path),snapshot=str(batch/'source_snapshot'),
                    certificate_path=str(folder/f'certificate_{method}.json'),
                    result_path=str(run/'result.json'),row_path=str(run/'row.json'))
                path = run/'job.json';write(path,job);jobs.append((path,job))
    write(batch/'fixed_projection.json',projections)
    write(batch/'manifest.json',dict(input_sha256=inputs,source_sha256=source,origins=origins,
        experiment_source_sha256={name:sha(OUT/name) for name in ('run.py','certificate_sets.py','prove.py')},
        job_count=len(jobs),budget=30,threads=1,seeds=[42,43,44],other_night_tasks=external(),
        scope='offline frozen certificate, common scalar floor and common audited original start; complete-kernel diagnostic only',
        limitation='No CG/pool stage; cannot claim end-to-end speedup or independent-source generality. External CPU not controlled.'))
    for index,(path,job) in enumerate(jobs,1):
        before = external();started = datetime.now(timezone.utc).isoformat()
        cp = read(NIGHT/'checkpoint.json');cp.update(status='RUNNING_CERTIFICATE_ABLATION',
            active_process=dict(conductor_pid=os.getpid(),job_index=index,total=len(jobs),job=job),
            current_experiment='certificate_ablation01/full30',updated_utc=started,
            next_action='Resume recorded tool session; never repeat active/completed kernel ablation. After18 jobs validate all rows/provenance, then interpret only this offline diagnostic.')
        write(NIGHT/'checkpoint.json',cp)
        process = subprocess.run([sys.executable,str(OUT/'run.py'),'--worker',str(path)],
            cwd=batch/'source_snapshot',capture_output=True,text=True,timeout=job['budget']+30)
        path.with_name('solver.log').write_text(process.stdout+'\n'+process.stderr)
        write(path.with_name('external_load.json'),dict(started_utc=started,
            finished_utc=datetime.now(timezone.utc).isoformat(),before=before,after=external()))
        if process.returncode:
            write(batch/'failure.json',dict(job=job,returncode=process.returncode,stderr=process.stderr))
            raise RuntimeError('Worker failed; retain raw evidence, never retry')
        row = read(Path(job['row_path']));rows.append(row);write(batch/'comparison.json',rows)
        print(f'{index}/{len(jobs)} n{job["customers"]} {job["method"]}/{job["seed"]}: '
            f'obj={row["objective"]:.6f} lb={row["bound"]:.6f} gap={row["gap"]:.6%}',flush=True)
    cp = read(NIGHT/'checkpoint.json');cp.update(status='CERTIFICATE_ABLATION_COMPLETE_VERIFICATION_PENDING',active_process=None)
    write(NIGHT/'checkpoint.json',cp)


if __name__=='__main__':
    main()
