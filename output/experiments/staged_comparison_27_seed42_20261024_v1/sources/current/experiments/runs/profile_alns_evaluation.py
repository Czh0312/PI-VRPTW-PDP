"""Serial ALNS diagnostics on explicit cases, optionally with cProfile.

Use --source with an extracted source snapshot for before/after comparisons.
Profiled runs locate bottlenecks; compare unprofiled runs for wall-clock quality.
These selected-case diagnostics are not a formal benchmark.
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import pstats
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def worker(args):
    sys.path.insert(0, str(args.source))
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.service import solve_routing
    from tools.configuration.loader import load_routing_case

    number, name = args.cases[0].split(':')
    number = int(number)
    size = dict(S='small', M='medium', L='large')[name[0]]
    case = args.dataset / f'model_{number}' / size / name / 'case.json'
    problem = load_instance(*load_routing_case(case))
    profiler = cProfile.Profile() if args.profile else None
    begun = time.perf_counter()
    if profiler:
        profiler.enable()
    result = solve_routing(model=number, method='alns', problem=problem,
                           alns_time=args.seconds, alns_iter=args.iterations, seed=args.seed)
    if profiler:
        profiler.disable()
    wall = time.perf_counter() - begun
    # Independent final verification is outside the measured solve/profile.
    audit = audit_solution(problem, result)
    write(args.output / 'result.json', result)
    write(args.output / 'audit.json', audit)
    info = result['info']
    row = dict(model=number, case=name, seed=args.seed, seconds=args.seconds,
               iteration_limit=args.iterations, profiled=args.profile,
               valid=bool(audit['valid']), status=info.get('status'),
               objective=info.get('objective'), wall_seconds=wall,
               iterations=info.get('iterations'), pool=result.get('container_pool_size'),
               k=result.get('cycle_batch_count'), input=str(case),
               input_sha256=hashlib.sha256(case.read_bytes()).hexdigest())
    if profiler:
        profiler.dump_stats(str(args.output / 'solve.prof'))
        stats = pstats.Stats(profiler)
        functions = []
        for (filename, line, name), (primitive, calls, own, cumulative, _) in stats.stats.items():
            if '/models/' in filename:
                functions.append(dict(file=filename, line=line, function=name, calls=calls,
                                      primitive_calls=primitive, own_seconds=own,
                                      cumulative_seconds=cumulative))
        functions.sort(key=lambda item: -item['cumulative_seconds'])
        write(args.output / 'functions.json', functions)
        stream = io.StringIO()
        pstats.Stats(profiler, stream=stream).sort_stats('cumulative').print_stats(40)
        (args.output / 'profile.txt').write_text(stream.getvalue())
        row['decoder_calls'] = sum(f['calls'] for f in functions
                                   if f['function'] == 'evaluate' and f['file'].endswith('/evaluator.py'))
        row['physical_audits'] = sum(f['calls'] for f in functions
                                     if f['function'] == '_audit' and f['file'].endswith('/audit.py'))
    write(args.output / 'summary.json', row)
    print(json.dumps(row), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', nargs='+', required=True, help='MODEL:CASE, e.g. 5:S01 8:L01')
    parser.add_argument('--dataset', type=Path, default=ROOT / 'data/formal_benchmark27_uniform_7_20_50_v1')
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--baseline-source', type=Path,
                        help='Alternate serial baseline/candidate pairs using these frozen sources')
    parser.add_argument('--seconds', type=float, default=3)
    parser.add_argument('--iterations', type=int, default=10**9)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    for field in ('source', 'dataset', 'output'):
        setattr(args, field, getattr(args, field).resolve())
    if args.baseline_source is not None:
        args.baseline_source = args.baseline_source.resolve()
    if args.seconds <= 0 or args.iterations < 0:
        parser.error('Require positive seconds and nonnegative iterations')
    if args.worker:
        worker(args)
        return
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('Output must be new or empty')
    args.output.mkdir(parents=True, exist_ok=True)
    variants = {'candidate': args.source}
    if args.baseline_source is not None:
        variants = {'baseline': args.baseline_source, **variants}
    sources = {name: {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted((source / 'models').rglob('*.py'))}
               for name, source in variants.items()}
    write(args.output / 'manifest.json', dict(
        kind='selected_case_diagnostic_not_formal_benchmark',
        dataset=str(args.dataset), source=str(args.source), source_sha256=sources,
        cases=args.cases, seconds=args.seconds, iterations=args.iterations, seed=args.seed,
        profiled=args.profile, python=sys.version, platform=platform.platform(), concurrency=1,
        python_hash_seed=0, alternating_variant_order=True))
    rows = []
    environment = {**os.environ, 'PYTHONHASHSEED': '0', **{name: '1' for name in
                   ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')}}
    pairs = [(item, name, source) for index, item in enumerate(args.cases)
             for name, source in (list(variants.items()) if index % 2 == 0
                                  else list(reversed(list(variants.items()))))]
    for item, variant, source in pairs:
        number, case = item.split(':')
        destination = args.output / variant / f'm{number}_{case}'
        command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--cases', item,
                   '--source', str(source), '--dataset', str(args.dataset),
                   '--seconds', str(args.seconds), '--iterations', str(args.iterations),
                   '--seed', str(args.seed), '--output', str(destination)]
        if args.profile:
            command.append('--profile')
        completed = subprocess.run(command, cwd=ROOT, env=environment,
                                   capture_output=True, text=True, timeout=args.seconds + 120)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / 'worker.log').write_text(completed.stdout + completed.stderr)
        if completed.returncode:
            raise RuntimeError(f'{item} failed; see {destination}/worker.log')
        row = json.loads((destination / 'summary.json').read_text())
        row['variant'] = variant
        rows.append(row)
        write(args.output / 'results.json', rows)
        print(variant, item, row['objective'], row['valid'], round(row['wall_seconds'], 3), flush=True)


if __name__ == '__main__':
    main()
