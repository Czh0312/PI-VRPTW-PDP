"""Bounded, sequential before/after audit runs; retain every failed run.

The manifest is written before solving. Use the same manifest for both versions.
This is a diagnostic benchmark, not the full research experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
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


def worker(job, destination):
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.service import solve_routing
    config, fleet = load_routing_case(job['case'])
    problem = load_instance({**config, 'model': job['model']}, fleet)
    begun = time.perf_counter()
    result = solve_routing(model=job['model'], method=job['solver'], problem=problem,
                           exact_time=job['budget'], alns_time=job['budget'],
                           alns_iter=job['iterations'], seed=job['seed'])
    wall = time.perf_counter()-begun
    audit = audit_solution(problem, result)
    write(destination, dict(job=job, solve_wall_seconds=wall, result=result,
                           independent_audit=audit, orders=len(problem.orders),
                           customers=len({o.customer for o in problem.orders}),
                           satellites=len(problem.satellites)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--phase', default='before')
    parser.add_argument('--families', nargs='+', help='Optional diagnostic subset of registered families')
    parser.add_argument('--models', type=int, nargs='+', choices=range(1, 5))
    parser.add_argument('--budgets', type=float, nargs='+', help='Optional subset of registered time budgets')
    parser.add_argument('--worker', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    args.output = args.output.resolve()
    if args.worker:
        worker(json.loads(args.worker), args.output)
        return
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output/'benchmark_manifest.json'
    if not manifest_path.exists():
        families = ['mechanism_forced_late', 'mechanism_cutoff_choice',
                    'zhou_Set1_7-2-1', 'zhou_Set1_7-2-1b', 'lade_0823_10aoi',
                    'zhou_Set2_100-5-1b_subset50', 'lade_0823_30aoi']
        dataset = json.loads((ROOT/'data/manifest.json').read_text())
        cases = {f['family_id']: f['cases']['b1'] for f in dataset['families']}
        from configuration.loader import load_routing_case, resolve_project_path
        frozen = args.output.resolve()/'benchmark_inputs'
        frozen.mkdir()
        for family in families:
            config, fleet = load_routing_case(cases[family])
            folder = frozen/family
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
            for field in ('algorithm_config', 'container_pool_config'):
                config.pop(field, None)
            write(folder/'case.json', config)
            cases[family] = str(folder/'case.json')
        jobs = [dict(family=family, case=cases[family], model=model, solver=solver,
                     seed=seed, budget=5.0 if solver == 'exact' else 1.0, iterations=2000)
                for family in families for model in range(1, 5)
                for solver, seed in [('exact', 42), ('alns', 42), ('alns', 43), ('alns', 44)]]
        write(manifest_path, dict(purpose='diagnostic_before_after_not_full_study', jobs=jobs,
                                 outer_timeout_overhead=20, created=time.strftime('%Y-%m-%dT%H:%M:%S'),
                                 python=sys.version, platform=platform.platform()))
    manifest = json.loads(manifest_path.read_text())
    out = args.output/args.phase
    out.mkdir()
    jobs = [job for job in manifest['jobs']
            if (not args.families or job['family'] in args.families)
            and (not args.models or job['model'] in args.models)
            and (not args.budgets or job['budget'] in args.budgets)]
    if not jobs:
        raise ValueError('The requested diagnostic subset has no registered jobs')
    write(out/'jobs.json', jobs)
    sources = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
               for base in ('models', 'configuration') for p in (ROOT/base).rglob('*.py')}
    inputs = {str(p.relative_to(args.output)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (args.output/'benchmark_inputs').rglob('*') if p.is_file()}
    write(out/'hashes.json', dict(sources=sources, inputs=inputs))
    rows = []
    for index, job in enumerate(jobs):
        name = f'{index:03d}_{job["family"]}_M{job["model"]}_{job["solver"]}_{job["seed"]}'
        dest = out/(name+'.json')
        command = [sys.executable, str(Path(__file__).resolve()), '--worker', json.dumps(job),
                   '--output', str(dest)]
        row = dict(job)
        try:
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                       timeout=job['budget']+manifest['outer_timeout_overhead'],
                                       env={**os.environ, 'PYTHONHASHSEED': '0'})
            (out/(name+'.log')).write_text(completed.stdout+completed.stderr)
            if completed.returncode:
                row.update(status='ERROR', error=completed.stderr[-3000:], valid=False)
            else:
                saved = json.loads(dest.read_text())
                info, audit = saved['result']['info'], saved['independent_audit']
                row.update(status=info['status'], valid=audit['valid'],
                           periodic=audit.get('periodic_replay', {}).get('valid', False),
                           objective=info.get('objective'), optimal=info.get('proven_optimal', False),
                           infeasible=info.get('proven_infeasible', False),
                           bound=info.get('best_bound'), gap=info.get('mip_gap'),
                           wall=saved['solve_wall_seconds'], build=info.get('build_time'),
                           variables=info.get('variables'), constraints=info.get('constraints'),
                           iterations=info.get('iterations'), visited=info.get('assignments_visited'),
                           assignment_strategy=info.get('assignment_strategy'),
                           domain_assignment_count=info.get('domain_assignment_count'),
                           global_assignment_distance_bound=info.get('global_assignment_distance_bound'))
        except subprocess.TimeoutExpired as exc:
            row.update(status='OUTER_TIMEOUT', valid=False)
            (out/(name+'.log')).write_bytes((exc.stdout or b'')+(exc.stderr or b''))
        rows.append(row)
        write(out/'summary.json', rows)
        print(name, row['status'], row.get('objective'), 'valid=', row['valid'], flush=True)


if __name__ == '__main__':
    main()
