"""Small M5 event-cache pilot: frozen-source parity, replay and timed solves.

At most two cases; one seed; no exact solver or other numbered models. Replay
uses the same early candidate stream from the original baseline. Timed solves
do not carry trace instrumentation. All workers run serially in fresh processes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gc
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import subprocess
import sys
import time
import zipfile


ROOT = Path(__file__).resolve().parents[2]
DATASET = Path('data/formal_benchmark27_spacetime_v2')


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def case_path(name):
    size = dict(S='small', M='medium', L='large')[name[0]]
    return DATASET / 'model_5' / size / name / 'case.json'


def freeze(source, destination, cases):
    paths = [p for folder in ('models', 'tools') for p in (source / folder).rglob('*.py')]
    for name in cases:
        relative = case_path(name)
        paths.extend((source / relative.parent).glob('*.json'))
        shared = source / DATASET / 'shared' / relative.parts[-3] / name
        paths.extend(p for p in shared.iterdir() if p.suffix in ('.json', '.csv'))
    hashes = {}
    for path in sorted(set(paths)):
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        hashes[str(relative)] = hashlib.sha256(target.read_bytes()).hexdigest()
    return hashes


def worker(args):
    sys.path.insert(0, str(args.source))
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.model_5.alns.performance.cycle_cache import CycleEvaluator
    from models.routing.model_5.alns.solver import solve_alns
    from tools.configuration.loader import load_routing_case

    problem = load_instance(*load_routing_case(args.source / case_path(args.case)))
    enabled = args.variant == 'candidate'
    row = dict(mode=args.worker, case=args.case, variant=args.variant, seed=args.seed,
               customers=len({o.customer for o in problem.orders}), orders=len(problem.orders),
               satellites=len(problem.satellites), k=len(problem.operating_policy.batches),
               q=problem.total)
    if args.worker == 'trace':
        from copy import deepcopy
        states, expected = [], []
        valid = 0
        original = CycleEvaluator.__call__

        class CapturedEnough(Exception):
            pass

        def capture(self, p, state):
            nonlocal valid
            result = original(self, p, state)
            states.append(deepcopy(state))
            expected.append(digest(result))
            valid += not result[2]
            if len(states) >= args.trace_limit:
                raise CapturedEnough()
            return result

        CycleEvaluator.__call__ = capture
        begun = time.perf_counter()
        try:
            solve_alns(problem, max_time=10, max_iterations=1000000, seed=args.seed)
        except CapturedEnough:
            pass
        finally:
            CycleEvaluator.__call__ = original
        row.update(candidates=len(states), feasible_candidates=valid,
                   wall_seconds=time.perf_counter() - begun, diagnostic_only=True)
        if not states:
            raise RuntimeError('Baseline produced no evaluation trace')
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / 'trace.json.gz').write_bytes(gzip.compress(
            json.dumps(dict(states=states, expected_sha256=expected)).encode(), mtime=0))
    elif args.worker == 'replay':
        trace = json.loads(gzip.decompress(args.trace.read_bytes()))
        evaluator = (CycleEvaluator(problem, cache_e2_schedule=True) if enabled
                     else CycleEvaluator(problem))
        gc.collect()
        cpu_begun = time.process_time()
        begun = time.perf_counter()
        values = [evaluator(problem, state) for state in trace['states']]
        elapsed = time.perf_counter() - begun
        cpu_elapsed = time.process_time() - cpu_begun
        mismatches = [i for i, (value, expected) in enumerate(zip(values, trace['expected_sha256']))
                      if digest(value) != expected]
        row.update(candidates=len(values), wall_seconds=elapsed, cpu_seconds=cpu_elapsed,
                   mismatches=mismatches,
                   identical=not mismatches, round=args.round,
                   e1_hits=evaluator.hits, e1_misses=evaluator.misses,
                   e2_hits=evaluator.e2_cache.hits if enabled else 0,
                   e2_misses=evaluator.e2_cache.misses if enabled else 0)
    else:
        options = dict(max_time=args.seconds, max_iterations=10**9, seed=args.seed)
        if enabled:
            options['cache_e2_schedule'] = True
        begun = time.perf_counter()
        result = solve_alns(problem, **options)
        elapsed = time.perf_counter() - begun
        audit = audit_solution(problem, result)  # External double-cycle check, outside timer.
        write(args.output / 'result.json', result)
        write(args.output / 'audit.json', audit)
        info = result['info']
        row.update(seconds=args.seconds, iterations=info.get('iterations'),
                   objective=info.get('objective'), pool=result.get('container_pool_size'),
                   status=info.get('status'), valid=audit['valid'],
                   periodic_valid=audit.get('periodic_replay', {}).get('valid', False),
                   wall_seconds=elapsed, solver_seconds=info.get('runtime'),
                   evaluations=info.get('evaluation_cache_misses'),
                   e1_hits=info.get('e1_schedule_cache_hits', 0),
                   e1_misses=info.get('e1_schedule_cache_misses', 0),
                   e2_hits=info.get('e2_schedule_cache_hits', 0),
                   e2_misses=info.get('e2_schedule_cache_misses', 0),
                   distance_e1=audit.get('distances', {}).get('e1'),
                   distance_e2=audit.get('distances', {}).get('e2'))
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    row['peak_rss_bytes'] = rss if sys.platform == 'darwin' else rss * 1024
    write(args.output / 'summary.json', row)
    print(json.dumps(row), flush=True)
    if row.get('identical') is False:
        raise RuntimeError('Candidate evaluation differs from frozen baseline; stop pilot')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline-source', type=Path)
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--cases', nargs='+', default=['M02', 'L06'])
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--trace-limit', type=int, default=600)
    parser.add_argument('--replay-rounds', type=int, default=3)
    parser.add_argument('--worker', choices=['trace', 'replay', 'solve'], help=argparse.SUPPRESS)
    parser.add_argument('--variant', choices=['baseline', 'candidate'], default='baseline', help=argparse.SUPPRESS)
    parser.add_argument('--case', help=argparse.SUPPRESS)
    parser.add_argument('--trace', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--round', type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args()
    for field in ('output', 'source', 'baseline_source', 'trace'):
        value = getattr(args, field)
        if value is not None:
            setattr(args, field, value.resolve())
    if (args.seconds <= 0 or args.trace_limit < 1 or args.replay_rounds < 1
            or not 1 <= len(args.cases) <= 2 or len(set(args.cases)) != len(args.cases)):
        parser.error('Require positive budgets and one or two distinct cases')
    if args.worker:
        worker(args)
        return
    if args.baseline_source is None:
        parser.error('--baseline-source must name the original frozen source tree')
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('Use a new or empty output directory')
    args.output.mkdir(parents=True, exist_ok=True)
    sources = {}
    hashes = {}
    for variant, source in [('baseline', args.baseline_source), ('candidate', args.source)]:
        sources[variant] = args.output / 'sources' / variant
        hashes[variant] = freeze(source, sources[variant], args.cases)
    shutil.copy2(__file__, args.output / 'driver.py')
    archive = args.output / 'sources.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for path in sorted((args.output / 'sources').rglob('*')):
            if path.is_file():
                z.write(path, path.relative_to(args.output))
    manifest = dict(kind='two_case_pilot_not_formal_benchmark',
        created_at=datetime.now(timezone.utc).isoformat(), command=sys.argv,
        base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        branch=subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip(),
        cases=args.cases, seconds=args.seconds, seed=args.seed, iteration_limit=10**9,
        trace_limit=args.trace_limit, replay_rounds=args.replay_rounds,
        python=sys.version, executable=sys.executable, platform=platform.platform(),
        concurrency=1, python_hash_seed=0, thread_limits=1,
        source_and_input_sha256=hashes,
        source_archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    write(args.output / 'manifest.json', manifest)
    dependencies = subprocess.run([sys.executable, '-m', 'pip', 'freeze'],
                                   capture_output=True, text=True, timeout=30)
    (args.output / 'dependencies.txt').write_text(dependencies.stdout + dependencies.stderr)
    environment = {**os.environ, 'PYTHONHASHSEED': '0', **{name: '1' for name in
        ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')}}
    rows = []

    def run(mode, variant, case, round_number=0):
        destination = args.output / mode / case / (f'{round_number}_{variant}' if mode == 'replay' else variant)
        command = [sys.executable, str(Path(__file__).resolve()), '--worker', mode,
            '--variant', variant, '--case', case, '--source', str(sources[variant]),
            '--output', str(destination), '--seed', str(args.seed), '--seconds', str(args.seconds),
            '--trace-limit', str(args.trace_limit), '--round', str(round_number)]
        if mode == 'replay':
            command += ['--trace', str(args.output / 'trace' / case / 'baseline' / 'trace.json.gz')]
        destination.mkdir(parents=True, exist_ok=True)
        print(f'START {mode} {case} {variant} round={round_number}', flush=True)
        with (destination / 'worker.log').open('w') as log:
            completed = subprocess.run(command, cwd=ROOT, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT, timeout=max(60, args.seconds + 30))
        if completed.returncode:
            raise RuntimeError(f'Worker failed: {destination / "worker.log"}')
        row = json.loads((destination / 'summary.json').read_text())
        rows.append(row)
        write(args.output / 'results.json', rows)
        print(f'DONE {mode} {case} {variant}: {row}', flush=True)

    for index, case in enumerate(args.cases):
        run('trace', 'baseline', case)
        for round_number in range(args.replay_rounds):
            variants = ['baseline', 'candidate']
            for variant in variants if (index + round_number) % 2 == 0 else variants[::-1]:
                run('replay', variant, case, round_number + 1)
    # Only after both cases pass exact replay equality, run the quality comparison.
    for index, case in enumerate(args.cases):
        variants = ['baseline', 'candidate']
        for variant in variants if index % 2 == 0 else variants[::-1]:
            run('solve', variant, case)
    write(args.output / 'completion.json', dict(status='COMPLETED', workers=len(rows),
          completed_at=datetime.now(timezone.utc).isoformat(),
          all_replays_identical=all(r['identical'] for r in rows if r['mode'] == 'replay'),
          all_solutions_valid=all(r['valid'] and r['periodic_valid'] for r in rows if r['mode'] == 'solve')))


if __name__ == '__main__':
    main()
