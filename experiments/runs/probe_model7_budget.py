"""Paired M7 budget experiments; every run retains routes and source hashes."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.runs.run_model12346_alns_scaling import (
    as_solution, load_problem, route_cost, solve_one,
)
from models.routing.alns.variants.model_7 import solve_alns


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--seconds', type=float, default=5)
    parser.add_argument('--seeds', nargs='+', type=int, default=[43, 44])
    parser.add_argument('--variants', nargs='+', default=['m4', 'm7', 'lean'])
    parser.add_argument('--screen', action='store_true')
    parser.add_argument('--extra-relay', type=float, default=0)
    args = parser.parse_args()
    out = ROOT / args.output
    out.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__), *sorted((ROOT / 'models/routing').rglob('*.py'))]
    metadata = vars(args) | {'source_sha256': {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sources}}
    (out / 'settings.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    fleet = json.loads((ROOT / 'config/routing/validation/zhou_large.json').read_text())
    paths = sorted((ROOT / 'data/zhou_later_pickup/large/standard_same_day').glob('*.csv'))
    if args.screen:
        paths = paths[::3]
    rows = []
    for index, path in enumerate(paths):
        for seed in args.seeds:
            variants = list(args.variants)
            shift = (index + seed) % len(variants)
            variants = variants[shift:] + variants[:shift]
            for variant in variants:
                problem = load_problem(path, fleet)
                started = time.perf_counter()
                common = dict(max_time=args.seconds, iterations=1_000_000, seed=seed)
                if variant in ('m4', 'm7'):
                    r1, r2, assignment, info = solve_one(4 if variant == 'm4' else 7, problem, **common)
                elif variant == 'lean':
                    r1, r2, assignment, info = solve_alns(
                        problem, vehicles_per_satellite=problem.fleet.e2_per_sat_count,
                        max_time=args.seconds, max_iterations=1_000_000, seed=seed,
                        verbose=False, return_info=True, stage1_time_ratio=0.95,
                        assignment_pool_size=1, transshipment_time_budget=args.seconds * 0.05,
                        transshipment_sample_pickups=12, transshipment_sample_locations=6,
                        transshipment_sample_carriers=4, transshipment_position_top_k=4)
                else:
                    raise ValueError(variant)
                elapsed = time.perf_counter() - started
                cost = route_cost(problem, r1, r2)
                feasible = bool(info.get('feasible')) and cost is not None
                delta = cost - info['objective'] if feasible else None
                if feasible and abs(delta) > 1e-6:
                    raise AssertionError((path.name, variant, delta))
                row = dict(instance=path.stem, seed=seed, variant=variant,
                           budget=args.seconds, wall_time=elapsed, feasible=feasible,
                           objective=info.get('objective') if feasible else None,
                           route_cost_delta=delta, info=info, e1_routes=r1, e2_routes=r2,
                           assignment=assignment)
                rows.append(row)
                with (out / 'runs.jsonl').open('a', encoding='utf-8') as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + '\n')
                print(f'{path.stem} seed={seed} {variant}: {row["objective"]} ({elapsed:.3f}s)', flush=True)
                if variant == 'm4' and feasible and args.extra_relay > 0:
                    # Mechanism test only: this receives EXTRA time and must not
                    # be confused with the equal-total-budget comparison.
                    t0 = time.perf_counter()
                    a, b, assignment, extra = solve_alns(
                        problem, vehicles_per_satellite=problem.fleet.e2_per_sat_count,
                        initial_solution=as_solution(r1, r2), max_time=args.extra_relay,
                        max_iterations=0, stage1_time_ratio=0, assignment_pool_size=1,
                        refill_unused_time=False, verbose=False, return_info=True,
                        seed=seed, transshipment_time_budget=args.extra_relay,
                        transshipment_sample_pickups=12, transshipment_sample_locations=6,
                        transshipment_sample_carriers=4, transshipment_position_top_k=4)
                    audited = route_cost(problem, a, b)
                    if not extra['feasible'] or abs(audited - extra['objective']) > 1e-6:
                        raise AssertionError('Warm-start relay audit failed')
                    warm = dict(instance=path.stem, seed=seed, variant='m4_plus_relay',
                                budget=args.seconds + args.extra_relay,
                                wall_time=elapsed + time.perf_counter()-t0, feasible=True,
                                objective=extra['objective'], baseline_objective=row['objective'],
                                route_cost_delta=audited-extra['objective'], info=extra,
                                e1_routes=a, e2_routes=b, assignment=assignment)
                    rows.append(warm)
                    with (out / 'runs.jsonl').open('a', encoding='utf-8') as handle:
                        handle.write(json.dumps(warm, ensure_ascii=False) + '\n')
                    print(f'  extra relay: {warm["objective"]}', flush=True)
    lines = ['# Model 7 budget probe', '',
             'Equal-budget runs are compared on common feasible instance/seed pairs. '
             'm4_plus_relay uses extra time and only tests marginal relay benefit.', '',
             '| Variant | Feasible | Mean seconds | Gain vs M4 | W/T/L |',
             '| --- | ---: | ---: | ---: | --- |']
    base = {(r['instance'], r['seed']): r for r in rows if r['variant']=='m4' and r['feasible']}
    for variant in dict.fromkeys(r['variant'] for r in rows):
        group = [r for r in rows if r['variant']==variant]
        good = [r for r in group if r['feasible']]
        gains = [(base[r['instance'], r['seed']]['objective']-r['objective']) /
                 base[r['instance'], r['seed']]['objective'] * 100
                 for r in good if (r['instance'], r['seed']) in base]
        wins = sum(g>1e-7 for g in gains)
        losses = sum(g < -1e-7 for g in gains)
        lines.append(f'| {variant} | {len(good)}/{len(group)} | '
                     f'{statistics.mean(r["wall_time"] for r in group):.3f} | '
                     f'{statistics.mean(gains):.4f}% | {wins}/{len(gains)-wins-losses}/{losses} |')
    (out / 'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
