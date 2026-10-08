"""Frozen M5 restricted E2 route-pool improvements and same-budget comparisons."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def worker(job, destination):
    from tools.configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.variants.model_5.exact import reoptimize_route_pool, route_cover_certificate, solve_exact
    p = load_instance(*load_routing_case(job['case']))
    initial = json.loads(Path(job['initial_result']).read_text())
    assert audit_solution(p, initial)['valid']
    started = time.perf_counter()
    variant, cert = job['variant'], None
    if variant in ('milp', 'pool-milp'):
        result = solve_exact(p, time_limit=job['budget'], seed=job['seed'], verbose=True,
            initial_result=initial, distance_cuts='components',
            route_pool_time=job['polish_time'] if variant == 'pool-milp' else 0)
        bound = result['info']['best_bound']
    else:
        result = initial
        if variant in ('reorder', 'pool', 'pool-cg'):
            allowance = min(job['polish_time'], job['budget']/3) if variant == 'pool-cg' else job['budget']
            result = reoptimize_route_pool(p, initial, time_limit=allowance, seed=job['seed'], verbose=True,
                transfers=variant != 'reorder', diagnostics_dir=destination.parent/(job['id']+'_pool_models'))
        bound = None
        if variant in ('cg', 'pool-cg'):
            cert = route_cover_certificate(p, time_limit=max(1e-12, job['budget']-(time.perf_counter()-started)),
                initial_result=result, seed=job['seed'], e1_time_limit=10, verbose=True,
                diagnostics_dir=destination.parent/(job['id']+'_pricing_models'))
            bound = cert['bound']
    audited = audit_solution(p, result)
    assert audited['valid']
    upper = audited['objective']
    write(destination, dict(job=job, wall_seconds=time.perf_counter()-started, result=result,
        independent_audit=audited, certificate=cert, bound=bound,
        gap=max(0, upper-bound)/max(1e-9, abs(upper)) if bound is not None else None,
        fixed_initial_gap=max(0, job['initial_upper']-bound)/max(1e-9, abs(job['initial_upper'])) if bound is not None else None))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', nargs='+')
    parser.add_argument('--initial-results', nargs='+')
    parser.add_argument('--time', type=float, default=10)
    parser.add_argument('--polish-time', type=float, default=10)
    parser.add_argument('--seeds', nargs='+', type=int, default=[42])
    parser.add_argument('--variants', nargs='+', choices=['reorder', 'pool', 'cg', 'pool-cg', 'milp', 'pool-milp'],
                        default=['reorder', 'pool'])
    parser.add_argument('--worker', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(json.loads(args.worker), args.output)
        return
    if not args.cases or not args.initial_results or len(args.cases) != len(args.initial_results):
        parser.error('one --initial-results file is required per case')
    if any(not math.isfinite(v) or v <= 0 for v in (args.time, args.polish_time)):
        parser.error('budgets must be finite and positive')
    if len(set(args.variants)) != len(args.variants):
        parser.error('variants must be distinct')
    from tools.configuration.loader import load_routing_case, resolve_project_path
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    jobs = []
    for index, source in enumerate(args.cases):
        config, fleet = load_routing_case(source)
        p = load_instance(config, fleet)
        if p.model_number != 5:
            raise ValueError('M5 inputs required')
        frozen = output/'inputs'/str(index)
        frozen.mkdir(parents=True)
        for field in ('data', 'orders', 'distance_matrix', 'duration_matrix', 'dataset_metadata'):
            if config.get(field):
                path = resolve_project_path(config[field])
                target = frozen/(field+path.suffix)
                shutil.copy2(path, target)
                config[field] = str(target)
        write(frozen/'fleet.json', fleet)
        config['routing_config'], config['operating_policy'] = str(frozen/'fleet.json'), fleet['operating_policy']
        for field in ('algorithm_config', 'container_pool_config'):
            config.pop(field, None)
        write(frozen/'case.json', config)
        payload = json.loads(resolve_project_path(args.initial_results[index]).read_text())
        initial = payload.get('result', payload)
        a = audit_solution(p, initial)
        assert a['valid']
        write(frozen/'initial_result.json', initial)
        write(frozen/'initial_audit.json', a)
        for seed in args.seeds:
            for variant in args.variants:
                jobs.append(dict(id=f'{index:02d}_{seed}_{variant}', variant=variant,
                    case=str(frozen/'case.json'), source_case=source, initial_result=str(frozen/'initial_result.json'),
                    initial_upper=a['objective'], budget=args.time, polish_time=args.polish_time,
                    seed=seed, customers=len({o.customer for o in p.orders})))
    for source in [*ROOT.glob('models/**/*.py'), *ROOT.glob('tools/configuration/*.py'), ROOT/'tools/__init__.py', Path(__file__).resolve()]:
        target = output/'source_snapshot'/source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for name in ('multibatch_route_pool.py', 'multibatch_e1_polish.py', 'multibatch_e1_inventory.py', 'multibatch_e1_routes.py',
                 'multibatch_route_cg.py', 'multibatch_route_pricing.py', 'solver.py', 'formulation.py'):
        path = output/'source_snapshot/models/routing/solvers/exact'/name
        compile(path.read_text(), str(path), 'exec')
    compile(Path(__file__).read_text(), str(Path(__file__)), 'exec')
    hashes = {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(output.rglob('*')) if p.is_file()}
    write(output/'manifest.json', dict(jobs=jobs, artifact_sha256=hashes, python=sys.version,
        platform=platform.platform(), python_hash_seed=0,
        note='Restricted route-pool primal search retains actual full M5 feasibility. Search is within the original total budget. '
             'Restricted subproblem bounds/optimality never certify full M5.'))
    summary = []
    worker_path = output/'source_snapshot/tools/audit/benchmark_multibatch_route_pool.py'
    for job in jobs:
        destination = output/(job['id']+'.json')
        with (output/(job['id']+'.log')).open('w') as log:
            run = subprocess.run([sys.executable, str(worker_path), '--worker', json.dumps(job), '--output', str(destination)],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=args.time+20,
                env={**os.environ, 'PYTHONHASHSEED': '0'})
        row = dict(job=job, failure=None if run.returncode == 0 else f'EXIT_{run.returncode}')
        if destination.exists():
            data = json.loads(destination.read_text())
            row.update(upper=data['independent_audit']['objective'], bound=data['bound'], gap=data['gap'],
                fixed_initial_gap=data['fixed_initial_gap'], wall_seconds=data['wall_seconds'],
                valid=data['independent_audit']['valid'], info=data['result']['info'])
        summary.append(row)
        write(output/'summary.json', summary)
        print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
