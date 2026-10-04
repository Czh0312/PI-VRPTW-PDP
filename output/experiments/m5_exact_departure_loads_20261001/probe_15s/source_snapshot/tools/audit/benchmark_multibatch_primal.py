"""Sequential M5 seed/formulation ablation with frozen inputs and source.

Primal mode disables only primal construction in its control arm. Formulation
mode disables the newer capacity cuts, pruning and order-level cutoff sharing.
Incumbent mode compares the original seed portfolio with insertion/E1 merging.
Root mode compares indicator/rectangular routes with linear E2 links and trip symmetry.
Routes mode compares indexed internal arcs with adaptive sharing and route identity.
Loads mode compares repeated departure-load sums with their factored variables.
Both arms use the same total budget and seed; this is not a historical snapshot.
Optional --initial-results freezes an audited incumbent for each case in both arms.
This small diagnostic does not establish large-instance speed improvements.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DEFAULT_CASES = [
    'config/cases/multibatch_demo.json',
    'data/multi_batch/small/mechanism_forced_late/b2/case.json',
    'data/multi_batch/small/mechanism_cutoff_choice/b3/case.json',
    'data/multi_batch/small/zhou_Set1_7-2-1/b2/case.json',
    'data/multi_batch/small/zhou_Set1_7-2-1/b3/case.json',
    'data/multi_batch/small/zhou_Set1_10-2-1/b3/case.json',
    'data/multi_batch/small/lade_0823_10aoi/b3/case.json',
    'data/multi_batch/medium/zhou_Set2_100-5-1b_subset20/b3/case.json',
]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def worker(job, output):
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.exact import solver
    problem = load_instance(*load_routing_case(job['case']))
    initial = json.loads(Path(job['initial_result']).read_text()) if job.get('initial_result') else None
    begun = time.perf_counter()
    with ExitStack() as stack:
        if not job['warm_start']:
            stack.enter_context(patch.object(solver, 'construct_incumbent', return_value=None))
        if job.get('primal_improve') is False:
            from models.routing.solvers.exact import multibatch_primal
            original_primal = multibatch_primal.construct_multibatch_incumbent
            stack.enter_context(patch.object(multibatch_primal, 'construct_multibatch_incumbent',
                side_effect=lambda *a, **kw: original_primal(*a, **kw, improve=False)))
        options = job.get('build_options', {})
        if options:
            original = solver.build_model
            stack.enter_context(patch.object(solver, 'build_model',
                side_effect=lambda *a, **kw: original(*a, **kw, **options)))
        result = solver.solve_exact(problem, time_limit=job['budget'], seed=job['seed'],
                                    verbose=True, initial_result=initial)
    wall = time.perf_counter()-begun
    write(output, dict(job=job, wall_seconds=wall, result=result,
                       independent_audit=audit_solution(problem, result)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', nargs='+', default=DEFAULT_CASES)
    parser.add_argument('--time', type=float, default=5)
    parser.add_argument('--seeds', type=int, nargs='+', default=[42])
    parser.add_argument('--mode', choices=['primal', 'formulation', 'incumbent', 'root', 'routes', 'loads'], default='primal')
    parser.add_argument('--initial-results', nargs='+',
                        help='one audited result (raw or benchmark payload) per case, frozen for both arms')
    parser.add_argument('--worker', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        worker(json.loads(args.worker), args.output)
        return
    if not math.isfinite(args.time) or args.time <= 0:
        parser.error('--time must be finite and positive')
    if args.initial_results and len(args.initial_results) != len(args.cases):
        parser.error('--initial-results must supply exactly one result per case')
    if args.initial_results and args.mode in ('primal', 'incumbent'):
        parser.error('--initial-results is only supported for formulation, root, routes and loads ablation')
    from configuration.loader import load_routing_case, resolve_project_path
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    jobs = []
    for index, path in enumerate(args.cases):
        config, fleet = load_routing_case(path)
        problem = load_instance(config, fleet)
        if problem.model_number != 5:
            raise ValueError(f'M5 case required: {path}')
        frozen = output/'inputs'/str(index)
        frozen.mkdir(parents=True)
        for field in ('data', 'orders', 'distance_matrix', 'duration_matrix', 'dataset_metadata'):
            if config.get(field):
                source = resolve_project_path(config[field])
                target = frozen/(field+source.suffix)
                shutil.copy2(source, target)
                config[field] = str(target)
        write(frozen/'fleet.json', fleet)
        config['routing_config'] = str(frozen/'fleet.json')
        config['operating_policy'] = fleet['operating_policy']
        for field in ('algorithm_config', 'container_pool_config'):
            config.pop(field, None)
        write(frozen/'case.json', config)
        initial_options = {}
        if args.initial_results:
            initial_source = resolve_project_path(args.initial_results[index])
            payload = json.loads(initial_source.read_text())
            initial = payload.get('result', payload)
            independent = audit_solution(problem, initial)
            if not independent['valid']:
                raise ValueError(f'Initial result failed independent audit: {initial_source}')
            write(frozen/'initial_result.json', initial)
            write(frozen/'initial_audit.json', independent)
            initial_options = dict(initial_result=str(frozen/'initial_result.json'),
                                   initial_source=str(initial_source),
                                   initial_objective=initial['info']['objective'])
        variants = {
            'primal': [('cold', False, {}), ('warm', True, {})],
            'formulation': [('reference', True, dict(strengthen_multibatch=False, share_cutoff=False)),
                            ('strengthened', True, {})],
            'incumbent': [('reference', True, {}), ('improved', True, {})],
            'root': [('reference', True, dict(linear_e2_links=False, trip_symmetry=False)),
                     ('compact', True, {})],
            'routes': [('reference', True, dict(compress_multibatch_routes=False)),
                       ('adaptive', True, {})],
            'loads': [('reference', True, dict(factor_e2_loads=False)),
                      ('factored', True, dict(factor_e2_loads=True))],
        }[args.mode]
        for seed in args.seeds:
            for variant, warm, options in variants:
                jobs.append(dict(id=f'{index:02d}_{seed}_{variant}', variant=variant, build_options=options,
                                 source_case=path, case=str(frozen/'case.json'), warm_start=warm,
                                 **initial_options,
                                 budget=args.time, seed=seed,
                                 primal_improve=not (args.mode == 'incumbent' and variant == 'reference'),
                                 orders=len(problem.orders),
                                 customers=len({o.customer for o in problem.orders}),
                                 batch_count=len(problem.operating_policy.batches)))
    sources = [*ROOT.glob('models/**/*.py'), *ROOT.glob('configuration/*.py'), Path(__file__).resolve()]
    for source in sources:
        target = output/'source_snapshot'/source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    # Shared source files may be edited by another active chat. Check the
    # frozen solver before dispatching workers, including duplicate keywords
    # (which ast.parse alone does not reject).
    for name in ('formulation.py', 'solver.py', 'multibatch_cuts.py', 'primal.py', 'multibatch_primal.py'):
        path = output/'source_snapshot/models/routing/solvers/exact'/name
        compile(path.read_text(), str(path), 'exec')
    hashes = {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(output.rglob('*')) if p.is_file()}
    write(output/'manifest.json', dict(purpose=f'M5_{args.mode}_ablation_diagnostic', jobs=jobs,
        python=sys.version, platform=platform.platform(), artifact_sha256=hashes,
        python_hash_seed=0, note='Same integer feasible set. No ALNS. Total budget includes primal and model build.',
        initial_result_note='Both arms receive the same independently audited frozen incumbent; own primal remains enabled.'
                            if args.initial_results else 'No supplied incumbent; each worker constructs its own timed primal.'))
    records = []
    frozen_worker = output/'source_snapshot'/'tools/audit/benchmark_multibatch_primal.py'
    for job in jobs:
        destination = output/(job['id']+'.json')
        begun = time.perf_counter()
        with (output/(job['id']+'.log')).open('w') as log:
            try:
                completed = subprocess.run([sys.executable, str(frozen_worker), '--worker',
                    json.dumps(job), '--output', str(destination)], cwd=ROOT, stdout=log,
                    stderr=subprocess.STDOUT, timeout=args.time+20, env={**os.environ, 'PYTHONHASHSEED': '0'})
                failed = f'EXIT_{completed.returncode}' if completed.returncode else None
            except subprocess.TimeoutExpired:
                failed = 'OUTER_TIMEOUT'
        record = dict(job=job, outer_seconds=time.perf_counter()-begun, failure=failed)
        if destination.exists():
            payload = json.loads(destination.read_text())
            record.update(info=payload['result']['info'], valid=payload['independent_audit']['valid'],
                          wall_seconds=payload['wall_seconds'])
        records.append(record)
        write(output/'summary.json', records)
        print(json.dumps(dict(id=job['id'], failure=failed, valid=record.get('valid'),
                              info=record.get('info')), ensure_ascii=False), flush=True)
    return records


if __name__ == '__main__':
    main()
