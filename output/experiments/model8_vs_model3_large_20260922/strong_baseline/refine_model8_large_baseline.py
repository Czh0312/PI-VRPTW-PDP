"""Strengthen M3 on positive pilot cases, then repeat a paired M8 comparison.

This post-hoc sensitivity check is stored separately from the six-case pilot.
M3 gets a 30-second warm-started MILP; both second stages get 30 seconds, seed 42.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time

from experiments.runs.run_model8_capacity_probe import ROOT, load_problem
from experiments.runs.run_model8_large_comparison import DEFAULT_OUTPUT, worker as run_stage2
from experiments.runs.run_model8_small_comparison import write


def worker(args):
    from models.routing.variants.model_3.exact_solver import solve_exact
    from models.routing.variants.model_8.baseline import prepare_baseline
    from models.routing.variants.model_8.state import Evaluator, baseline_state

    parent = ROOT / args.input
    folder = parent / 'strong_baseline' / args.instance
    folder.mkdir(parents=True, exist_ok=True)
    baseline = json.loads((parent / args.instance / 'base.json').read_text())
    p, a, b, data_source = load_problem(args.instance)
    history = ROOT / 'output/experiments/model8_capacity_20260922' / args.instance / 'base.json'
    if history.exists():
        previous = json.loads(history.read_text())
        if previous.get('source_sha256') != hashlib.sha256(data_source.read_bytes()).hexdigest():
            raise RuntimeError('Historical baseline dataset hash mismatch')
        snapshot = prepare_baseline(p, a, b, p.fleet.e2_per_sat_count,
                                    (previous['e1_routes'], previous['e2_routes']))
        audit = Evaluator(p, a, b, snapshot).audit(baseline_state(snapshot))
        if not math.isclose(audit['objective'], previous['info']['objective'], abs_tol=1e-6):
            raise RuntimeError('Historical M3 audit mismatch')
        if audit['objective'] < baseline['info']['objective'] - 1e-6:
            baseline = {**previous, 'source': str(history.relative_to(ROOT)),
                        'historical_source_sha256': hashlib.sha256(history.read_bytes()).hexdigest()}
            write(folder / 'historical_warm_start.json', baseline)
    started = time.perf_counter()
    x, y, info = solve_exact(
        p, a, b, p.fleet.e2_per_sat_count, time_limit=args.exact_time,
        warm_start_routes=(baseline['e1_routes'], baseline['e2_routes']),
        verbose=False, return_info=True, return_incumbent_on_time_limit=True,
        gurobi_params={'Threads': 1, 'Seed': 42, 'SoftMemLimit': 1.5,
                       'MIPGap': 0, 'MIPGapAbs': 0})
    exact = {'e1_routes': x, 'e2_routes': y, 'info': info,
             'wall_seconds': time.perf_counter() - started,
             'mip_budget': args.exact_time, 'source': 'warm_started_M3_MILP'}
    if x is not None and y is not None:
        snapshot = prepare_baseline(p, a, b, p.fleet.e2_per_sat_count, (x, y))
        audit = Evaluator(p, a, b, snapshot).audit(baseline_state(snapshot))
        if not math.isclose(audit['objective'], info['objective'], abs_tol=1e-6):
            raise RuntimeError('Refined M3 audit mismatch')
        exact.update(baseline_fingerprint=snapshot['route_fingerprint'],
                     independent_audit=audit,
                     effective_vehicle_count=max(map(len, y.values())))
    write(folder / 'm3_exact.json', exact)
    if x is not None and info['objective'] <= baseline['info']['objective'] + 1e-6:
        selected = exact
    else:
        selected = {**baseline, 'selection': 'best_audited_M3_baseline_retained'}
    write(folder / 'base.json', selected)
    print(json.dumps({'instance': args.instance, 'method': 'strong_M3',
                      'objective': selected['info']['objective'],
                      'proven': bool(selected['info'].get('proven_optimal'))}), flush=True)
    args.output = str((parent / 'strong_baseline').relative_to(ROOT))
    args.seed = 42
    for method in ('control', 'relay'):
        args.worker = method
        run_stage2(args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', default=DEFAULT_OUTPUT)
    parser.add_argument('--exact-time', type=float, default=30)
    parser.add_argument('--stage2-time', type=float, default=30)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--instance')
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    parent = ROOT / args.input
    summary = json.loads((parent / 'summary.json').read_text())
    selected = [r['instance'] for r in summary['instances']
                if r['best_relay_transfers'] > 0 and r['m8_saving'] > 1e-6]
    folder = parent / 'strong_baseline'
    if folder.exists():
        raise FileExistsError('Preserve previous refinement; use a new input/output experiment')
    folder.mkdir(parents=True)
    source = Path(__file__).resolve()
    shutil.copy2(source, folder / source.name)
    write(folder / 'manifest.json', {'instances': selected, 'exact_time': args.exact_time,
          'stage2_time': args.stage2_time, 'seed': 42,
          'selection': 'post-hoc positive cases with transfers in the main pilot',
          'historical_baseline_policy': 'If a previously saved M3 solution on identical data audits feasible and is better, use it as the MILP warm start and incumbent.',
          'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
          'proof_scope': 'M3 proof reported individually; stage-two ALNS remains heuristic'})

    def launch(instance):
        case = folder / instance
        case.mkdir(parents=True)
        command = [sys.executable, '-m', 'experiments.runs.refine_model8_large_baseline',
                   '--worker', '--instance', instance, '--input', args.input,
                   '--exact-time', str(args.exact_time), '--stage2-time', str(args.stage2_time)]
        started = time.perf_counter()
        with (case / 'worker.log').open('w') as log:
            try:
                result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                        timeout=args.exact_time + 2 * args.stage2_time + 60,
                                        check=False)
                status = 'completed' if result.returncode == 0 else f'failed:{result.returncode}'
            except subprocess.TimeoutExpired:
                status = 'hard_timeout'
        return {'instance': instance, 'status': status, 'wall_seconds': time.perf_counter() - started}

    jobs = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(launch, instance) for instance in selected]
        for future in as_completed(futures):
            job = future.result()
            jobs.append(job)
            write(folder / 'jobs.json', jobs)
            print(json.dumps(job), flush=True)
    if any(job['status'] != 'completed' for job in jobs):
        raise RuntimeError('Refinement incomplete: inspect saved outputs; do not label timeout as zero gain')


if __name__ == '__main__':
    main()
