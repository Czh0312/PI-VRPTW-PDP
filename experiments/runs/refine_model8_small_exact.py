# Historical M8 experiments: retain their original two-stage semantics.
"""Resolve unclosed small M8 cases without changing their M3 baseline.

First try the existing metric zero-pickup-load relaxation certificate. If its
lower bound does not close, rerun only the M8 conditional MILP with more time.
Initial 30-second outputs are retained; refinements are saved separately.
"""
import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

from experiments.runs.run_model8_small_comparison import ROOT, problem_data, write


def worker(args):
    from models.routing.variants.model_3.exact_solver import solve_exact as solve_m3
    from models.routing.variants.model_8.two_stage_v2.exact_solver import solve_exact as solve_m8
    from models.routing.variants.model_8.two_stage_v2.baseline import prepare_baseline
    from models.routing.variants.model_8.two_stage_v2.state import Evaluator
    from models.routing.variants.model_5.exact_solver import _has_metric_distances, _zero_pickup_load_relaxation

    folder = ROOT / args.input
    case = folder / args.instance
    settings = json.loads((folder / 'summary.json').read_text())['settings']
    p, a, b, _, _ = problem_data(SimpleNamespace(scenario=settings['scenario'], instance=args.instance))
    base = json.loads((case / 'm3.json').read_text())
    original = json.loads((case / 'm8_exact.json').read_text())
    count = p.fleet.e2_per_sat_count
    snapshot = prepare_baseline(p, a, b, count, (base['e1_routes'], base['e2_routes']))
    started = time.perf_counter()
    certificate = {'metric_verified': _has_metric_distances(p, a, b),
                   'scope': 'lower_bound_valid_for_M8_by_metric_shortcut',
                   'baseline_fingerprint': snapshot['route_fingerprint']}
    if certificate['metric_verified']:
        _, _, relaxed = solve_m3(p, a, _zero_pickup_load_relaxation(b), count,
                                 time_limit=args.relax_time, verbose=False, return_info=True,
                                 return_incumbent_on_time_limit=True,
                                 warm_start_routes=(base['e1_routes'], base['e2_routes']),
                                 gurobi_params={'Threads': 2, 'Seed': 42, 'MIPGap': 0, 'MIPGapAbs': 0})
        lower = relaxed.get('best_bound')
        certificate.update(relaxation_info=relaxed, lower_bound=lower)
    else:
        lower = None
    upper = original['info']['objective']
    valid = lower is not None and math.isfinite(lower) and abs(lower) < 1e90
    if valid and lower > upper + 1e-5:
        raise RuntimeError('Relaxation bound exceeds feasible M8 incumbent')
    closed = valid and abs(upper - lower) <= 1e-6
    certificate['closed'] = closed
    write(case / 'relaxation_certificate.json', certificate)
    if closed:
        result = deepcopy(original)
        i = result['info']
        i.update(status='CONDITIONAL_OPTIMAL', solution_type='conditional_optimal',
                 conditional_proven_optimal=True,
                 two_stage_proven_optimal=i['stage1_proven_optimal'],
                 conditional_best_bound=lower, conditional_mip_gap=0.0,
                 proof_method='metric_zero_pickup_load_lower_bound_certificate',
                 certificate=certificate)
        # Original MILP TIME_LIMIT remains visible inside stage2.
        result['refinement_method'] = 'valid_lower_bound_certificate'
    else:
        x, y, i = solve_m8(p, a, b, count, base_routes=(base['e1_routes'], base['e2_routes']),
                           base_info=base['info'], time_limit=args.refine_time,
                           verbose=False, return_info=True, return_incumbent_on_time_limit=True,
                           gurobi_params={'Threads': 2, 'Seed': 42, 'MIPFocus': 3})
        if i['objective'] > upper + 1e-6:
            result = deepcopy(original)
            result['failed_to_improve_refinement'] = i
        else:
            result = {'e1_routes': x, 'e2_routes': y, 'info': i}
        result['refinement_method'] = 'extended_conditional_milp'
        result['relaxation_certificate'] = certificate
    audit = Evaluator(p, a, b, snapshot).audit(result['info']['state'])
    assert math.isclose(audit['objective'], result['info']['objective'], abs_tol=1e-6, rel_tol=1e-9)
    result.update(reloaded_audit_valid=True, wall_seconds=time.perf_counter() - started,
                  initial_wall_seconds=original['wall_seconds'], seed=42,
                  budgets={'relaxation': args.relax_time, 'extended_m8': args.refine_time})
    write(case / 'm8_exact_refined.json', result)
    print(json.dumps({'instance': args.instance, 'method': result['refinement_method'],
                      'objective': result['info']['objective'],
                      'proven': result['info']['conditional_proven_optimal'],
                      'lower_bound': result['info']['conditional_best_bound'],
                      'seconds': result['wall_seconds']}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', default='output/experiments/model8_vs_model3_small_20260922/standard_same_day')
    parser.add_argument('--relax-time', type=float, default=20)
    parser.add_argument('--refine-time', type=float, default=120)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--instance')
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    folder = ROOT / args.input
    summary = json.loads((folder / 'summary.json').read_text())
    if not summary['statistics']['complete']:
        raise RuntimeError('Initial batch is not complete')
    pending = [r['instance'] for r in summary['results'] if not r['m8_exact_proven']]
    jobs = []
    for instance in pending:
        command = [sys.executable, '-m', 'experiments.runs.refine_model8_small_exact',
                   '--worker', '--instance', instance, '--input', args.input,
                   '--relax-time', str(args.relax_time), '--refine-time', str(args.refine_time)]
        started = time.perf_counter()
        with (folder / instance / 'refinement.log').open('w') as log:
            try:
                done = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                      timeout=args.relax_time + args.refine_time + 30, check=False)
                status = 'completed' if done.returncode == 0 else f'failed:{done.returncode}'
            except subprocess.TimeoutExpired:
                status = 'hard_timeout'
        job = {'instance': instance, 'status': status, 'seconds': time.perf_counter() - started}
        jobs.append(job)
        write(folder / 'refinement_jobs.json', jobs)
        print(json.dumps(job), flush=True)
        if status != 'completed':
            raise RuntimeError(f'Inspect refinement.log for {instance}')


if __name__ == '__main__':
    main()
