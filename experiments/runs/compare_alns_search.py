"""Paired local diagnostics against the immutable formal-study source ZIP.

This is a selected-case regression study, not a replacement formal benchmark.
Both versions run serially in isolated interpreters with alternating order.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile


ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT/'output/experiments/benchmark27_20261015_formal_v1'
CASES = [(2, 'S03'), (2, 'S09'), (4, 'S05'), (4, 'S09'),
         (8, 'M01'), (8, 'L05'), (8, 'L09')]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def worker(args):
    sys.path.insert(0, str(args.source))
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.service import solve_routing

    size = dict(S='small', M='medium', L='large')[args.case[0]]
    path = ROOT/f'data/formal_benchmark27_v1/model_{args.model}'/size/args.case/'case.json'
    problem = load_instance(*load_routing_case(path, use_pool_config=False))
    begun = time.perf_counter()
    result = solve_routing(model=args.model, method='alns', problem=problem,
        alns_time=args.seconds, alns_iter=10**9, seed=args.seed)
    wall = time.perf_counter()-begun
    audit = audit_solution(problem, result)
    write(args.output/'result.json', result)
    write(args.output/'audit.json', audit)
    info = result['info']
    row = dict(model=args.model, case=args.case, seed=args.seed, seconds=args.seconds,
        status=info['status'], valid=bool(audit['valid']), objective=info.get('objective'),
        wall_seconds=wall, iterations=info.get('iterations'),
        k=result.get('cycle_batch_count'), pool=result.get('container_pool_size'),
        candidate_count=len(result.get('count_search', [])),
        visited_count=sum(bool(r['visits']) for r in result.get('count_search', [])),
        k1_budget=sum(v['budget'] for v in result.get('count_search_visits', [])
                      if v['batch_count']==1),
        finished_at=datetime.now(timezone.utc).isoformat())
    write(args.output/'summary.json', row)
    print(json.dumps(row))


def snapshots(destination):
    baseline, candidate = destination/'baseline', destination/'candidate'
    with zipfile.ZipFile(STUDY/'source_current.zip') as archive:
        prefix = 'sources/current/'
        for name in archive.namelist():
            if not name.startswith(prefix) or name.endswith('/'):
                continue
            relative = Path(name[len(prefix):])
            if '..' in relative.parts or relative.is_absolute():
                raise ValueError('Invalid source archive member')
            target = baseline/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
    shutil.copytree(baseline, candidate)
    for folder in ('models', 'tools', 'cli', 'experiments'):
        for source in (ROOT/folder).rglob('*.py'):
            target = candidate/source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    for source in (baseline, candidate):
        for folder in ('data', 'config', 'archive'):
            if not (source/folder).exists():
                (source/folder).symlink_to(ROOT/folder, target_is_directory=True)
    return dict(baseline=baseline, candidate=candidate)


def compare(args):
    registered = json.loads((STUDY/'manifest.json').read_text(encoding='utf-8'))
    current_dataset = hashlib.sha256((ROOT/'data/formal_benchmark27_v1/manifest.json').read_bytes()).hexdigest()
    if current_dataset != registered['dataset_sha256']:
        raise ValueError('Historical source comparison requires its original dataset revision; '
                         'prepare a new formal study for the current cost profile')
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError('Use a new output directory')
    args.output.mkdir(parents=True, exist_ok=True)
    if args.seconds <= 0 or not args.seeds or len(set(args.seeds)) != len(args.seeds):
        raise ValueError('Require a positive time budget and distinct seeds')
    rows = []
    env = {**os.environ, **{k: '1' for k in
        ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')}}
    with tempfile.TemporaryDirectory(prefix='alns-search-comparison-') as temporary:
        sources = snapshots(Path(temporary))
        hashes = {variant: {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ('models', 'tools', 'cli', 'experiments')
            for p in sorted((source/folder).rglob('*.py'))}
            for variant, source in sources.items()}
        write(args.output/'manifest.json', dict(
            kind='selected_case_diagnostic_not_formal_benchmark',
            cases=CASES, seeds=args.seeds, seconds=args.seconds, iterations=10**9,
            python=sys.version, platform=platform.platform(), logical_cpus=os.cpu_count(),
            concurrency=1, paired_alternating_order=True,
            baseline_source_zip=str(STUDY/'source_current.zip'),
            baseline_source_zip_sha256=hashlib.sha256((STUDY/'source_current.zip').read_bytes()).hexdigest(),
            dataset_sha256=hashlib.sha256((ROOT/'data/formal_benchmark27_v1/manifest.json').read_bytes()).hexdigest(),
            source_hashes=hashes))
        index = 0
        for model, case in CASES:
            for seed in args.seeds:
                order = ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline']
                index += 1
                for variant in order:
                    output = args.output/variant/f'{case}_m{model}_s{seed}'
                    command = [sys.executable, str(Path(__file__).resolve()), 'worker',
                        '--source', str(sources[variant]), '--output', str(output),
                        '--case', case, '--model', str(model), '--seed', str(seed),
                        '--seconds', str(args.seconds)]
                    completed = subprocess.run(command, cwd=ROOT, env=env,
                        capture_output=True, text=True, timeout=args.seconds+60)
                    output.mkdir(parents=True, exist_ok=True)
                    (output/'worker.log').write_text(completed.stdout+completed.stderr, encoding='utf-8')
                    if completed.returncode:
                        row = dict(model=model, case=case, seed=seed, seconds=args.seconds,
                                   status='ERROR', valid=False, objective=None)
                    else:
                        row = json.loads((output/'summary.json').read_text(encoding='utf-8'))
                    rows.append(dict(variant=variant, **row))
                    write(args.output/'results.json', rows)
                    print(variant, case, f'M{model}', seed, row['status'], row['objective'], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('compare')
    run.add_argument('--output', type=Path, required=True)
    run.add_argument('--seconds', type=float, default=15)
    run.add_argument('--seeds', type=int, nargs='+', default=[42])
    one = sub.add_parser('worker')
    one.add_argument('--source', type=Path, required=True)
    one.add_argument('--output', type=Path, required=True)
    one.add_argument('--case', required=True)
    one.add_argument('--model', type=int, required=True)
    one.add_argument('--seed', type=int, required=True)
    one.add_argument('--seconds', type=float, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    worker(args) if args.command == 'worker' else compare(args)


if __name__ == '__main__':
    main()
