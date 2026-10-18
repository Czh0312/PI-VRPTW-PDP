"""Serial fixed-K construction diagnostics for two frozen source versions."""
import argparse
import hashlib
from importlib import import_module
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from experiments.runs.compare_joint_refinement import ROOT, snapshots, write


def worker(args):
    sys.path.insert(0, str(args.source))
    from tools.configuration.loader import load_routing_case
    from models.common.cycle import load_instance
    size = dict(S='small', M='medium', L='large')[args.case[0]]
    path = ROOT/f'data/formal_benchmark27_v1/model_{args.model}/{size}/{args.case}/case.json'
    problem = load_instance(*load_routing_case(path, use_pool_config=False)).for_count(args.count)
    organized = (args.source/f'models/routing/model_{args.model}/exact/formulations').is_dir()
    name = 'model' if args.model == 6 else ('formulations.fixed_count' if organized else 'joint_model')
    builder = import_module(f'models.routing.model_{args.model}.exact.{name}').build_model
    begun = time.perf_counter()
    model, ctx = builder(problem, deadline=begun+60)
    elapsed = time.perf_counter()-begun
    try:
        row = dict(model=args.model, case=args.case, k=args.count, build_seconds=elapsed,
                   variables=model.NumVars, binary=model.NumBinVars, integer=model.NumIntVars,
                   linear=model.NumConstrs, indicators=model.NumGenConstrs,
                   e2_routes=len(ctx['e2']))
        write(args.output, row)
    finally:
        model.dispose()


def compare(args):
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError('Use a new output directory')
    rows = []
    with tempfile.TemporaryDirectory(prefix='joint-build-') as temporary:
        sources = snapshots(Path(temporary), args.baseline_source)
        hashes = {version: {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for folder in ('models', 'tools', 'cli', 'experiments')
                  for p in sorted((source/folder).rglob('*.py'))} for version, source in sources.items()}
        write(args.output/'manifest.json', dict(cases=args.cases, repeats=args.repeats,
            source_hashes=hashes, baseline_source=str(args.baseline_source),
            baseline_sha256=hashlib.sha256(args.baseline_source.read_bytes()).hexdigest(),
            dataset_sha256=hashlib.sha256((ROOT/'data/formal_benchmark27_v1/manifest.json').read_bytes()).hexdigest()))
        for configuration in args.cases:
            number, case, count = configuration.split(':')
            for repeat in range(args.repeats):
                versions = ['baseline', 'candidate'] if repeat % 2 == 0 else ['candidate', 'baseline']
                for version in versions:
                    output = args.output/f'{number}_{case}_k{count}_{repeat}_{version}.json'
                    command = [sys.executable, '-m', 'experiments.runs.profile_joint_build', 'worker',
                               '--source', str(sources[version]), '--model', number, '--case', case,
                               '--count', count, '--output', str(output)]
                    process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=75)
                    output.with_suffix('.log').write_text(process.stdout+process.stderr)
                    if process.returncode:
                        raise RuntimeError(f'Build failed; see {output.with_suffix(".log")}')
                    row = dict(version=version, repeat=repeat, **json.loads(output.read_text()))
                    rows.append(row)
                    write(args.output/'results.json', rows)
                    print(json.dumps(row), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('compare')
    run.add_argument('--baseline-source', type=Path, required=True)
    run.add_argument('--cases', nargs='+', required=True, help='MODEL:CASE:K')
    run.add_argument('--repeats', type=int, default=3)
    one = sub.add_parser('worker')
    one.add_argument('--source', type=Path, required=True)
    one.add_argument('--model', type=int, required=True)
    one.add_argument('--case', required=True)
    one.add_argument('--count', type=int, required=True)
    for command in (run, one):
        command.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    compare(args) if args.command == 'compare' else worker(args)


if __name__ == '__main__':
    main()
