"""Frozen, paired M6/M7/M8 calendar assessment. No output overwrites/retries."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def register(out):
    if out.exists() and any(out.iterdir()):
        raise ValueError('Refusing to overwrite an existing assessment')
    out.mkdir(parents=True, exist_ok=True)
    from configuration.loader import load_routing_case
    manifest_path = ROOT/'data/model6/uniform_v2_20261005/manifest.json'
    rows = read(manifest_path)['cases']
    groups = {}
    for row in sorted(rows, key=lambda r: r['name']):
        if row['variant'] == 'spread_medium':
            groups.setdefault((row['source_kind'], row['customer_count']), row)
    selected = list(groups.values())
    wanted = {'zhou_Set1_7-2-1': ['spread_narrow', 'clean_slow', 'release_staggered',
                               'pool_high', 'repeat_orders', 'extended_k'],
              'lade_0823_10aoi': ['spread_narrow', 'release_staggered', 'clean_slow']}
    selected += [row for row in rows if row['variant'] in wanted.get(row['family_id'], [])]
    selected.sort(key=lambda r: r['name'])
    assert len(selected) == 19
    input_hashes = {}
    cases = []
    for row in selected:
        config, fleet = load_routing_case(ROOT/row['m6_case'], use_pool_config=False)
        # Freeze all routing/cost inputs, deduplicating only identical bytes.
        for key in ('data', 'orders', 'routing_config', 'operating_policy', 'distance_matrix',
                    'duration_matrix', 'economic_config', 'dataset_metadata', 'algorithm_config'):
            if isinstance(config.get(key), str):
                origin = Path(config[key])
                if not origin.is_absolute():
                    origin = ROOT/origin
                digest = sha(origin)
                dest = out/'inputs'/(digest+origin.suffix)
                dest.parent.mkdir(exist_ok=True)
                if not dest.exists():
                    shutil.copy2(origin, dest)
                config[key] = str(dest)
                input_hashes[str(dest.relative_to(out))] = digest
        case_path = out/'cases'/(row['name']+'.json')
        write(case_path, dict(config=config, fleet=fleet, record=row))
        input_hashes[str(case_path.relative_to(out))] = sha(case_path)
        cases.append(dict(name=row['name'], case=str(case_path), customer_count=row['customer_count'],
                          family_id=row['family_id'], variant=row['variant']))
    jobs = []
    for case in cases:
        for model in (6, 7, 8):
            for method, seed in [('alns', s) for s in (42, 43, 44)]+([('exact', 42)] if case['customer_count'] <= 12 else []):
                jobs.append(dict(id=f'{case["name"]}__m{model}_{method}_{seed}', case=case['case'],
                                 name=case['name'], model=model, method=method, seed=seed,
                                 budget=8 if method == 'exact' else 3, iterations=5000))
    random.Random(20261005).shuffle(jobs)
    write(out/'plan.json', dict(registered_at=datetime.now(timezone.utc).isoformat(), cases=cases,
          jobs=jobs, workers=2, input_sha256=input_hashes, manifest_sha256=sha(manifest_path),
          scope='Implementation, feasibility and bounded search assessment; no population-level significance claim.',
          selection='First lexicographic family per source and size, plus predefined sensitivity variants.',
          stages=['freeze_baseline', 'run_baseline', 'diagnose', 'freeze_candidate_if_needed', 'paired_retest'],
          include_all_statuses=True, hard_timeout='max(30, 4*budget+10) seconds per worker'))
    print('REGISTERED', len(cases), 'cases;', len(jobs), 'runs', flush=True)


def freeze(out, phase):
    root = out/phase/'source'
    if root.exists():
        raise ValueError('Refusing to replace a frozen source version')
    paths = [p for folder in ('models', 'configuration', 'cli') for p in (ROOT/folder).rglob('*.py')]
    paths += [Path(__file__)]
    hashes = {}
    for source in paths:
        name = source.relative_to(ROOT)
        dest = root/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        hashes[str(name)] = sha(dest)
    write(out/phase/'source_hashes.json', hashes)


def worker(out, phase, job_id):
    plan = read(out/'plan.json')
    job = next(j for j in plan['jobs'] if j['id'] == job_id)
    source = out/phase/'source'
    sys.path.insert(0, str(source))
    for name, digest in read(out/phase/'source_hashes.json').items():
        assert sha(source/name) == digest, name
    for name, digest in plan['input_sha256'].items():
        assert sha(out/name) == digest, name
    from models.routing.common.cycle.instance import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.service import solve_routing
    payload = read(job['case'])
    config = payload['config']
    model = job['model']
    modes = {6: 'uniform_cycle', 7: 'uniform_independent_cutoffs', 8: 'flexible_cycle'}
    config.update(model=model, joint_batch_options={**config['joint_batch_options'], 'schedule_mode': modes[model]})
    problem = load_instance(config, payload['fleet'])
    started = time.perf_counter()
    result = solve_routing(model=model, method=job['method'], problem=problem, seed=job['seed'],
                           alns_time=job['budget'], exact_time=job['budget'], alns_iter=job['iterations'])
    elapsed = time.perf_counter()-started
    audit = audit_solution(problem, result) if result['info'].get('objective') is not None else None
    write(out/phase/'results'/(job_id+'.json'), dict(job=job, result=result, independent_audit=audit,
                                                   solve_wall_time=elapsed, load_average=os.getloadavg()))
    return dict(id=job_id, name=job['name'], model=model, method=job['method'], seed=job['seed'],
                status=result['info']['status'], valid=bool(audit and audit['valid']),
                objective=result['info'].get('objective'), best_bound=result['info'].get('best_bound'),
                optimal=result['info'].get('proven_optimal', False), infeasible=result['info'].get('proven_infeasible', False),
                wall_time=elapsed, budget=job['budget'], batch_count=result.get('cycle_batch_count'),
                delivery_batch_count=result.get('delivery_batch_count'), pool=result.get('container_pool_size'),
                errors=audit['errors'] if audit else [])


def run(out, phase):
    plan = read(out/'plan.json')
    results_dir = out/phase/'results'
    if results_dir.exists():
        raise ValueError('Refusing to overwrite/retry recorded runs')
    results_dir.mkdir(parents=True)
    log_dir = out/phase/'logs'
    log_dir.mkdir()
    env = {**os.environ, 'PYTHONHASHSEED': '0', 'PYTHONPYCACHEPREFIX': '/tmp/model78-review-bytecode'}
    runner = out/phase/'source/tools/audit/benchmark_model78.py'
    def execute(job):
        destination = out/phase/'summaries'/(job['id']+'.json')
        command = [sys.executable, str(runner), '--output', str(out), '--phase', phase, '--job', job['id']]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, env=env,
                                       timeout=max(30, 4*job['budget']+10))
            (log_dir/(job['id']+'.log')).write_text(completed.stdout+completed.stderr)
            if completed.returncode != 0:
                write(destination, dict(id=job['id'], name=job['name'], model=job['model'], method=job['method'],
                                         seed=job['seed'], status='CRASHED', valid=False, returncode=completed.returncode))
        except subprocess.TimeoutExpired as exc:
            write(destination, dict(id=job['id'], name=job['name'], model=job['model'], method=job['method'],
                                     seed=job['seed'], status='HARD_TIMEOUT', valid=False, budget=job['budget']))
            (log_dir/(job['id']+'.log')).write_text(str(exc))
        return read(destination)
    summaries = []
    with ThreadPoolExecutor(max_workers=plan['workers']) as executor:
        futures = [executor.submit(execute, job) for job in plan['jobs']]
        for future in as_completed(futures):
            summary = future.result()
            summaries.append(summary)
            write(out/phase/'progress.json', dict(completed=len(summaries), total=len(plan['jobs']), last=summary))
            if len(summaries) % 12 == 0 or summary['status'] in ('CRASHED', 'HARD_TIMEOUT', 'AUDIT_FAILED'):
                print(len(summaries), '/', len(plan['jobs']), summary['id'], summary['status'], flush=True)
    write(out/phase/'summary.json', sorted(summaries, key=lambda r: r['id']))
    print('COMPLETED', phase, len(summaries), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--phase', default='baseline')
    parser.add_argument('--register', action='store_true')
    parser.add_argument('--freeze', action='store_true')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--job')
    args = parser.parse_args()
    out = args.output.resolve()
    if args.job:
        try:
            summary = worker(out, args.phase, args.job)
            write(out/args.phase/'summaries'/(args.job+'.json'), summary)
        except Exception:
            traceback.print_exc()
            raise
    else:
        if args.register:
            register(out)
        if args.freeze:
            freeze(out, args.phase)
        if args.run:
            run(out, args.phase)


if __name__ == '__main__':
    main()
