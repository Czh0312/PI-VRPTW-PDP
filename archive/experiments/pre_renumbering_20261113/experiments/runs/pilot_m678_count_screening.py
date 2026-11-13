"""One small case, one seed, two serial exact runs: pre-build K screening.

Both runs use compact E2, a complete K domain, default Gurobi settings and no
MIP start. Only the candidate omits blocks with an assignment certificate.
The public exact solver remains unchanged by default. Requires a valid license.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from importlib import import_module
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
DATA = Path('data/formal_benchmark27_spacetime_v2')


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def worker(args):
    sys.path.insert(0, str(args.source))
    from models.common.cycle import load_instance
    from models.common.cycle.audit import audit_solution
    from models.common.cycle.unified_milp import solve_unified_milp
    from tools.configuration.loader import load_routing_case
    case = args.source / DATA / f'model_{args.model}/small/{args.case}/case.json'
    p = load_instance(*load_routing_case(case))
    package = f'models.routing.model_{args.model}.exact'
    start = time.perf_counter()
    result = solve_unified_milp(p,
        build_fixed=import_module(package + '.formulation').build_model,
        extract_fixed=import_module(package + '.solution').extract_solution,
        time_limit=args.seconds, seed=args.seed, verbose=True,
        milp_options={'threads': 1, 'formulation': 'compact'},
        count_screening=args.worker == 'screened')
    elapsed = time.perf_counter() - start
    write(args.output / 'result.json', result)
    info = result['info']
    audit = audit_solution(p, result) if info.get('objective') is not None else None
    write(args.output / 'audit.json', audit)
    row = dict(model=args.model, case=args.case, variant=args.worker, seed=args.seed,
               wall_seconds=elapsed, info=info, audit=audit,
               selected_k=result.get('cycle_batch_count'))
    write(args.output / 'summary.json', row)
    if info.get('status') == 'AUDIT_FAILED' or (audit is not None and not audit['valid']):
        raise RuntimeError('Incumbent audit failed; preserve outputs and stop')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=int, choices=(6, 7, 8), default=6)
    parser.add_argument('--case', default='S04')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--seconds', type=float, default=300)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument('--worker', choices=('baseline', 'screened'), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not re.fullmatch(r'S\d{2}', args.case):
        parser.error('Choose exactly one small case, e.g. S04')
    if not math.isfinite(args.seconds) or args.seconds <= 0 or not 0 <= args.seed <= 2000000000:
        parser.error('Require a positive finite budget and one valid seed')
    args.source, args.output = args.source.resolve(), args.output.resolve()
    if args.worker:
        worker(args)
        return
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('Use a new or empty output directory; runs are never overwritten or retried')
    case_dir = DATA / f'model_{args.model}/small/{args.case}'
    shared_dir = DATA / f'shared/small/{args.case}'
    if not (args.source / case_dir / 'case.json').is_file():
        parser.error('Input case not found')
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = dict(created_at=datetime.now(timezone.utc).isoformat(), model=args.model,
        case=args.case, seed=args.seed, seconds=args.seconds, run_order=['baseline', 'screened'],
        threads=1, formulation='compact', count_domain='original_complete', mip_start=False,
        retry=False, completed=[], status='license_preflight', command=sys.argv)
    write(args.output / 'manifest.json', manifest)
    import gurobipy as gp
    try:
        probe = gp.Model()
        probe.dispose()
    except gp.GurobiError as exc:
        manifest.update(status='blocked_license', license_error=str(exc), optimization_runs=0)
        write(args.output / 'manifest.json', manifest)
        print(json.dumps(manifest, ensure_ascii=False), flush=True)
        return 78
    source = args.output / 'source'
    paths = [p for folder in ('models', 'tools') for p in (args.source / folder).rglob('*.py')]
    paths += list((args.source / case_dir).glob('*.json'))
    paths += [p for p in (args.source / shared_dir).iterdir() if p.suffix in ('.json', '.csv')]
    hashes = {}
    for path in sorted(set(paths)):
        rel = path.relative_to(args.source)
        target = source / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        hashes[str(rel)] = hashlib.sha256(target.read_bytes()).hexdigest()
    driver = args.output / 'driver.py'
    shutil.copy2(__file__, driver)
    manifest.update(status='running', source_sha256=hashes,
                    driver_sha256=hashlib.sha256(driver.read_bytes()).hexdigest())
    write(args.output / 'manifest.json', manifest)
    env = {**os.environ, 'PYTHONHASHSEED': '0', 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'}
    for variant in manifest['run_order']:
        target = args.output / variant
        target.mkdir()
        command = [sys.executable, str(driver), '--worker', variant, '--source', str(source),
            '--model', str(args.model), '--case', args.case, '--seed', str(args.seed),
            '--seconds', str(args.seconds), '--output', str(target)]
        begun = time.monotonic()
        hard_timeout = args.seconds + 120
        with (target / 'console.log').open('w') as log:
            child = subprocess.Popen(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
            while True:
                try:
                    code = child.wait(timeout=min(30, max(.01, hard_timeout-(time.monotonic()-begun))))
                    break
                except subprocess.TimeoutExpired:
                    elapsed = time.monotonic()-begun
                    print(json.dumps(dict(variant=variant, elapsed_seconds=elapsed)), flush=True)
                    if elapsed >= hard_timeout:
                        child.kill()
                        child.wait()
                        manifest.update(status='hard_timeout', failed_variant=variant)
                        write(args.output / 'manifest.json', manifest)
                        return 1
        if code:
            manifest.update(status='failed', failed_variant=variant, returncode=code)
            write(args.output / 'manifest.json', manifest)
            return code
        manifest['completed'].append(variant)
        write(args.output / 'manifest.json', manifest)
    manifest['status'] = 'completed'
    write(args.output / 'manifest.json', manifest)
    write(args.output / 'comparison.json', [json.loads((args.output / v / 'summary.json').read_text())
                                          for v in manifest['run_order']])
    return 0


if __name__ == '__main__':
    sys.exit(main())
