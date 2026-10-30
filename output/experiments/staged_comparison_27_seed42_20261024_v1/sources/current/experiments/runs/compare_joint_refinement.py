"""Serial paired diagnostics against revision-3 frozen sources.

Selected cases, budgets and seeds are explicit; this is not the formal study.
Both versions run in fresh processes with identical inputs and one solver thread.
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
import tempfile
import zipfile

from experiments.runs import compare_alns_search as prior

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT/'output/experiments/benchmark27_20261016_cost_update_v1'


def write(path, value):
    prior.write(path, value)


def snapshots(destination, baseline_zip):
    """Freeze either a formal study or a previous local development version."""
    baseline, candidate = destination/'baseline', destination/'candidate'
    with zipfile.ZipFile(baseline_zip) as archive:
        prefix = 'sources/current/' if 'sources/current/models/__init__.py' in archive.namelist() else ''
        for name in archive.namelist():
            if not name.startswith(prefix) or name.endswith('/'):
                continue
            relative = Path(name[len(prefix):])
            if '..' in relative.parts or relative.is_absolute():
                raise ValueError('Invalid source archive member')
            target = baseline/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
    source_folders = ('models', 'tools', 'cli', 'experiments')
    # Keep snapshot support files, but replace each active source tree in full.
    # An overlay would retain deleted/renamed modules from the older layout.
    shutil.copytree(baseline, candidate, ignore=lambda directory, names: [
        name for name in names if Path(directory) == baseline and name in source_folders])
    for folder in source_folders:
        for path in (ROOT/folder).rglob('*.py'):
            target = candidate/path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    for source in (baseline, candidate):
        for folder in ('data', 'config', 'archive'):
            if not (source/folder).exists():
                (source/folder).symlink_to(ROOT/folder, target_is_directory=True)
    return dict(baseline=baseline, candidate=candidate)


def worker(args):
    sys.path.insert(0, str(args.source))
    # The controller is imported before source selection; it imports no models.
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.routing.service import solve_routing
    import time

    size = dict(S='small', M='medium', L='large')[args.case[0]]
    path = ROOT/f'data/formal_benchmark27_v1/model_{args.model}'/size/args.case/'case.json'
    problem = load_instance(*load_routing_case(path, use_pool_config=False))
    begun = time.perf_counter()
    result = solve_routing(model=args.model, method=args.method, problem=problem,
        alns_time=args.seconds, alns_iter=10**9, exact_time=args.seconds, seed=args.seed)
    wall = time.perf_counter()-begun
    audit = audit_solution(problem, result)
    write(args.output/'result.json', result)
    write(args.output/'audit.json', audit)
    info = result['info']
    row = dict(model=args.model, case=args.case, method=args.method,
        seed=args.seed, seconds=args.seconds, status=info['status'], valid=bool(audit['valid']),
        objective=info.get('objective'), bound=info.get('best_bound'), gap=info.get('mip_gap'),
        proven_optimal=bool(info.get('proven_optimal')), wall_seconds=wall,
        iterations=info.get('iterations'), k=result.get('cycle_batch_count'),
        pool=result.get('container_pool_size'), candidate_count=len(result.get('count_search', [])),
        visited_count=sum(bool(r['visits']) for r in result.get('count_search', [])))
    write(args.output/'summary.json', row)
    print(json.dumps(row))


def compare(args):
    manifest = json.loads((STUDY/'manifest.json').read_text())
    dataset = hashlib.sha256((ROOT/'data/formal_benchmark27_v1/manifest.json').read_bytes()).hexdigest()
    if dataset != manifest['dataset_sha256']:
        raise ValueError('Dataset differs from frozen revision-3 study')
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError('Use a new output directory')
    cases = [(int(s.split(':')[0]), s.split(':')[1]) for s in args.cases]
    env = {**os.environ, **{key: '1' for key in
        ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')}}
    rows = []
    baseline_zip = args.baseline_source or STUDY/'source_current.zip'
    with tempfile.TemporaryDirectory(prefix='joint-refinement-') as directory:
        sources = snapshots(Path(directory), baseline_zip)
        hashes = {name: {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ('models', 'tools', 'cli', 'experiments')
            for p in sorted((source/folder).rglob('*.py'))} for name, source in sources.items()}
        write(args.output/'manifest.json', dict(kind='selected_case_diagnostic_not_formal_benchmark',
            cases=cases, method=args.method, seconds=args.seconds, seeds=args.seeds,
            python=sys.version, platform=platform.platform(), concurrency=1,
            paired_alternating_order=True, dataset_sha256=dataset, source_hashes=hashes,
            baseline_source_zip=str(baseline_zip),
            baseline_source_zip_sha256=hashlib.sha256(baseline_zip.read_bytes()).hexdigest()))
        with zipfile.ZipFile(args.output/'candidate_sources.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
            for relative in hashes['candidate']:
                archive.write(sources['candidate']/relative, relative)
        index = 0
        for model, case in cases:
            for seed in args.seeds:
                variants = ['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline']
                index += 1
                for variant in variants:
                    output = args.output/variant/f'{case}_m{model}_{args.method}_s{seed}'
                    command = [sys.executable, '-m', 'experiments.runs.compare_joint_refinement',
                        'worker', '--source', str(sources[variant]), '--output', str(output),
                        '--case', case, '--model', str(model), '--method', args.method,
                        '--seed', str(seed), '--seconds', str(args.seconds)]
                    completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                               text=True, timeout=args.seconds+60)
                    output.mkdir(parents=True, exist_ok=True)
                    (output/'worker.log').write_text(completed.stdout+completed.stderr)
                    if completed.returncode:
                        raise RuntimeError(f'{variant} {case} M{model} failed; see {output}/worker.log')
                    row = dict(variant=variant, **json.loads((output/'summary.json').read_text()))
                    rows.append(row)
                    write(args.output/'results.json', rows)
                    print(variant, model, case, seed, row['objective'], row['bound'],
                          row['valid'], row['proven_optimal'], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('compare')
    run.add_argument('--cases', nargs='+', required=True, help='M:CASE, e.g. 6:S03 8:L05')
    run.add_argument('--seeds', type=int, nargs='+', default=[42])
    run.add_argument('--baseline-source', type=Path, help='Frozen source ZIP; defaults to revision-3 formal study')
    one = sub.add_parser('worker')
    one.add_argument('--source', type=Path, required=True)
    one.add_argument('--case', required=True)
    one.add_argument('--model', type=int, required=True)
    one.add_argument('--seed', type=int, required=True)
    for command in (run, one):
        command.add_argument('--output', type=Path, required=True)
        command.add_argument('--method', choices=['alns', 'exact'], required=True)
        command.add_argument('--seconds', type=float, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    if args.seconds <= 0:
        raise ValueError('Require a positive time budget')
    compare(args) if args.command == 'compare' else worker(args)


if __name__ == '__main__':
    main()
