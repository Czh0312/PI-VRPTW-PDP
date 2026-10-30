"""Single-root-seed ALNS search experiment; independent exact references are offline only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SEED = 42


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    for attempt in range(10):
        try:
            path.write_text(payload, encoding='utf-8')
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(min(.02*2**attempt, .5))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sources(variant):
    folder = HERE / 'sources' / variant
    return {p.relative_to(folder).as_posix(): sha(p) for p in folder.rglob('*.py')}


def prepare():
    for variant in ['baseline', 'candidate']:
        folder = HERE / 'sources' / variant / 'models'
        if folder.exists():
            raise FileExistsError(folder)
        shutil.copytree(ROOT / 'models', folder, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    files = {}
    for case in [f'S{i:02}' for i in range(1, 10)]:
        case_path = ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json'
        files[str(case_path)] = sha(case_path)
        cfg = read(case_path)
        for key in ['data', 'routing_config', 'orders', 'operating_policy', 'economic_config',
                    'dataset_metadata', 'parameter_contract', 'batch_count_certificate']:
            if isinstance(cfg.get(key), str):
                path = Path(cfg[key])
                path = path if path.is_absolute() else ROOT/path
                files[str(path)] = sha(path)
    save(HERE / 'inputs.json', files)
    save(HERE / 'baseline_hashes.json', sources('baseline'))
    print(dict(single_experiment_seed=SEED, cases=9, source_files=len(sources('baseline'))), flush=True)


def worker(args):
    source = HERE / 'sources' / args.variant
    sys.path[:0] = [str(source), str(ROOT)]
    assert not any(name == 'models' or name.startswith('models.') for name in sys.modules)
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.model_8.alns.solver import solve_alns
    from models.routing.model_8.alns import search, fixed_calendar_search, calendar_polish
    from tools.configuration.loader import load_routing_case
    p = load_instance(*load_routing_case(ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{args.case}/case.json', use_pool_config=False))
    begun = time.perf_counter()
    cpu_begun = time.process_time()
    result = solve_alns(p, max_time=args.seconds, max_iterations=10**9, seed=SEED)
    wall = time.perf_counter()-begun
    cpu = time.process_time()-cpu_begun
    audit = audit_solution(p, result)
    valid = bool(audit['valid'] and audit.get('periodic_replay', {}).get('valid'))
    assert not result['info'].get('proven_optimal')
    assert not any(name.startswith('gurobipy') or (name.startswith('models.') and '.exact' in name) for name in sys.modules)
    folder = HERE / 'runs' / args.tag / args.case / args.variant
    if folder.exists():
        raise FileExistsError(folder)
    save(folder / 'result.json', result)
    save(folder / 'audit.json', audit)
    row = dict(case=args.case, variant=args.variant, tag=args.tag, seed=SEED,
        seconds=args.seconds, wall_seconds=wall, cpu_seconds=cpu,
        objective=audit.get('objective') if valid else None, valid=valid,
        periodic_valid=bool(audit.get('periodic_replay', {}).get('valid')),
        iterations=result['info'].get('iterations'), K=result.get('cycle_batch_count'),
        N=result.get('container_pool_size'), external_initial_solution_used=False,
        exact_reference_used=False, original_K=list(p.batch_count_candidates))
    save(folder / 'summary.json', row)
    print(json.dumps(row, ensure_ascii=False), flush=True)


def run(args):
    cases = args.cases or ['S02', 'S03', 'S09']
    jobs = []
    for index, case in enumerate(cases):
        for variant in (['baseline', args.compare] if index % 2 == 0 else [args.compare, 'baseline']):
            jobs.append(dict(case=case, variant=variant))
    plan_path = HERE / f'{args.tag}_plan.json'
    if plan_path.exists():
        raise FileExistsError(plan_path)
    manifest = dict(jobs=jobs, seed=SEED, seconds=args.seconds, concurrency=1,
        variants={v: sources(v) for v in ['baseline', args.compare]}, driver_sha256=sha(Path(__file__)),
        input_hashes=read(HERE/'inputs.json'), cold_start=True, exact_reference_used=False,
        clock='Wall budget includes construction, search, polishing and internal audits; final independent audit outside.')
    save(plan_path, manifest)
    for job in jobs:
        command = [sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), 'worker',
            '--case', job['case'], '--variant', job['variant'], '--seconds', str(args.seconds), '--tag', args.tag]
        environment = {**os.environ, 'PYTHONHASHSEED': '0', **{key: '1' for key in
            ['OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS']}}
        completed = subprocess.run(command, cwd=ROOT, env=environment,
            capture_output=True, text=True, encoding='utf-8', timeout=args.seconds+120)
        log = HERE / 'logs' / f'{args.tag}_{job["case"]}_{job["variant"]}.log'
        log.parent.mkdir(exist_ok=True)
        log.write_text(completed.stdout+completed.stderr, encoding='utf-8')
        if completed.returncode:
            raise RuntimeError(completed.stdout+completed.stderr)
        print(completed.stdout.strip(), flush=True)
    assert all(sha(path) == digest for path, digest in manifest['input_hashes'].items())
    assert all(sources(v) == manifest['variants'][v] for v in manifest['variants'])
    assert sha(Path(__file__)) == manifest['driver_sha256']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run', 'worker'])
    parser.add_argument('--tag', default='pilot')
    parser.add_argument('--seconds', type=float, default=20.)
    parser.add_argument('--cases', nargs='+')
    parser.add_argument('--case', default='S03')
    parser.add_argument('--variant', choices=['baseline', 'candidate', 'candidate_v2', 'candidate_v3'], default='baseline')
    parser.add_argument('--compare', choices=['candidate', 'candidate_v2', 'candidate_v3'], default='candidate')
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare()
    elif args.action == 'worker':
        worker(args)
    else:
        run(args)
