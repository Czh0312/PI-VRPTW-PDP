"""Sequential frozen M5 complete route-cover LP versus integer coverage.

All variants use the same audited upper bound and original total budget.
Integer coverage certifies a stronger E2 relaxation only; partial
universes fall back to the full original integer pricing domain.
"""
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
    problem = load_instance(*load_routing_case(job['case']))
    initial = json.loads(Path(job['initial_result']).read_text())
    independent = audit_solution(problem, initial)
    if not independent['valid']:
        raise ValueError('Frozen incumbent failed audit')
    started = time.perf_counter()
    from models.routing.solvers.exact.multibatch_route_cg import route_cover_certificate
    certificate = route_cover_certificate(problem, time_limit=job['budget'], initial_result=initial,
        seed=job['seed'], pricing_time_limit=job['pricing_time_limit'], verbose=True,
        e1_time_limit=job['e1_time_limit'],
        pricing_backend='hybrid',
        integer_cover_time_limit=0 if job['variant'] == 'lp' else job['integer_cover_time_limit'],
        enumeration_time_limit=job['enumeration_time_limit'],
        enumeration_max_states=1 if job['variant'] == 'fallback' else job['enumeration_max_states'],
        diagnostics_dir=destination.parent/(job['id']+'_pricing_models'))
    lower = certificate['bound']
    payload, valid = dict(certificate=certificate), None
    upper = independent['objective']
    write(destination, dict(job=job, wall_seconds=time.perf_counter()-started,
        frozen_incumbent_audit=independent, bound=lower,
        fixed_incumbent_gap=max(0, upper-lower)/max(1e-9, abs(upper)) if lower is not None else None,
        result_valid=valid, **payload))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', nargs='+')
    parser.add_argument('--initial-results', nargs='+')
    parser.add_argument('--time', type=float, default=30)
    parser.add_argument('--pricing-time', type=float, default=2)
    parser.add_argument('--e1-time', type=float, default=10, help='E1 time cap inside the CG budget, further capped at one third of remaining time')
    parser.add_argument('--integer-time', type=float, default=15)
    parser.add_argument('--enumeration-time', type=float, default=15)
    parser.add_argument('--enumeration-states', type=int, default=2000000)
    parser.add_argument('--seeds', nargs='+', type=int, default=[42])
    parser.add_argument('--variants', nargs='+', choices=['lp', 'integer', 'fallback'], default=['lp', 'integer'])
    parser.add_argument('--worker', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(json.loads(args.worker), args.output)
        return
    if not args.cases or not args.initial_results or len(args.cases) != len(args.initial_results):
        parser.error('one --initial-results file is required per case')
    if any(not math.isfinite(v) or v <= 0 for v in (args.time, args.pricing_time, args.e1_time, args.enumeration_time, args.integer_time)):
        parser.error('budgets must be finite and positive')
    if args.enumeration_states < 1:
        parser.error('enumeration-states must be positive')
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
        problem = load_instance(config, fleet)
        if problem.model_number != 5:
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
        independent = audit_solution(problem, initial)
        if not independent['valid']:
            raise ValueError('Initial result failed independent M5 audit')
        write(frozen/'initial_result.json', initial)
        write(frozen/'initial_audit.json', independent)
        for seed in args.seeds:
            for variant in args.variants:
                jobs.append(dict(id=f'{index:02d}_{seed}_{variant}', variant=variant,
                    case=str(frozen/'case.json'), source_case=source, initial_result=str(frozen/'initial_result.json'),
                    frozen_upper_bound=independent['objective'], budget=args.time, seed=seed,
                    pricing_time_limit=args.pricing_time, e1_time_limit=args.e1_time,
                    integer_cover_time_limit=args.integer_time, enumeration_time_limit=args.enumeration_time, enumeration_max_states=args.enumeration_states, orders=len(problem.orders),
                    customers=len({o.customer for o in problem.orders})))
    for source in [*ROOT.glob('models/**/*.py'), *ROOT.glob('tools/configuration/*.py'), ROOT/'tools/__init__.py', Path(__file__).resolve()]:
        target = output/'source_snapshot'/source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for name in ('multibatch_route_integer_cover.py', 'multibatch_route_enumeration.py', 'multibatch_route_cg.py', 'multibatch_route_pricing.py', 'multibatch_bounds.py', 'solver.py', 'formulation.py'):
        path = output/'source_snapshot/models/routing/solvers/exact'/name
        compile(path.read_text(), str(path), 'exec')
    hashes = {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(output.rglob('*')) if p.is_file()}
    write(output/'manifest.json', dict(jobs=jobs, artifact_sha256=hashes, python=sys.version,
        platform=platform.platform(), python_hash_seed=0,
        note='Same audited frozen upper bound. Enumeration, full LP, integer cover and fallback all share the original budget. No integer M5 proof.'))
    summary = []
    frozen_worker = output/'source_snapshot/tools/audit/benchmark_multibatch_integer_cover.py'
    for job in jobs:
        destination = output/(job['id']+'.json')
        started = time.perf_counter()
        with (output/(job['id']+'.log')).open('w') as log:
            try:
                run = subprocess.run([sys.executable, str(frozen_worker), '--worker', json.dumps(job),
                    '--output', str(destination)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                    timeout=args.time+20, env={**os.environ, 'PYTHONHASHSEED': '0'})
                failure = f'EXIT_{run.returncode}' if run.returncode else None
            except subprocess.TimeoutExpired:
                failure = 'OUTER_TIMEOUT'
        row = dict(job=job, failure=failure, outer_seconds=time.perf_counter()-started)
        if destination.exists():
            payload = json.loads(destination.read_text())
            row.update(bound=payload['bound'], fixed_incumbent_gap=payload['fixed_incumbent_gap'],
                       wall_seconds=payload['wall_seconds'], result_valid=payload['result_valid'])
            if job['variant'] in ('lp', 'integer', 'fallback'):
                row.update(status=payload['certificate']['status'],
                    e1_bound=payload['certificate']['e1_bound'],
                    e1_certificate=payload['certificate'].get('e1_certificate'),
                    sites=[{key: s[key] for key in ('satellite', 'bound', 'status', 'lp_converged', 'runtime')}
                           for s in payload['certificate']['sites']])
            else:
                row['info'] = payload['result']['info']
        summary.append(row)
        write(output/'summary.json', summary)
        print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
