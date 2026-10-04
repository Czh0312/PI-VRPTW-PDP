"""Sequential M5 before/after benchmark with frozen inputs and source snapshots.

Every phase uses the registered jobs and a fresh process per solve. Failed runs
are retained. Pilot filters never change the registered final comparison.
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
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def hashes(folder):
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def register(out):
    from configuration.loader import load_routing_case, resolve_project_path
    dataset = json.loads((ROOT/'data/manifest.json').read_text())
    families = {'zhou_Set2_100-5-1b_subset50': 'development',
                'zhou_Set2_100-5-1b': 'development',
                'zhou_Set2_100-5-2_subset50': 'held_out',
                'zhou_Set2_100-5-2': 'held_out'}
    frozen = out/'inputs'
    frozen.mkdir()
    jobs = []
    for case in dataset['cases']:
        if case['family_id'] not in families or case['group'] != 'multi_batch':
            continue
        config, fleet = load_routing_case(case['case_path'], use_pool_config=False)
        folder = frozen/case['case_id']
        folder.mkdir()
        for field in ('data', 'orders', 'distance_matrix', 'duration_matrix'):
            if config.get(field):
                source = resolve_project_path(config[field])
                target = folder/(field+source.suffix)
                shutil.copy2(source, target)
                config[field] = str(target)
        write(folder/'fleet.json', fleet)
        config['routing_config'] = str(folder/'fleet.json')
        config['operating_policy'] = fleet['operating_policy']
        for field in ('algorithm_config', 'container_pool_config', 'container_pool_size'):
            config.pop(field, None)
        write(folder/'case.json', config)
        total = case['total_container_uses']
        tight = max(max(case['batch_demands'].values()), math.ceil(total*.5))
        for pool_label, pool in [('full', total), ('tight', tight)]:
            budgets = [5.0, 15.0] if case['customer_count'] == 100 and case['batch_count'] == 3 else [5.0]
            for budget in budgets:
                for seed in (42, 43, 44):
                    jobs.append(dict(case_id=case['case_id'], family=case['family_id'],
                        split=families[case['family_id']], customers=case['customer_count'],
                        batches=case['batch_count'], total=total, pool=pool, pool_label=pool_label,
                        case=str(folder/'case.json'), budget=budget, iterations=100000,
                        seed=seed, model=5, solver='alns'))
    write(out/'benchmark_manifest.json', dict(
        purpose='M5 distance, audited feasibility, and seed dispersion under equal budgets',
        scope='Engineering benchmark on two mother instances; not a global optimality claim',
        seeds=[42, 43, 44], jobs=jobs, job_count=len(jobs),
        total_solver_seconds_per_phase=sum(j['budget'] for j in jobs),
        created=time.strftime('%Y-%m-%dT%H:%M:%S'), python=sys.version,
        platform=platform.platform(), input_sha256=hashes(frozen),
        dataset_manifest_sha256=hashlib.sha256((ROOT/'data/manifest.json').read_bytes()).hexdigest()))


def worker(job, destination):
    from configuration.loader import load_routing_case, apply_container_pool_size
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.service import solve_routing
    config, fleet = load_routing_case(job['case'], use_pool_config=False)
    config, fleet = apply_container_pool_size(config, fleet, job['pool'])
    problem = load_instance(config, fleet)
    begun = time.perf_counter()
    result = solve_routing(model=5, method='alns', problem=problem,
        alns_time=job['budget'], alns_iter=job['iterations'], seed=job['seed'])
    elapsed = time.perf_counter()-begun
    # Audit a serialization round trip, outside the solver's own final audit.
    result = json.loads(json.dumps(result))
    audit = audit_solution(problem, result)
    write(destination, dict(job=job, solve_wall_seconds=elapsed, result=result,
                            independent_audit=audit))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--phase', default='before')
    parser.add_argument('--source', type=Path, help='Use an existing frozen source directory')
    parser.add_argument('--families', nargs='+')
    parser.add_argument('--budgets', nargs='+', type=float)
    parser.add_argument('--seeds', nargs='+', type=int)
    parser.add_argument('--batch-counts', nargs='+', type=int)
    parser.add_argument('--pool-labels', nargs='+', choices=['full', 'tight'])
    parser.add_argument('--pilot-time', type=float, help='Explicit pilot-only time override')
    parser.add_argument('--register-only', action='store_true')
    parser.add_argument('--worker', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    args.output = args.output.resolve()
    if args.worker:
        worker(json.loads(args.worker), args.output)
        return
    if args.pilot_time is not None and (not math.isfinite(args.pilot_time) or args.pilot_time <= 0):
        parser.error('pilot time must be finite and positive')
    args.output.mkdir(parents=True, exist_ok=True)
    if not (args.output/'benchmark_manifest.json').exists():
        register(args.output)
    manifest = json.loads((args.output/'benchmark_manifest.json').read_text())
    if hashes(args.output/'inputs') != manifest['input_sha256']:
        raise ValueError('Frozen benchmark inputs have changed')
    if args.register_only:
        print(manifest['job_count'], 'registered jobs;', manifest['total_solver_seconds_per_phase'], 'solver seconds per phase')
        return
    jobs = [j for j in manifest['jobs']
        if (not args.families or j['family'] in args.families)
        and (not args.budgets or j['budget'] in args.budgets)
        and (not args.seeds or j['seed'] in args.seeds)
        and (not args.batch_counts or j['batches'] in args.batch_counts)
        and (not args.pool_labels or j['pool_label'] in args.pool_labels)]
    if not jobs:
        raise ValueError('No registered jobs selected')
    if args.pilot_time is not None:
        jobs = [{**j, 'budget': args.pilot_time} for j in jobs]
    phase = args.output/args.phase
    phase.mkdir()
    source = phase/'source'
    source.mkdir()
    origin = args.source.resolve() if args.source else ROOT
    for directory in ('models', 'configuration', 'tools'):
        for p in (origin/directory).rglob('*.py'):
            target = source/p.relative_to(origin)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
    # Always use the current benchmark harness with the selected solver snapshot.
    shutil.copy2(Path(__file__), source/'tools/audit/benchmark_model5_alns.py')
    source_hashes = hashes(source)
    write(phase/'manifest.json', dict(jobs=jobs, source_sha256=source_hashes,
        input_sha256=manifest['input_sha256'], arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()}))
    rows = []
    for index, job in enumerate(jobs):
        name = f'{index:03d}_{job["case_id"]}_{job["pool_label"]}_{job["budget"]:g}s_seed{job["seed"]}'
        dest = phase/(name+'.json')
        command = [sys.executable, str(source/'tools/audit/benchmark_model5_alns.py'),
                   '--worker', json.dumps(job), '--output', str(dest)]
        row = dict(job, result_file=dest.name)
        try:
            process = subprocess.run(command, cwd=source, capture_output=True, text=True,
                timeout=job['budget']+20, env={**os.environ, 'PYTHONHASHSEED':'0'})
            (phase/(name+'.log')).write_text(process.stdout+process.stderr)
            if process.returncode:
                row.update(status='ERROR', valid=False, error=process.stderr[-3000:])
            else:
                saved = json.loads(dest.read_text())
                info, audit = saved['result']['info'], saved['independent_audit']
                valid = bool(audit['valid']) and bool(audit.get('periodic_replay',{}).get('valid'))
                row.update(status=info['status'], valid=valid,
                    objective=audit['objective'] if valid else None,
                    periodic=audit.get('periodic_replay',{}).get('valid',False),
                    wall=saved['solve_wall_seconds'], solver_runtime=info['runtime'],
                    actual_iterations=info.get('iterations'),
                    first_feasible=info.get('time_to_first_feasible'),
                    time_to_best=info.get('time_to_best'), audit_errors=audit['errors'])
        except subprocess.TimeoutExpired as exc:
            row.update(status='OUTER_TIMEOUT', valid=False)
            (phase/(name+'.log')).write_bytes((exc.stdout or b'')+(exc.stderr or b''))
        rows.append(row)
        write(phase/'summary.json', rows)
        print(f'{index+1}/{len(jobs)} {name}: {row["status"]} valid={row["valid"]} distance={row.get("objective")}', flush=True)
    write(phase/'verification.json', dict(source_unchanged=hashes(source)==source_hashes,
        inputs_unchanged=hashes(args.output/'inputs')==manifest['input_sha256']))


if __name__ == '__main__':
    main()
