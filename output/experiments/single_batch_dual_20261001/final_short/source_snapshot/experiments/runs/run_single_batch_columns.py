"""Frozen, sequential same-start comparisons of MILP and the B1 column hybrid."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def worker(job_path):
    from configuration.loader import load_routing_case
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    from models.routing.solvers.exact.solver import solve_exact
    from models.routing.solvers.exact.single_batch_columns import solve_with_route_bound
    job = json.loads(Path(job_path).read_text())
    config, fleet = load_routing_case(job['case_path'])
    problem = load_instance(config, fleet)
    initial = json.loads(Path(job['start_path']).read_text())
    if not audit_solution(problem, initial)['valid']:
        raise ValueError('Frozen start failed the current independent audit')
    options = dict(time_limit=job['budget'], seed=job['seed'], initial_result=initial)
    begin = time.perf_counter()
    if job['method'] == 'milp':
        result = solve_exact(problem, **options)
    else:
        result = solve_with_route_bound(problem, root_time=job['root_budget'],
            pricing_method=job['pricing_method'],
            pool_time=job.get('pool_budget', 0) if job['method'] in ('columns_pool', 'columns_time', 'columns_dual') else 0,
            time_cuts=job['method'] == 'columns_time',
            dual_cuts=job['method'] == 'columns_dual',
            **options)
    wall = time.perf_counter()-begin
    audit = audit_solution(problem, result)
    result['audit'] = audit
    write(Path(job['result_path']), result)
    info = result['info']
    if not audit['valid']:
        raise RuntimeError('Result failed independent audit: '+str(audit['errors']))
    write(Path(job['row_path']), dict(case=job['case'], method=job['method'], seed=job['seed'],
        budget=job['budget'], valid=True, replay=audit['periodic_replay']['valid'],
        proven_optimal=info['proven_optimal'], proven_infeasible=info['proven_infeasible'],
        objective=info['objective'], bound=info.get('best_bound'), gap=info.get('mip_gap'),
        wall_time=wall, root_time=info.get('route_bound_certificate', {}).get('runtime', 0),
        root_bound=info.get('route_bound_certificate', {}).get('bound'),
        root_status=info.get('route_bound_certificate', {}).get('status'),
        time_rows=info.get('route_bound_certificate', {}).get('time_rows'),
        dual_cuts=info.get('single_batch_route_dual_cuts', 0),
        pool=info.get('route_pool_primal'),
        floor=info.get('single_batch_route_floor'), nodes=info.get('mip_node_count'),
        status=info['status']))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker')
    parser.add_argument('--frozen-runs', nargs='+')
    parser.add_argument('--seeds', type=int, nargs='+', default=[42, 43])
    parser.add_argument('--total-time', type=float, default=30)
    parser.add_argument('--root-time', type=float, default=10)
    parser.add_argument('--pricing-method', choices=('label', 'mip'), default='label')
    parser.add_argument('--methods', choices=('milp', 'columns', 'columns_pool', 'columns_time', 'columns_dual'), nargs='+', default=['milp', 'columns'])
    parser.add_argument('--pool-time', type=float, default=3)
    parser.add_argument('--output')
    args = parser.parse_args(argv)
    if args.worker:
        return worker(args.worker)
    if not args.frozen_runs or not args.output:
        parser.error('Provide frozen result directories and a new output directory')
    if any(not math.isfinite(t) or t <= 0 for t in (args.total_time, args.root_time)):
        parser.error('Budgets must be finite and positive')
    if not math.isfinite(args.pool_time) or args.pool_time < 0 or len(set(args.methods)) != len(args.methods):
        parser.error('Pool budget must be finite and nonnegative; methods must be distinct')
    out = Path(args.output).resolve()
    if out.exists() and any(out.iterdir()):
        parser.error('Refusing to overwrite a nonempty experiment directory')
    out.mkdir(parents=True, exist_ok=True)
    from configuration.loader import load_routing_case, resolve_project_path
    from experiments.runs.run_multibatch_cycle import INPUT_KEYS, freeze_inputs, source_snapshot
    from models.routing.common.cycle import load_instance
    from models.routing.common.cycle.audit import audit_solution
    variants, starts = [], []
    for value in args.frozen_runs:
        source = resolve_project_path(value).resolve()
        paths = list((source/'inputs').glob('00_*.json'))
        if len(paths) != 1:
            raise ValueError(f'Expected one frozen case in {source}')
        config, fleet = load_routing_case(paths[0])
        p = load_instance(config, fleet)
        if p.model_number not in (1, 2):
            raise ValueError('This prototype only supports M1/M2')
        initial = json.loads((source/'result.json').read_text())
        if not audit_solution(p, initial)['valid']:
            raise ValueError(f'Invalid shared start for {source.name}')
        sources = {resolve_project_path(config[k]).resolve() for k in INPUT_KEYS if isinstance(config.get(k), str)}
        hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
        variants.append(dict(config=deepcopy(config), _source_hashes=hashes, case=source.name))
        starts.append(initial)
    inputs = freeze_inputs(variants, out)
    sources = source_snapshot(out)
    jobs, rows = [], []
    for variant, initial in zip(variants, starts):
        folder = out/variant['case']
        folder.mkdir()
        case_path, start_path = folder/'case.json', folder/'start.json'
        write(case_path, variant['config'])
        write(start_path, initial)
        for seed in args.seeds:
            # Alternate method order between seeds to reduce order effects.
            methods = args.methods if seed % 2 == 0 else list(reversed(args.methods))
            for method in methods:
                run = folder/f'{method}_{seed}'
                run.mkdir()
                job = dict(case=variant['case'], method=method, seed=seed,
                    budget=args.total_time, root_budget=args.root_time,
                    pool_budget=args.pool_time,
                    pricing_method=args.pricing_method,
                    case_path=str(case_path), start_path=str(start_path),
                    result_path=str(run/'result.json'), row_path=str(run/'row.json'))
                job_path = run/'job.json'
                write(job_path, job)
                jobs.append((job_path, job))
    write(out/'manifest.json', dict(arguments=vars(args), input_sha256=inputs, source_sha256=sources,
        job_count=len(jobs), threads=1, execution='sequential isolated subprocesses; no retries',
        shared_start='same audited frozen start for every method and seed of each case',
        baseline='full joint MILP kernel with supplied start; excludes assignment seed/warm wrapper',
        budget='one total wall-clock solver budget including CG, optional pool selection/scheduling, and full MILP construction'))
    runner = out/'source_snapshot/experiments/runs/run_single_batch_columns.py'
    for index, (job_path, job) in enumerate(jobs, 1):
        process = subprocess.run([sys.executable, str(runner), '--worker', str(job_path)],
            cwd=out/'source_snapshot', capture_output=True, text=True, timeout=args.total_time+30)
        job_path.with_name('solver.log').write_text(process.stdout+'\n'+process.stderr)
        if process.returncode:
            row = dict(case=job['case'], seed=job['seed'], method=job['method'], error=process.stderr,
                       valid=False, status='ERROR')
        else:
            row = json.loads(Path(job['row_path']).read_text())
        rows.append(row)
        write(out/'comparison.json', rows)
        print(f'{index}/{len(jobs)} {job["case"]} {job["method"]} seed={job["seed"]}: '
              f'{row.get("status")} obj={row.get("objective")} bound={row.get("bound")} '
              f'gap={row.get("gap")}', flush=True)
    return out


if __name__ == '__main__':
    main()
