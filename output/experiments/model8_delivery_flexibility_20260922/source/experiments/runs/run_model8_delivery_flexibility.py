"""Matched 2x2 experiment: delivery backbone freedom versus empty-box relay.

Raw demand, time windows, capacities, E1 routes and E2 vehicle labels are fixed.
Delivery and pickup assignments/orders may change only in the flexible arms.
All arms start from the same saved strong M3 route per instance.
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
from experiments.runs.run_model8_small_comparison import write

DEFAULT_OUTPUT = 'output/experiments/model8_delivery_flexibility_20260922'
INSTANCES = [f'Set2_{group}-{i}' for group in ('100-5', '100-10', '200-10') for i in (1, 2)]
ARMS = {'fixed_no_relay': ('fixed', False), 'fixed_relay': ('fixed', True),
        'flex_no_relay': ('flexible', False), 'flex_relay': ('flexible', True)}


def worker(args):
    from models.routing.alns.variants.model_8 import solve_alns
    from models.routing.variants.model_8.baseline import prepare_baseline
    from models.routing.variants.model_8.state import Evaluator
    folder = ROOT / args.output / args.instance
    base_record = json.loads((folder / 'base.json').read_text())
    p, a, b, _ = load_problem(args.instance)
    base = prepare_baseline(p, a, b, p.fleet.e2_per_sat_count,
                            (base_record['e1_routes'], base_record['e2_routes']))
    policy, relay = ARMS[args.arm]
    started = time.perf_counter()
    x, y, info = solve_alns(
        p, a, b, p.fleet.e2_per_sat_count, max_time=args.time,
        max_iterations=1000000, seed=args.seed, verbose=False, return_info=True,
        base_routes=(base_record['e1_routes'], base_record['e2_routes']),
        base_info=base_record['info'], max_handoff_receivers=3,
        search_all_receivers=True, multi_donor_pickups=True,
        delivery_policy=policy, allow_transfers=relay)
    audit = Evaluator(p, a, b, base, delivery_policy=policy).audit(info['state'])
    if (info['baseline']['route_fingerprint'] != base['route_fingerprint'] or
            x != base['e1_routes'] or y != info['state']['e2_routes'] or
            not math.isclose(audit['objective'], info['objective'], abs_tol=1e-6) or
            info['objective'] > base['objective'] + 1e-6 or
            (not relay and info['state']['transfers'])):
        raise RuntimeError('Independent solution audit failed')
    write(folder / f'{args.arm}_{args.seed}.json', {
        'e1_routes': x, 'e2_routes': y, 'info': info, 'arm': args.arm,
        'seed': args.seed, 'budget': args.time,
        'wall_seconds': time.perf_counter() - started, 'audit_valid': True})
    print(json.dumps({'instance': args.instance, 'arm': args.arm, 'seed': args.seed,
                      'objective': info['objective'],
                      'handoffs': info['transshipment_count'],
                      'delivery_move_attempts': info['delivery_move_attempts'],
                      'split_deposits': info['split_handoff_deposit_count'],
                      'multi_receiver_feasible': info['multi_receiver_feasible']}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instances', nargs='+', default=INSTANCES)
    parser.add_argument('--seeds', nargs='+', type=int, default=[42, 7, 19])
    parser.add_argument('--time', type=float, default=20)
    parser.add_argument('--output', default=DEFAULT_OUTPUT)
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--instance')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--arm', choices=list(ARMS))
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    folder = ROOT / args.output
    if folder.exists():
        raise FileExistsError('Use a new output directory; do not overwrite prior results')
    folder.mkdir(parents=True)
    inputs = []
    for instance in args.instances:
        source = ROOT / 'output/experiments/model8_vs_model3_large_20260922/strong_baseline' / instance / 'base.json'
        if not source.exists():
            source = ROOT / 'output/experiments/model8_capacity_20260922' / instance / 'base.json'
        base = json.loads(source.read_text())
        p, a, b, data_source = load_problem(instance)
        from models.routing.variants.model_8.baseline import prepare_baseline
        snapshot = prepare_baseline(p, a, b, p.fleet.e2_per_sat_count,
                                    (base['e1_routes'], base['e2_routes']))
        if not math.isclose(snapshot['objective'], base['info']['objective'], abs_tol=1e-6):
            raise RuntimeError('Baseline objective mismatch')
        write(folder / instance / 'base.json', base)
        meta = {'instance': instance, 'customers': len(p.customers),
                'base_source': str(source.relative_to(ROOT)),
                'base_source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'dataset': str(data_source.relative_to(ROOT)),
                'dataset_sha256': hashlib.sha256(data_source.read_bytes()).hexdigest(),
                'baseline_fingerprint': snapshot['route_fingerprint']}
        inputs.append(meta)
        write(folder / instance / 'input.json', meta)
    sources = sorted((ROOT / 'models/routing').rglob('*.py'))
    sources += [Path(__file__).resolve(), ROOT / 'config/routing/validation/zhou_large.json',
                ROOT / 'tests/routing/test_model8_split.py',
                ROOT / 'tests/routing/test_model8_delivery_flexibility.py',
                ROOT / 'experiments/runs/run_model8_capacity_probe.py',
                ROOT / 'experiments/runs/run_model8_small_comparison.py']
    hashes = {}
    for source in sources:
        rel = source.relative_to(ROOT)
        destination = folder / 'source' / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        hashes[str(rel)] = hashlib.sha256(source.read_bytes()).hexdigest()
    write(folder / 'manifest.json', {'settings': vars(args), 'inputs': inputs,
          'arms': ARMS, 'python': sys.version, 'source_sha256': hashes,
          'interpretation': '2x2 finite-budget heuristic comparison; fixed E1 and existing E2 vehicle labels; no global optimum claims'})
    started = time.perf_counter()

    def launch(instance, seed, arm):
        command = [sys.executable, '-m', 'experiments.runs.run_model8_delivery_flexibility',
                   '--worker', '--instance', instance, '--seed', str(seed), '--arm', arm,
                   '--time', str(args.time), '--output', args.output]
        tick = time.perf_counter()
        with (folder / instance / f'{arm}_{seed}.log').open('w') as log:
            try:
                result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                        timeout=args.time + 45, check=False)
                status = 'completed' if result.returncode == 0 else f'failed:{result.returncode}'
            except subprocess.TimeoutExpired:
                status = 'hard_timeout'
        return {'instance': instance, 'seed': seed, 'arm': arm, 'status': status,
                'wall_seconds': time.perf_counter() - tick, 'command': command}

    specs = []
    for instance in args.instances:
        for index, seed in enumerate(args.seeds):
            arms = list(ARMS)
            arms = arms[index % len(arms):] + arms[:index % len(arms)]
            specs += [(instance, seed, arm) for arm in arms]
    jobs = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(launch, *spec) for spec in specs]
        for future in as_completed(futures):
            job = future.result()
            jobs.append(job)
            write(folder / 'jobs.json', {'jobs': jobs, 'elapsed': time.perf_counter() - started})
            print(json.dumps({k: v for k, v in job.items() if k != 'command'}), flush=True)
    if any(j['status'] != 'completed' for j in jobs):
        raise RuntimeError('Incomplete batch; retain failed jobs and inspect logs')


if __name__ == '__main__':
    main()
