"""Frozen, isolated diagnostic runs for the shared eight-model dataset."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import redirect_stdout, redirect_stderr
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback


def read(path):
    return json.loads(Path(path).read_text())


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    temporary.replace(path)


def worker(job_path):
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.service import solve_routing
    from models.cost.joint_batch import JointCostProfile
    job = read(job_path)
    out = Path(job['output'])
    stream = io.StringIO()
    start = time.perf_counter()
    row = {k: job[k] for k in ('id', 'base_case_id', 'model', 'size', 'method', 'seed', 'budget')}
    try:
        with redirect_stdout(stream), redirect_stderr(stream):
            cfg, fleet = load_routing_case(job['case_path'])
            if job.get('pool_rate') is not None:
                economic=read(cfg['economic_config'])
                economic['container_pool_cost_per_period']=job['pool_rate']
                cfg['economic_config']=economic
            if job.get('fixed_k') is not None:
                if job['model'] < 6:
                    raise ValueError('Restricted K diagnostic only applies to M6-M8')
                cfg['joint_batch_options']['batch_count_candidates'] = [job['fixed_k']]
            problem = load_instance(cfg, fleet)
            result = solve_routing(model=job['model'], method=job['method'], problem=problem,
                alns_time=job['budget'], exact_time=job['budget'], alns_iter=10**9,
                seed=job['seed'], exact_backend=cfg['exact_backend'])
            audit = audit_solution(problem, result) if result['info'].get('objective') is not None else None
            if audit is not None:
                result['independent_review_audit'] = audit
            valid = bool(audit and audit['valid'])
            pool = result.get('container_pool_size', problem.operating_policy.container_pool_size)
            economic=cfg['economic_config'] if isinstance(cfg['economic_config'],dict) else read(cfg['economic_config'])
            cost = JointCostProfile(economic).evaluate(audit, pool) if valid and problem.reusable else None
            row.update(status=result['info']['status'], valid=valid,
                fixed_k_diagnostic=job.get('fixed_k'),
                pool_rate_override=job.get('pool_rate'),
                native_objective=result['info'].get('objective'),
                proven_optimal=result['info'].get('proven_optimal', False),
                proven_infeasible=result['info'].get('proven_infeasible', False),
                best_bound=result['info'].get('best_bound'),
                total_distance=sum(audit['distances'].values()) if valid else None,
                comparable_reusable_cost=cost['total'] if cost else None,
                Q=problem.total, N=pool if problem.reusable and valid else None,
                minimum_pool_for_schedule=audit.get('minimum_pool_for_schedule') if valid and problem.reusable else None,
                K=result.get('cycle_batch_count', len(problem.batch_map)) if valid else None,
                count_visited=sum(bool(r.get('visits')) for r in result.get('count_search', [])),
                count_unvisited=sum(not r.get('visits') for r in result.get('count_search', [])),
                e1_trips=audit.get('e1_trip_count') if valid else None,
                e2_trips=audit.get('e2_trip_count') if valid else None,
                audit_errors=audit.get('errors', []) if audit else [])
            write(out/'result.json', result)
    except Exception as exc:
        row.update(status='ERROR', valid=False, error=f'{type(exc).__name__}: {exc}')
        stream.write(traceback.format_exc())
    row['wall_seconds'] = time.perf_counter()-start
    (out/'solver.log').write_text(stream.getvalue())
    write(out/'summary.json', row)


def launch(job, frozen):
    out = Path(job['output'])
    write(out/'job.json', job)
    with (out/'process.log').open('w') as log:
        try:
            completed = subprocess.run([sys.executable, '-m', 'tools.audit.review_formal_benchmark27',
                'worker', '--job', str(out/'job.json')], cwd=frozen,
                stdout=log, stderr=log, timeout=job['budget']+60)
            if completed.returncode and not (out/'summary.json').exists():
                write(out/'summary.json', {**job, 'status': 'PROCESS_ERROR', 'valid': False,
                                          'returncode': completed.returncode})
        except subprocess.TimeoutExpired:
            write(out/'summary.json', {**job, 'status': 'HARD_TIMEOUT', 'valid': False})
    return read(out/'summary.json')


def run(args):
    root = Path(__file__).resolve().parents[2]
    dataset = (root/args.dataset).resolve()
    out = (root/args.output).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('Refusing to overwrite diagnostic study')
    frozen = out/'frozen_root'
    frozen.mkdir(parents=True)
    for name in ('models', 'tools'):
        shutil.copytree(root/name, frozen/name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(dataset, frozen/'data'/dataset.name)
    # Record the exact source and data bytes used, including uncommitted work.
    hashes = {p.relative_to(frozen).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(frozen.rglob('*')) if p.is_file()}
    records = read(frozen/'data'/dataset.name/'manifest.json')['cases']
    jobs = []
    for record in records:
        if record['model'] not in args.models or (args.bases and record['base_case_id'] not in args.bases):
            continue
        for seed in args.seeds:
            job_id = f'{record["base_case_id"]}_m{record["model"]}_{args.method}_s{seed}'
            jobs.append(dict(id=job_id, base_case_id=record['base_case_id'], model=record['model'],
                size=record['size'], case_path=str(frozen/'data'/dataset.name/f'model_{record["model"]}'/
                                                record['size']/record['base_case_id']/'case.json'),
                method=args.method, budget=args.budget, seed=seed, fixed_k=args.fixed_k,pool_rate=args.pool_rate,
                output=str(out/'runs'/job_id)))
    jobs.sort(key=lambda j:(j['base_case_id'], j['seed'], j['model']))
    write(out/'manifest.json', dict(scope='Engineering diagnostic, not a final performance benchmark',
        jobs=jobs, planned=len(jobs), budget=args.budget, method=args.method, workers=args.workers,
        frozen_root=str(frozen), artifact_sha256=hashes, python=sys.version,
        hard_timeout_grace_seconds=60, first_attempts_retained=True))
    rows=[]
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(launch, job, frozen) for job in jobs]
        for future in as_completed(futures):
            row = future.result(); rows.append(row)
            write(out/'progress.json', dict(completed=len(rows), planned=len(jobs),
                valid=sum(r['valid'] for r in rows), last=row))
            if len(rows)%8 == 0 or len(rows)==len(jobs):
                print(f'{len(rows)}/{len(jobs)} valid={sum(r["valid"] for r in rows)} last={row["id"]}:{row["status"]}', flush=True)
    rows.sort(key=lambda r:r['id'])
    write(out/'summary.json', dict(planned=len(jobs), completed=len(rows), valid=sum(r['valid'] for r in rows), rows=rows))
    keys=sorted({key for row in rows for key in row})
    with (out/'runs.csv').open('w', newline='') as handle:
        writer=csv.DictWriter(handle, fieldnames=keys);writer.writeheader();writer.writerows(rows)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command', required=True)
    p=sub.add_parser('worker');p.add_argument('--job', required=True)
    p=sub.add_parser('run');p.add_argument('--dataset', default='data/formal_benchmark27_v1')
    p.add_argument('--output', required=True);p.add_argument('--method', choices=['alns','exact'], default='alns')
    p.add_argument('--models', nargs='+', type=int, default=list(range(1,9)))
    p.add_argument('--bases', nargs='+');p.add_argument('--seeds', nargs='+', type=int, default=[42])
    p.add_argument('--fixed-k', type=int, help='Explicit restricted diagnostic; never edits the dataset')
    p.add_argument('--pool-rate',type=float,help='Synthetic cost sensitivity; never edits the dataset')
    p.add_argument('--budget', type=float, default=3);p.add_argument('--workers', type=int, default=2)
    args=parser.parse_args()
    worker(args.job) if args.command=='worker' else run(args)


if __name__=='__main__':
    main()
