"""Run selected cases from the current six-model benchmark."""
import argparse
from datetime import datetime
import math
from pathlib import Path

from cli.multibatch import run_case
from cli.no_batch import run_case as run_no_batch
from tools.configuration.loader import PROJECT_ROOT, load_routing_case, resolve_project_path
from tools.data.build_current_benchmark import TARGET, read, write


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', nargs='+', type=int, choices=range(1, 7), required=True)
    parser.add_argument('--cases', nargs='+')
    parser.add_argument('--sizes', nargs='+', choices=('small', 'medium', 'large'))
    parser.add_argument('--solver', choices=('exact', 'alns'), required=True)
    parser.add_argument('--seconds', type=float, required=True)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--output')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error('--seconds must be positive and finite')
    if not 0 <= args.seed <= 2_000_000_000:
        parser.error('--seed must be between 0 and 2000000000')
    rows = read(TARGET/'manifest.json')['cases']
    known = {row['base_case_id'] for row in rows}
    if args.cases and set(args.cases) - known:
        parser.error(f'Unknown case IDs: {sorted(set(args.cases)-known)}')
    jobs = [row for row in rows if row['model'] in args.models
            and (not args.cases or row['base_case_id'] in args.cases)
            and (not args.sizes or row['size'] in args.sizes)]
    if not jobs:
        parser.error('No cases matched the selection')
    plan = dict(solver=args.solver, seconds_per_case=args.seconds, seed=args.seed, jobs=jobs)
    if args.dry_run:
        for job in jobs:
            print(f"M{job['model']} {job['base_case_id']} {job['case_path']}")
        return plan
    out = (resolve_project_path(args.output) if args.output else
           PROJECT_ROOT/'output/current/benchmark'/datetime.now().strftime('%Y%m%dT%H%M%S_%f'))
    if out.exists() and any(out.iterdir()):
        parser.error(f'Use a new or empty directory: {out}')
    write(out/'plan.json', plan)
    results = []
    for job in jobs:
        path = Path(job['case_path'])
        number, case_id = job['model'], job['base_case_id']
        run_dir = out/f'M{number}_{case_id}'
        row = dict(model=number, case=case_id, path=run_dir.name)
        try:
            config, fleet = load_routing_case(path, use_pool_config=False)
            config.update(solver=args.solver, seed=args.seed, alns_time=args.seconds,
                          exact_time=args.seconds)
            result = (run_no_batch if number == 6 else run_case)(
                config, fleet, case_path=path, output=run_dir)
            info = result['info']
            audit = result.get('audit', {})
            row.update(status=info['status'], valid=audit.get('valid', False),
                       periodic_replay=audit.get('periodic_replay', {}).get('valid', False),
                       objective_mode=info.get('objective_mode', result.get('objective_mode', 'distance')),
                       objective=info.get('objective'), proven_optimal=info.get('proven_optimal', False),
                       proven_infeasible=info.get('proven_infeasible', False))
        except (OSError, ValueError, RuntimeError) as exc:
            row.update(status='ERROR', valid=False, error=str(exc))
        results.append(row)
        write(out/'summary.json', results)
    return results


if __name__ == '__main__':
    main()
