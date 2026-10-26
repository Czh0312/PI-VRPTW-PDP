"""One-case comparison of stock-first and cost-aware chain repair, plus common-start diagnostics."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
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
CASE = Path('data/formal_benchmark27_spacetime_v2/model_5/medium/M02/case.json')


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def worker(args):
    sys.path.insert(0, str(args.source))
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.model_5.alns.solver import solve_alns
    from tools.configuration.loader import load_routing_case

    problem = load_instance(*load_routing_case(args.source / CASE))
    if args.worker == 'mechanism':
        from models.routing.model_5.alns.evaluator import EvaluationCache
        from models.routing.model_5.alns.performance.cycle_cache import CycleEvaluator
        from models.routing.model_5.alns.stock_chain import CriticalStockRepair, stock_requirements
        from models.routing.model_5.alns.state import state_from_result
        from models.common.cycle.precheck import check_necessary_conditions

        anchor = json.loads(args.reference.read_text())
        anchor_audit = audit_solution(problem, anchor)
        if not anchor_audit['valid']:
            raise RuntimeError('Reference schedule is not independently feasible')
        state = state_from_result(problem, anchor)
        cached = EvaluationCache(problem, CycleEvaluator(problem))
        score, decoded, issues = cached(state)
        if issues or not audit_solution(problem, decoded)['valid']:
            raise RuntimeError('Reference could not be decoded into a feasible state')
        best_pool = decoded
        records = []

        def recorded(proposed):
            nonlocal best_pool
            value = cached(proposed)
            cost, result, problems = value
            row = dict(cost=cost, violations=problems)
            if not problems:
                row.update(pool=result['container_pool_size'], requirements=stock_requirements(problem, result))
                if (result['container_pool_size'], cost) < (best_pool['container_pool_size'], best_pool['info']['objective']):
                    best_pool = result
            records.append(row)
            return value

        repair_class = CriticalStockRepair
        if args.variant == 'candidate':
            from models.routing.model_5.alns.cost_chain import CostAwareStockRepair
            repair_class = CostAwareStockRepair
        repair = repair_class(problem, lower_bound=check_necessary_conditions(problem)['pool_lower_bound'])
        begun = time.perf_counter()
        proposed = repair.propose(state, decoded, recorded, begun + 2)
        elapsed = time.perf_counter() - begun
        final = cached(proposed)[1] if proposed is not None else decoded
        final_audit = audit_solution(problem, final)
        lowest_audit = audit_solution(problem, best_pool)
        row = dict(kind='mechanism', case='M02', variant=args.variant, diagnostic_only=True,
            reference_objective=anchor['info']['objective'], reconstructed_objective=score,
            initial_pool=decoded['container_pool_size'], final_pool=final['container_pool_size'],
            final_objective=final['info']['objective'], wall_seconds=elapsed,
            valid=final_audit['valid'], periodic_valid=final_audit['periodic_replay']['valid'],
            lowest_pool_valid=lowest_audit['valid'] and lowest_audit['periodic_replay']['valid'],
            details=repair.last_run)
        write(args.output / 'evaluation_trace.json', records)
        write(args.output / 'lowest_pool_result.json', best_pool)
        write(args.output / 'lowest_pool_audit.json', lowest_audit)
        write(args.output / 'result.json', final)
        write(args.output / 'audit.json', final_audit)
    else:
        options = dict(max_time=args.seconds, max_iterations=10**9, seed=args.seed, critical_stock_repair=True)
        if args.variant == 'candidate':
            options['stock_chain_strategy'] = 'cost_aware'
        cpu = time.process_time()
        begun = time.perf_counter()
        result = solve_alns(problem, **options)
        elapsed = time.perf_counter() - begun
        cpu_elapsed = time.process_time() - cpu
        audit = audit_solution(problem, result)
        info = result['info']
        row = dict(kind='quality', case='M02', variant=args.variant, seed=args.seed,
            seconds=args.seconds, objective=info.get('objective'), pool=result.get('container_pool_size'),
            valid=audit['valid'], periodic_valid=audit.get('periodic_replay', {}).get('valid', False),
            status=info.get('status'), wall_seconds=elapsed, cpu_seconds=cpu_elapsed,
            costs=result.get('cost_components'), trips=dict(e1=len(result['e1_trips']), e2=len(result['e2_trips'])),
            iterations=info.get('iterations'), evaluations=info.get('evaluation_cache_misses'),
            distance_e1=audit.get('distances', {}).get('e1'), distance_e2=audit.get('distances', {}).get('e2'),
            stock_chain={k: v for k, v in info.items() if k.startswith('stock_chain_')},
            e2_schedule_cache_enabled=info.get('e2_schedule_cache_enabled', False))
        write(args.output / 'result.json', result)
        write(args.output / 'audit.json', audit)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    row['peak_rss_bytes'] = rss if sys.platform == 'darwin' else rss * 1024
    write(args.output / 'summary.json', row)
    print(json.dumps(row, ensure_ascii=False), flush=True)
    if not row['valid']:
        raise RuntimeError('Independent audit failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--baseline-source', type=Path)
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--reference', type=Path, required=True,
        help='Audited M02 schedule used only for the separate mechanism check')
    parser.add_argument('--worker', choices=['quality', 'mechanism'], help=argparse.SUPPRESS)
    parser.add_argument('--variant', choices=['baseline', 'candidate'], help=argparse.SUPPRESS)
    args = parser.parse_args()
    for name in ('output', 'baseline_source', 'source', 'reference'):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.resolve())
    if args.seconds <= 0:
        parser.error('Positive time budget required')
    if args.worker:
        worker(args)
        return
    if args.baseline_source is None or (args.output.exists() and any(args.output.iterdir())):
        parser.error('Require original --baseline-source and a new/empty --output')
    sys.path.insert(0, str(ROOT))
    from experiments.runs.pilot_m5_event_cache import freeze

    args.output.mkdir(parents=True, exist_ok=True)
    sources, hashes = {}, {}
    for variant, source in [('baseline', args.baseline_source), ('candidate', args.source)]:
        sources[variant] = args.output / 'sources' / variant
        hashes[variant] = freeze(source, sources[variant], ['M02'])
    reference = args.output / 'inputs' / 'anchor_result.json'
    reference.parent.mkdir()
    shutil.copy2(args.reference, reference)
    shutil.copy2(__file__, args.output / 'driver.py')
    shutil.copy2(ROOT / 'experiments/runs/pilot_m5_event_cache.py', args.output / 'freeze_helper.py')
    with zipfile.ZipFile(args.output / 'sources.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for path in sorted((args.output / 'sources').rglob('*')):
            if path.is_file():
                z.write(path, path.relative_to(args.output))
    write(args.output / 'manifest.json', dict(kind='single_case_pilot', case='M02',
        created_at=datetime.now(timezone.utc).isoformat(), command=sys.argv,
        base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        branch=subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip(),
        python=sys.version, executable=sys.executable, platform=platform.platform(),
        seconds=args.seconds, seed=args.seed, iteration_limit=10**9, concurrency=1,
        python_hash_seed=0, thread_limits=1, e2_schedule_cache_enabled=False,
        variant_order=['baseline', 'candidate'], strategies=dict(baseline='stock_first', candidate='cost_aware'),
        source_and_input_sha256=hashes,
        anchor_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
        driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        quality_warm_start=False, mechanism_time_limit=2,
        repair_limits=dict(beam_width=2, max_depth=4, max_evaluations=192, max_neighbors=32,
                           invocation_period=150, call_seconds=0.4, total_time_fraction=0.15)))
    dependencies = subprocess.run([sys.executable, '-m', 'pip', 'freeze'], capture_output=True, text=True, timeout=30)
    (args.output / 'dependencies.txt').write_text(dependencies.stdout + dependencies.stderr)
    environment = {**os.environ, 'PYTHONHASHSEED': '0', **{name: '1' for name in
        ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')}}
    rows = []
    for kind, variant in [('quality', 'baseline'), ('quality', 'candidate'), ('mechanism', 'baseline'), ('mechanism', 'candidate')]:
        destination = args.output / kind / variant
        destination.mkdir(parents=True)
        command = [sys.executable, str(Path(__file__).resolve()), '--worker', kind,
            '--variant', variant, '--source', str(sources[variant]), '--output', str(destination),
            '--reference', str(reference), '--seconds', str(args.seconds), '--seed', str(args.seed)]
        print(f'START {kind} {variant}', flush=True)
        with (destination / 'worker.log').open('w') as log:
            completed = subprocess.run(command, cwd=ROOT, env=environment, stdout=log,
                stderr=subprocess.STDOUT, timeout=max(60, args.seconds + 30))
        if completed.returncode:
            raise RuntimeError(f'Worker failed; see {destination / "worker.log"}')
        row = json.loads((destination / 'summary.json').read_text())
        rows.append(row)
        write(args.output / 'results.json', rows)
        print(f'DONE {kind} {variant}: objective={row.get("objective", row.get("final_objective"))}, valid={row["valid"]}', flush=True)
    write(args.output / 'completion.json', dict(status='COMPLETED', workers=len(rows),
        all_audits_valid=all(r['valid'] and r.get('periodic_valid', True) and r.get('lowest_pool_valid', True) for r in rows)))


if __name__ == '__main__':
    main()
