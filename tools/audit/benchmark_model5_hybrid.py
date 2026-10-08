"""Fresh M5 hybrid runs: freeze inputs/code first, retain all audits and failures."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
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
    from models.routing.variants.model_5.exact import solve_hybrid
    problem = load_instance(*load_routing_case(job['case'], use_pool_config=False))
    start = time.perf_counter()
    experiment = solve_hybrid(problem, **job['options'], verbose=True,
        diagnostics_dir=destination.parent/(job['id']+'_models'))
    independent = audit_solution(problem, experiment['result'])
    write(destination, dict(job=job, experiment=experiment, independent_audit=independent,
        wall_seconds=time.perf_counter()-start))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--register-only', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--jobs', nargs='+', type=int)
    parser.add_argument('--worker', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(json.loads(args.worker), args.output)
        return
    output = args.output.resolve()
    if not args.resume:
        if args.plan is None:
            parser.error('--plan is required for registration')
        from tools.configuration.loader import load_routing_case, resolve_project_path
        from models.routing.common.cycle import load_instance
        plan = json.loads(args.plan.read_text())
        output.mkdir(parents=True, exist_ok=False)
        dataset = json.loads((ROOT/'data/manifest.json').read_text())
        jobs = []
        for family in plan['families']:
            row = next(f for f in dataset['families'] if f['family_id'] == family)
            source_case = row['cases']['b'+str(plan['batch_count'])]
            config, fleet = load_routing_case(source_case, use_pool_config=False)
            problem = load_instance(config, fleet)
            total = sum(o.count for o in problem.orders)
            minimum = max(problem.batch_demands.values())
            for label in plan['pool_labels']:
                if label not in ('full', 'tight'):
                    raise ValueError('Unknown pool label')
                frozen = output/'inputs'/(family+'_'+label)
                frozen.mkdir(parents=True)
                cfg = dict(config)
                for field in ('data', 'orders', 'distance_matrix', 'duration_matrix', 'dataset_metadata'):
                    if cfg.get(field):
                        source = resolve_project_path(cfg[field])
                        target = frozen/(field+source.suffix)
                        shutil.copy2(source, target)
                        cfg[field] = str(target)
                pool = total if label == 'full' else max(minimum, math.ceil(total*.5))
                policy = {**fleet['operating_policy'], 'container_pool_size': pool}
                write(frozen/'fleet.json', {**fleet, 'operating_policy': policy})
                cfg.update(routing_config=str(frozen/'fleet.json'), operating_policy=policy)
                for field in ('algorithm_config', 'container_pool_config'):
                    cfg.pop(field, None)
                write(frozen/'case.json', cfg)
                for seed in plan['seeds']:
                    options = {k: plan[k] for k in ('alns_time_limit', 'primal_time_limit', 'cover_time_limit',
                        'conditional_time_limit', 'target_gap', 'max_subset_cuts_per_batch', 'max_conditions')}
                    options.update(time_limit=plan['total_time_limit'], seed=seed)
                    jobs.append(dict(id=f'{len(jobs):02d}_{family}_{label}_{seed}', case=str(frozen/'case.json'),
                        family=family, pool_label=label, pool=pool, options=options,
                        orders=len(problem.orders), customers=len({o.customer for o in problem.orders})))
        for source in [*ROOT.glob('models/**/*.py'), *ROOT.glob('tools/configuration/*.py'), ROOT/'tools/__init__.py', Path(__file__).resolve()]:
            target = output/'source_snapshot'/source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        hashes = {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(output.rglob('*')) if p.is_file()}
        write(output/'manifest.json', dict(plan=plan, jobs=jobs, artifact_sha256=hashes,
            registered_before_run=True, python=sys.version, python_hash_seed=0,
            note='Fresh ALNS for every job. Fixed total budget; no reuse of prior solutions or bounds. All failures retained.'))
    manifest = json.loads((output/'manifest.json').read_text())
    for name, expected in manifest['artifact_sha256'].items():
        if hashlib.sha256((output/name).read_bytes()).hexdigest() != expected:
            raise ValueError('Registered input/source changed: '+name)
    if args.register_only:
        print(f'Registered {len(manifest["jobs"])} fresh runs', flush=True)
        return
    summary = json.loads((output/'summary.json').read_text()) if (output/'summary.json').exists() else []
    done = {r['job']['id'] for r in summary}
    harness = output/'source_snapshot/tools/audit/benchmark_model5_hybrid.py'
    for index, job in enumerate(manifest['jobs']):
        if job['id'] in done or (args.jobs is not None and index not in args.jobs):
            continue
        destination = output/(job['id']+'.json')
        start = time.perf_counter()
        with (output/(job['id']+'.log')).open('w') as log:
            try:
                run = subprocess.run([sys.executable, str(harness), '--worker', json.dumps(job),
                    '--output', str(destination)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                    timeout=job['options']['time_limit']+20, env={**os.environ, 'PYTHONHASHSEED': '0'})
                failure = f'EXIT_{run.returncode}' if run.returncode else None
            except subprocess.TimeoutExpired:
                failure = 'OUTER_TIMEOUT'
        row = dict(job=job, failure=failure, outer_seconds=time.perf_counter()-start)
        if destination.exists():
            payload = json.loads(destination.read_text())
            experiment = payload['experiment']
            row.update(bound=experiment['bound'], upper_bound=experiment['upper_bound'], gap=experiment['gap'],
                target_reached=experiment['target_reached'], status=experiment['status'],
                valid=payload['independent_audit']['valid'],
                periodic=payload['independent_audit'].get('periodic_replay', {}).get('valid'),
                wall_seconds=payload['wall_seconds'], stages=experiment['stages'])
        summary.append(row)
        write(output/'summary.json', summary)
        print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
