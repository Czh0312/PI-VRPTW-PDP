# Historical M8 experiments: retain their original two-stage semantics.
"""M3 versus M8 v2 on six unchanged large benchmarks, with matched controls.

Three M3 seeds form a baseline portfolio. Both second-stage branches share
the best audited baseline and use identical seeds and wall-clock budgets.
Independent subprocesses (at most two) have hard timeouts and save all routes.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time
from unittest.mock import patch

from experiments.runs.run_model8_capacity_probe import ROOT, load_problem
from experiments.runs.run_model8_small_comparison import write

DEFAULT_OUTPUT = 'output/experiments/model8_vs_model3_large_20260922'
INSTANCES = [f'Set2_{group}-{i}' for group in ('100-5', '100-10', '200-10')
             for i in (1, 2)]


def worker(args):
    from models.routing.alns.variants.model_3 import solve_alns as solve_m3
    from models.routing.variants.model_8.two_stage_v2.alns import solve_alns as solve_m8
    from models.routing.variants.model_8.two_stage_v2.baseline import prepare_baseline
    from models.routing.variants.model_8.two_stage_v2.state import Evaluator, baseline_state

    p, e1, e2, source = load_problem(args.instance)
    folder = ROOT / args.output / args.instance
    folder.mkdir(parents=True, exist_ok=True)
    if args.worker == 'base':
        write(folder / 'input.json', {
            'instance': args.instance, 'customers': len(p.customers),
            'source': str(source.relative_to(ROOT)),
            'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'fleet': json.loads((ROOT / 'config/routing/validation/zhou_large.json').read_text()),
            'scenario': 'standard_same_day'})
        candidates = []
        for seed in args.seeds:
            started = time.perf_counter()
            a, b, info = solve_m3(p, e1, e2, p.fleet.e2_per_sat_count,
                                 max_time=args.base_time, max_iterations=1000000,
                                 seed=seed, verbose=False, return_info=True)
            record = {'e1_routes': a, 'e2_routes': b, 'info': info,
                      'seed': seed, 'budget': args.base_time,
                      'wall_seconds': time.perf_counter() - started}
            if a is not None and b is not None:
                base = prepare_baseline(p, e1, e2, p.fleet.e2_per_sat_count, (a, b))
                audit = Evaluator(p, e1, e2, base).audit(baseline_state(base))
                if not math.isclose(audit['objective'], info['objective'], abs_tol=1e-6):
                    raise RuntimeError('M3 objective audit mismatch')
                record.update(baseline_fingerprint=base['route_fingerprint'],
                              effective_vehicle_count=max(map(len, b.values())),
                              independent_audit=audit)
                candidates.append(record)
            write(folder / f'm3_{seed}.json', record)
            print(json.dumps({'instance': args.instance, 'method': 'm3',
                              'seed': seed, 'objective': info.get('objective')}), flush=True)
        if not candidates:
            raise RuntimeError('No feasible M3 baseline; stage two is not run')
        best = min(candidates, key=lambda r: r['info']['objective'])
        write(folder / 'base.json', {**best, 'selection': 'best_of_current_three_seed_M3_portfolio',
                                   'portfolio_seeds': args.seeds})
        return

    base_record = json.loads((folder / 'base.json').read_text())
    base = prepare_baseline(p, e1, e2, base_record['effective_vehicle_count'],
                            (base_record['e1_routes'], base_record['e2_routes']))
    evaluator = Evaluator(p, e1, e2, base)
    failures = Counter()
    original_audit = Evaluator.audit

    def measured_audit(self, state):
        try:
            return original_audit(self, state)
        except ValueError as error:
            prefix = 'with_handoff: ' if state['transfers'] else 'no_handoff: '
            failures[prefix + str(error)] += 1
            raise

    started = time.perf_counter()
    with patch.object(Evaluator, 'audit', measured_audit):
        a, b, info = solve_m8(
            p, e1, e2, base_record['effective_vehicle_count'],
            base_routes=(base_record['e1_routes'], base_record['e2_routes']),
            base_info=base_record['info'], max_time=args.stage2_time,
            max_iterations=1000000, seed=args.seed, verbose=False, return_info=True,
            allow_transfers=args.worker == 'relay', repair_time_limit=.5,
            max_remove=3, insertion_beam=4, max_handoff_events=16)
    audit = evaluator.audit(info['state'])
    if (info['baseline']['route_fingerprint'] != base['route_fingerprint'] or
            a != base['e1_routes'] or b != info['state']['e2_routes'] or
            not math.isclose(audit['objective'], info['objective'], abs_tol=1e-6) or
            info['objective'] > base['objective'] + 1e-6 or
            (args.worker == 'control' and info['state']['transfers'])):
        raise RuntimeError('Second-stage result audit mismatch')
    record = {'e1_routes': a, 'e2_routes': b, 'info': info,
              'seed': args.seed, 'stage2_budget': args.stage2_time,
              'wall_seconds': time.perf_counter() - started,
              'audit_rejections': dict(failures), 'reloaded_audit_valid': True,
              'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                              / (1024 ** 2 if sys.platform == 'darwin' else 1024)}
    write(folder / f'{args.worker}_{args.seed}.json', record)
    print(json.dumps({'instance': args.instance, 'method': args.worker,
                      'seed': args.seed, 'objective': info['objective'],
                      'transfers': info['transshipment_count'],
                      'iterations': info['iterations']}), flush=True)


def run_job(args, instance, method, seed):
    folder = ROOT / args.output / instance
    folder.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-m', 'experiments.runs.run_model8_large_comparison',
               '--worker', method, '--instance', instance, '--seed', str(seed),
               '--seeds', *map(str, args.seeds), '--base-time', str(args.base_time),
               '--stage2-time', str(args.stage2_time), '--output', args.output]
    started = time.perf_counter()
    with (folder / f'{method}_{seed}.log').open('w') as log:
        try:
            done = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                  timeout=args.hard_timeout, check=False)
            status = 'completed' if done.returncode == 0 else f'failed:{done.returncode}'
        except subprocess.TimeoutExpired:
            status = 'hard_timeout'
    return {'instance': instance, 'method': method, 'seed': seed, 'status': status,
            'wall_seconds': time.perf_counter() - started, 'command': command}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instances', nargs='+', default=INSTANCES)
    parser.add_argument('--seeds', type=int, nargs='+', default=[42, 7, 19])
    parser.add_argument('--base-time', type=float, default=10)
    parser.add_argument('--stage2-time', type=float, default=30)
    parser.add_argument('--hard-timeout', type=float, default=90)
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--output', default=DEFAULT_OUTPUT)
    parser.add_argument('--worker', choices=['base', 'control', 'relay'])
    parser.add_argument('--instance')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    folder = ROOT / args.output
    if folder.exists():
        raise FileExistsError(f'Refusing to overwrite experiment folder: {folder}')
    folder.mkdir(parents=True)
    sources = sorted((ROOT / 'models/routing').rglob('*.py'))
    sources += [Path(__file__).resolve(), ROOT / 'experiments/runs/run_model8_capacity_probe.py',
                ROOT / 'experiments/runs/run_model8_small_comparison.py',
                ROOT / 'config/routing/validation/zhou_large.json']
    hashes = {}
    for path in sources:
        rel = path.relative_to(ROOT)
        dest = folder / 'source' / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        hashes[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest()
    write(folder / 'manifest.json', {'settings': vars(args), 'source_sha256': hashes,
          'python': sys.version, 'selection': 'first two numbered cases of each of three scale/warehouse groups',
          'proof_scope': 'heuristic_feasible_solutions_only',
          'concurrency_note': 'At most two independent single-process ALNS runs share the host; equal wall budgets, actual iterations recorded.'})
    jobs = []
    started = time.perf_counter()

    def execute(specs):
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            pending = [pool.submit(run_job, args, *spec) for spec in specs]
            for future in as_completed(pending):
                job = future.result()
                jobs.append(job)
                write(folder / 'jobs.json', {'jobs': jobs, 'elapsed': time.perf_counter() - started})
                print(json.dumps({k: v for k, v in job.items() if k != 'command'}), flush=True)

    execute([(instance, 'base', 42) for instance in args.instances])
    ready = {job['instance'] for job in jobs if job['status'] == 'completed'}
    execute([(instance, method, seed) for instance in args.instances if instance in ready
             for seed in args.seeds for method in ('control', 'relay')])
    print(str(folder), flush=True)
    if any(job['status'] != 'completed' for job in jobs):
        raise RuntimeError('Some jobs failed or timed out; inspect jobs.json; do not treat them as zero gain')


if __name__ == '__main__':
    main()
