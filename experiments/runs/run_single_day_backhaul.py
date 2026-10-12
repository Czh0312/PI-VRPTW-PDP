"""Run and independently audit single-day M3 instances across solvers/seeds."""
from __future__ import annotations

import argparse
import csv
from contextlib import redirect_stdout
from datetime import datetime
import glob
import hashlib
import io
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.configuration.loader import load_routing_case, load_json, resolve_project_path
from models.common.data_loader import parse_dataset
from models.common.domain.instance import ProblemInstance
from models.common.matrix_loader import load_square_matrix
from models.common.solution_audit import audit_exact_solution
from models.common.task_builder import build_tasks_for_model
from models.routing.service import solve_routing


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', default='config/experiments/baselines/zhou_single_day.json')
    parser.add_argument('--data', action='append', help='Data path/glob; repeat for multiple sets')
    parser.add_argument('--fleet-config', help='Override fleet, preserving the single-day policy')
    parser.add_argument('--solver', choices=('both', 'exact', 'alns'), default='both')
    parser.add_argument('--backends', nargs='+', choices=('commodity_flow', 'indexed'),
                        default=['commodity_flow', 'indexed'])
    parser.add_argument('--seeds', nargs='+', type=int, default=[42, 43, 44])
    parser.add_argument('--time', type=float, default=10)
    parser.add_argument('--exact-time', type=float, default=60)
    parser.add_argument('--day-end', type=float)
    parser.add_argument('--output', help='New, empty output directory')
    args = parser.parse_args(argv)
    if args.time <= 0 or args.exact_time <= 0:
        parser.error('time limits must be positive')
    cfg, fleet = load_routing_case(args.case)
    if cfg['model'] != 3 or not fleet.get('operating_policy'):
        parser.error('requires a single-day Model 3 case')
    if args.fleet_config:
        fleet = {**load_json(args.fleet_config), 'operating_policy': fleet['operating_policy']}
    if args.day_end is not None:
        fleet['operating_policy'] = {**fleet['operating_policy'], 'day_end': args.day_end}
    paths = []
    for pattern in args.data or [cfg['data']]:
        matched = sorted(glob.glob(str(resolve_project_path(pattern))))
        if not matched:
            parser.error(f'no data matched {pattern}')
        paths.extend(Path(p) for p in matched)
    paths = sorted(set(paths))
    distance = load_square_matrix(resolve_project_path(cfg['distance_matrix'])) if cfg.get('distance_matrix') else None
    duration = load_square_matrix(resolve_project_path(cfg['duration_matrix'])) if cfg.get('duration_matrix') else None
    if duration is not None and distance is None:
        parser.error('duration_matrix requires distance_matrix')
    out = resolve_project_path(args.output) if args.output else ROOT / 'output/baselines/single_day_backhaul' / datetime.now().strftime('%Y%m%dT%H%M%S_%f')
    if out.exists() and any(out.iterdir()):
        parser.error(f'output directory is not empty: {out}')
    out.mkdir(parents=True, exist_ok=True)
    revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    sources = [p for base in ('models/routing', 'models/common', 'archive/routing_baselines', 'tools/configuration', 'cli') for p in (ROOT / base).rglob('*.py')]
    sources.extend([ROOT / 'main.py', ROOT / 'models/__init__.py', ROOT / 'archive/__init__.py',
                    ROOT / 'tools/__init__.py', Path(__file__).resolve(),
                    ROOT / 'experiments/analysis/summarize_single_day_backhaul.py'])
    for source in sources:
        target = out / 'source_snapshot' / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    inputs = set(paths) | {resolve_project_path(args.case),
                          resolve_project_path(args.fleet_config or cfg['routing_config'])}
    for key in ('distance_matrix', 'duration_matrix', 'algorithm_config', 'operating_policy'):
        if isinstance(cfg.get(key), str):
            inputs.add(resolve_project_path(cfg[key]))
    packages = {}
    for package in ('gurobipy', 'numpy', 'pandas', 'matplotlib'):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = None
    manifest = dict(arguments=vars(args), run_config=cfg, fleet_config=fleet, git_revision=revision,
                    python=sys.version, package_versions=packages,
                    source_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sources)},
                    input_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(inputs)})
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    runs = []
    variants = []
    if args.solver in ('both', 'exact'):
        variants.extend(('exact', b, args.seeds[0]) for b in dict.fromkeys(args.backends))
    if args.solver in ('both', 'alns'):
        variants.extend(('alns', None, s) for s in dict.fromkeys(args.seeds))
    for index, path in enumerate(paths):
        for method, backend, seed in variants:
            name = f'{index:03d}_{path.stem}_{backend or method}_seed{seed}'
            log = io.StringIO()
            start = time.perf_counter()
            result = dict(data_file=str(path), solver=method, backend=backend, seed=seed,
                          e1_routes=None, e2_routes=None, info={}, route_audit=None)
            try:
                nodes, _ = parse_dataset(str(path))
                p = ProblemInstance(nodes, fleet, speed=fleet.get('speed', 1),
                                    distance_matrix=distance, travel_time_matrix=duration)
                tasks = build_tasks_for_model(p, 3)
                with redirect_stdout(log):
                    solved = solve_routing(allow_baseline=True, model=3, method=method, problem=p,
                        e1_tasks=tasks[0], e2_tasks_by_sat=tasks[1], alns_iter=1_000_000,
                        alns_time=args.time, exact_time=args.exact_time, exact_backend=backend,
                        TFp=cfg.get('TFp', 9999.0), seed=seed)
                result.update(e1_routes=solved[0], e2_routes=solved[1], info=solved[-1],
                              operating_policy=p.operating_policy.as_dict(p))
                if solved[0] is not None and solved[1] is not None:
                    result['route_audit'] = audit_exact_solution(p, 3, *solved[:2],
                        objective=solved[-1].get('objective'), tfp=cfg.get('TFp', 9999.0))
            except Exception as exc:
                result.update(error=f'{type(exc).__name__}: {exc}')
                result['info'] = dict(status='ERROR', proven_optimal=False)
            result['wall_time'] = time.perf_counter() - start
            audit = result['route_audit'] or {}
            valid = bool(audit.get('valid'))
            info = result['info']
            row = dict(run=name, data_file=str(path), solver=method, backend=backend or '', seed=seed,
                       status=info.get('status'), valid=valid,
                       proven_optimal=bool(valid and info.get('proven_optimal')),
                       objective=info.get('objective'), best_bound=info.get('best_bound'),
                       mip_gap=info.get('mip_gap'), wall_time=result['wall_time'],
                       e1_routes=audit.get('e1_route_count'), e2_routes=audit.get('e2_route_count'),
                       e1_waiting=audit.get('time_metrics', {}).get('e1_waiting'),
                       completion_time=audit.get('time_metrics', {}).get('completion_time'),
                       containers_returned=audit.get('container_balance', {}).get('returned_to_depot'),
                       error=result.get('error', '; '.join(audit.get('errors', []))))
            runs.append(row)
            (out / f'{name}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            (out / f'{name}.log').write_text(log.getvalue(), encoding='utf-8')
            with (out / 'runs.csv').open('w', newline='', encoding='utf-8') as handle:
                writer = csv.DictWriter(handle, fieldnames=list(row))
                writer.writeheader()
                writer.writerows(runs)
            print(f"{name}: {row['status']}, valid={valid}, objective={row['objective']}", flush=True)
    from experiments.analysis.summarize_single_day_backhaul import summarize
    summarize(out)
    print(f'Report: {out / "report.md"}')
    return out


if __name__ == '__main__':
    main()
