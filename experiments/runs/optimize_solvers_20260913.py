"""Reproducible serial solver comparisons; preserves every route and source hash."""
import argparse
import contextlib
import hashlib
import importlib
import io
import json
import math
import os
import platform
import shutil
import sys
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('--source-root')
    parser.add_argument('--method', choices=['exact', 'alns'], required=True)
    parser.add_argument('--models', nargs='+', type=int, default=[1, 2, 3, 4, 6, 7])
    parser.add_argument('--instances', nargs='+')
    parser.add_argument('--scale', default='small')
    parser.add_argument('--scenario', default='standard_same_day')
    parser.add_argument('--seconds', type=float, default=5)
    parser.add_argument('--seeds', nargs='+', type=int, default=[42, 43, 44])
    parser.add_argument('--options', default='{}')
    args = parser.parse_args()
    out = ROOT / args.output
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT))
    source_root = ROOT / 'output/tmp/solver_baseline_20260913' if args.baseline else out / 'source'
    if args.source_root:
        source_root = ROOT / args.source_root
    if not args.baseline and not (source_root / 'models').exists():
        shutil.copytree(ROOT / 'models', source_root / 'models', ignore=shutil.ignore_patterns('__pycache__'))
    sys.path.insert(0, str(source_root))
    sys.path.append(r'C:\gurobi\win64\python311\lib')
    from experiments.runs.run_model12346_alns_scaling import load_problem, solve_one, route_cost
    from models.routing.common.task_builder import build_tasks_for_model
    from models.routing.common.solution_audit import audit_exact_solution

    paths = sorted((ROOT / f'data/zhou_later_pickup/{args.scale}/{args.scenario}').glob('*.csv'))
    if args.instances:
        paths = [p for p in paths if p.stem in args.instances]
        assert len(paths) == len(args.instances), 'Unknown instance'
    fleet_path = ROOT / f'config/routing/validation/zhou_{args.scale}.json'
    fleet = json.loads(fleet_path.read_text(encoding='utf-8'))
    options = json.loads(args.options)
    sources = sorted((source_root / 'models').rglob('*.py'))
    manifest = dict(arguments=vars(args), platform=platform.platform(), python=sys.version,
                    python_hash_seed=os.environ.get('PYTHONHASHSEED'),
                    experiment_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in [Path(__file__),ROOT/'experiments/runs/run_model12346_alns_scaling.py']},
                    solver_source=str(source_root), fleet=fleet,
                    sha256={str(p.relative_to(source_root)): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in sources},
                    input_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in paths + [fleet_path]},
                    protocol='independent serial runs; wall budget includes initialization; no reference target supplied')
    manifest_path = out / 'manifest.json'
    if manifest_path.exists():
        assert json.loads(manifest_path.read_text(encoding='utf-8')) == manifest, 'Changed source/config: use a fresh output'
    else:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    result_path = out / 'runs.jsonl'
    rows = [json.loads(s) for s in result_path.read_text(encoding='utf-8').splitlines()] if result_path.exists() else []
    done = {(r['instance'], r['model'], r['seed']) for r in rows}
    seeds = args.seeds if args.method == 'alns' else args.seeds[:1]
    with result_path.open('a', encoding='utf-8') as handle:
        for path in paths:
            for seed in seeds:
                for model in args.models:
                    if (path.stem, model, seed) in done:
                        continue
                    problem = load_problem(path, fleet)
                    row = dict(instance=path.stem, customers=len(problem.customers), model=model,
                               seed=seed, method=args.method, budget=args.seconds, scale=args.scale,
                               scenario=args.scenario, baseline=args.baseline)
                    start = time.perf_counter()
                    log = io.StringIO()
                    try:
                        with contextlib.redirect_stdout(log):
                            if args.method == 'alns':
                                if options:
                                    solver = importlib.import_module(f'models.routing.alns.variants.model_{model}').solve_alns
                                    kwargs = dict(problem=problem, vehicles_per_satellite=problem.fleet.e2_per_sat_count,
                                                  max_time=args.seconds, max_iterations=1_000_000, seed=seed,
                                                  verbose=False, return_info=True, **options)
                                    if model in (1, 3, 6):
                                        kwargs['e1_tasks'], kwargs['e2_tasks_by_sat'] = build_tasks_for_model(problem, model)
                                    result = solver(**kwargs)
                                    r1, r2, assignment, info = result if model in (2, 4, 7) else (*result[:2], None, result[-1])
                                else:
                                    r1, r2, assignment, info = solve_one(model, problem, max_time=args.seconds,
                                        iterations=1_000_000, seed=seed)
                            else:
                                e1, e2 = build_tasks_for_model(problem, model)
                                solver = importlib.import_module(f'models.routing.variants.model_{model}').solve_exact
                                result = solver(problem=problem, e1_tasks=e1, e2_tasks_by_sat=e2,
                                    vehicles_per_satellite=problem.fleet.e2_per_sat_count,
                                    time_limit=args.seconds, verbose=False, return_info=True,
                                    return_incumbent_on_time_limit=True, gurobi_params={'Threads': 4, 'Seed': seed},
                                    **options)
                                r1, r2, assignment, info = result if model in (2, 4, 7) else (*result[:2], None, result[-1])
                        elapsed = time.perf_counter() - start
                        objective = info.get('objective')
                        cost = route_cost(problem, r1, r2)
                        audit = None
                        if r1 is not None and not info.get('transshipment_count', 0):
                            audit_r1 = ([[n[:-1] if n.endswith('d') and n[:-1] in {s.id for s in problem.satellites} else n for n in r] for r in r1]
                                        if model == 1 else r1)
                            audit = audit_exact_solution(problem, {6: 3, 7: 4}.get(model, model),
                                audit_r1, r2, assignment=assignment, objective=objective)
                        valid = bool(cost is not None and objective is not None and abs(cost-objective) < 1e-5
                                     and (audit['valid'] if audit else info.get('feasible', False)))
                        row.update(objective=objective, wall_time=elapsed, valid=valid,
                                   proven_optimal=bool(info.get('proven_optimal')), best_bound=info.get('best_bound'),
                                   mip_gap=info.get('mip_gap'), audit=audit, info=info,
                                   e1_routes=r1, e2_routes=r2, assignment=assignment,
                                   relay_audit='model evaluator plus independent distance' if audit is None else None)
                    except Exception as exc:
                        import traceback
                        row.update(objective=None, valid=False, proven_optimal=False,
                                   error=repr(exc), traceback=traceback.format_exc(), wall_time=time.perf_counter()-start)
                    if row.get('error') or (args.method == 'exact' and not row['proven_optimal']):
                        (out / f"{path.stem}_M{model}_{seed}.log").write_text(log.getvalue(), encoding='utf-8')
                    handle.write(json.dumps(row, ensure_ascii=False) + '\n')
                    handle.flush()
                    rows.append(row)
                    print(f"{len(rows)} {path.stem} M{model} s{seed}: obj={row['objective']} valid={row['valid']} optimal={row['proven_optimal']} t={row['wall_time']:.3f} {row.get('error','')}", flush=True)
    assert all(hashlib.sha256((source_root / p).read_bytes()).hexdigest() == h for p, h in manifest['sha256'].items())
    print('COMPLETE', flush=True)


if __name__ == '__main__':
    if os.environ.get('PYTHONHASHSEED') != '0':
        raise SystemExit(subprocess.call([sys.executable,__file__,*sys.argv[1:]],
            env={**os.environ,'PYTHONHASHSEED':'0'}))
    main()
