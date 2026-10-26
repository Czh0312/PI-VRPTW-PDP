"""One large M5 case: cost-aware repair with/without marginal pickup candidates.

Run each case/strategy once, serially, with independent processes and audits.
Fixed baseline-then-candidate order. Preserve failures; never retry a run.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import resource
import shutil
import subprocess
import sys
import time
import zipfile


ROOT = Path(__file__).resolve().parents[2]
DATASET = Path('data/formal_benchmark27_spacetime_v2/model_5/large')


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def worker(args):
    sys.path.insert(0, str(args.source))
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.model_5.alns.solver import solve_alns
    from models.routing.model_5.alns.stock_chain import stock_requirements
    from tools.configuration.loader import load_routing_case

    case = args.cases[0]
    problem = load_instance(*load_routing_case(args.source / DATASET / case / 'case.json'))
    cpu = time.process_time()
    begun = time.perf_counter()
    result = solve_alns(problem, max_time=args.seconds, max_iterations=10**9, seed=args.seed,
                        critical_stock_repair=True, stock_chain_strategy=args.strategy)
    elapsed, cpu_elapsed = time.perf_counter() - begun, time.process_time() - cpu
    # External independent replay is outside the timed solve. Internal
    # incumbent audits remain inside the solver budget.
    audit_begun = time.perf_counter()
    audit = audit_solution(problem, result)
    audit_seconds = time.perf_counter() - audit_begun
    info = result['info']
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    summary = dict(case=case, strategy=args.strategy, seed=args.seed, seconds=args.seconds,
        pid=os.getpid(), customers=len({o.customer for o in problem.orders}), orders=len(problem.orders),
        quantity=problem.total, satellites=len(problem.satellites), batches=len(problem.operating_policy.batches),
        batch_demands=problem.batch_demands, e1_vehicles=problem.fleet.e1_count,
        e2_per_sat=problem.fleet.e2_per_sat_count, status=info.get('status'),
        valid=audit['valid'], periodic_valid=audit.get('periodic_replay', {}).get('valid', False),
        objective=info.get('objective'), pool=result.get('container_pool_size'),
        pool_lower_bound=result.get('precheck', {}).get('pool_lower_bound'),
        stock_requirements=stock_requirements(problem, result) if audit['valid'] else None,
        wall_seconds=elapsed, cpu_seconds=cpu_elapsed, external_audit_seconds=audit_seconds,
        first_feasible_seconds=info.get('time_to_first_feasible'),
        initial_objective=info.get('initial_objective'), iterations=info.get('iterations'),
        evaluations=info.get('evaluation_cache_misses'), costs=result.get('cost_components'),
        distance_e1=audit.get('distances', {}).get('e1'), distance_e2=audit.get('distances', {}).get('e2'),
        trips=dict(e1=len(result.get('e1_trips', [])), e2=len(result.get('e2_trips', []))),
        stock_chain={k: v for k, v in info.items() if k.startswith('stock_chain_')},
        e2_schedule_cache_enabled=info.get('e2_schedule_cache_enabled', False),
        peak_rss_bytes=rss if sys.platform == 'darwin' else rss * 1024)
    write(args.output / 'result.json', result)
    write(args.output / 'audit.json', audit)
    write(args.output / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if not summary['valid'] or not summary['periodic_valid']:
        raise RuntimeError('Independent cycle replay did not pass; raw outputs retained')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--cases', nargs='+', default=['L07'])
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--strategy', choices=['cost_aware', 'cost_aware_marginal'], help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.source, args.output = args.source.resolve(), args.output.resolve()
    if (len(args.cases) != 1 or len(set(args.cases)) != len(args.cases)
            or any(re.fullmatch(r'L\d{2}', c) is None for c in args.cases)):
        parser.error('Choose exactly one large case ID (default L07)')
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error('A finite positive time budget is required')
    if args.worker:
        if len(args.cases) != 1 or args.strategy is None:
            parser.error('Worker requires one case and an explicit strategy')
        worker(args)
        return
    if any(not (args.source / DATASET / c / 'case.json').is_file() for c in args.cases):
        parser.error('Case inputs do not exist in the source directory')
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('Use a new or empty output directory')
    sys.path.insert(0, str(ROOT))
    from experiments.runs.pilot_m5_event_cache import freeze

    args.output.mkdir(parents=True, exist_ok=True)
    source = args.output / 'sources' / 'shared'
    hashes = freeze(args.source, source, args.cases)
    shutil.copy2(__file__, args.output / 'driver.py')
    shutil.copy2(ROOT / 'experiments/runs/pilot_m5_event_cache.py', args.output / 'freeze_helper.py')
    with zipfile.ZipFile(args.output / 'sources.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for path in sorted(source.rglob('*')):
            if path.is_file():
                z.write(path, path.relative_to(args.output))
    order = [(args.cases[0], strategy) for strategy in ('cost_aware', 'cost_aware_marginal')]
    timeout = max(90, args.seconds + 60)
    write(args.output / 'manifest.json', dict(kind='single_case_marginal_pickup_pilot',
        created_at=datetime.now(timezone.utc).isoformat(), command=sys.argv,
        base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        branch=subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip(),
        python=sys.version, executable=sys.executable, platform=platform.platform(),
        cases=args.cases, seed=args.seed, seconds=args.seconds, iteration_limit=10**9,
        source_and_input_sha256=hashes, driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        run_order=order, concurrency=1, python_hash_seed=0, thread_limits=1,
        warm_start=False, e2_schedule_cache_enabled=False, retry=False,
        worker_hard_timeout_seconds=timeout, monitor_seconds=30,
        repair_limits=dict(beam_width=2, max_depth=4, max_evaluations=192, max_neighbors=32,
            invocation_period=150, call_seconds=0.4, total_time_fraction=0.15)))
    packages = subprocess.run([sys.executable, '-m', 'pip', 'freeze'], capture_output=True, text=True, timeout=30)
    (args.output / 'dependencies.txt').write_text(packages.stdout + packages.stderr)
    env = {**os.environ, 'PYTHONHASHSEED': '0', **{name: '1' for name in
        ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')}}
    rows, runs = [], []
    for case, strategy in order:
        destination = args.output / 'quality' / case / strategy
        destination.mkdir(parents=True)
        command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--cases', case,
            '--strategy', strategy, '--source', str(source), '--output', str(destination),
            '--seconds', str(args.seconds), '--seed', str(args.seed)]
        print(f'START {case} {strategy}', flush=True)
        begun = time.perf_counter()
        timed_out = False
        with (destination / 'worker.log').open('w') as log:
            proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            while proc.poll() is None:
                elapsed = time.perf_counter() - begun
                progress = dict(case=case, strategy=strategy, pid=proc.pid, elapsed=elapsed,
                                state='RUNNING', updated_at=datetime.now(timezone.utc).isoformat())
                write(args.output / 'progress.json', progress)
                if elapsed >= timeout:
                    print(f'HARD TIMEOUT {case} {strategy}; stopping PID {proc.pid}, no retry', flush=True)
                    proc.kill()
                    proc.wait()
                    timed_out = True
                    break
                try:
                    proc.wait(timeout=min(30, timeout - elapsed))
                except subprocess.TimeoutExpired:
                    print(f'RUNNING {case} {strategy}: {time.perf_counter() - begun:.0f}s', flush=True)
        run = dict(case=case, strategy=strategy, pid=proc.pid, returncode=proc.returncode,
                   hard_timeout=timed_out, process_wall_seconds=time.perf_counter() - begun, command=command)
        runs.append(run)
        if (destination / 'summary.json').exists():
            row = json.loads((destination / 'summary.json').read_text())
        else:
            row = dict(case=case, strategy=strategy, status='TIMEOUT' if timed_out else 'CRASHED',
                       valid=False, periodic_valid=False)
        row['worker_returncode'] = proc.returncode
        rows.append(row)
        write(args.output / 'results.json', rows)
        write(args.output / 'runs.json', runs)
        print(f'DONE {case} {strategy}: objective={row.get("objective")}, valid={row["valid"]}, exit={proc.returncode}', flush=True)
    success = all(r['worker_returncode'] == 0 and r['valid'] and r['periodic_valid'] for r in rows)
    completion = dict(status='COMPLETED' if success else 'COMPLETED_WITH_FAILURES', workers=len(rows),
                      all_audits_valid=success, finished_at=datetime.now(timezone.utc).isoformat())
    write(args.output / 'completion.json', completion)
    write(args.output / 'progress.json', completion)
    if not success:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
