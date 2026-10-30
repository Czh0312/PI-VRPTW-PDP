"""Offline diagnosis using optimal plans, never a cold ALNS performance experiment.

The known plans are deliberately supplied to isolate representation, calendar,
assignment and constructive-route losses. These outcomes must not be reported
as ALNS finding an optimum by itself. Only root seed 42 is used for reconstruction.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.routing.model_8.alns.batch_initialization import balanced_assignments
from models.routing.model_8.alns.calendar_polish import deadline_calendar_seeds
from models.routing.model_8.alns.construction import _best_construction, _seed_from_result
from models.routing.model_8.alns.evaluator import JointEvaluator
from tools.configuration.loader import load_routing_case


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def digest_result(result):
    loads = Counter(result['order_batch_assignment'].values())
    return dict(objective=result['info']['objective'], K=result.get('cycle_batch_count'),
                N=result['container_pool_size'], cost_components=result['cost_components'],
                assignment=result['order_batch_assignment'], batch_order_counts=dict(loads),
                boundaries=result.get('batch_boundaries'), cutoffs=result.get('batch_cutoffs'),
                e1_trips=len(result['e1_trips']), e2_trips=len(result['e2_trips']))


def evaluated(score, result, issues):
    return dict(valid=result is not None, objective=score if result is not None else None,
                issues=list(issues), N=result['container_pool_size'] if result is not None else None,
                cost_components=result['cost_components'] if result is not None else None)


source_hashes = {path.relative_to(ROOT).as_posix(): sha(path)
                for folder in [ROOT/'models/routing/model_8/alns', ROOT/'models/common/cycle']
                for path in folder.rglob('*.py')}
reference_hashes = {}
cases = []
for case in ['S02', 'S03', 'S05']:
    case_path = ROOT/f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json'
    parent = load_instance(*load_routing_case(case_path, use_pool_config=False))
    opt_path = ROOT/f'output/experiments/m8_all_small_rerun_20261029_v1/runs/{case}/s42/best_audited_result.json'
    proof_path = opt_path.with_name('summary.json')
    alns_path = ROOT/f'output/experiments/m8_alns_search_quality_20261029_v1/runs/allocation_validation/{case}/candidate/result.json'
    for path in [case_path, opt_path, proof_path, alns_path]:
        reference_hashes[path.relative_to(ROOT).as_posix()] = sha(path)
    exact, alns, proof = read(opt_path), read(alns_path), read(proof_path)
    assert proof['proven_optimal'] and audit_solution(parent, exact)['valid']
    assert abs(exact['info']['objective']-proof['audited_upper_bound']) < 1e-6
    k, bounds = exact['cycle_batch_count'], exact['batch_boundaries']
    child = parent.fixed_calendar(k, bounds)
    retained, assignment, state = _seed_from_result(child, exact)
    evaluator = JointEvaluator(child)
    encoded = evaluated(*evaluator(assignment, state))
    without_waits = deepcopy(state)
    for route in without_waits['e2']:
        route.pop('depart_after', None)
        route.pop('return_after', None)
    earliest = evaluated(*evaluator(assignment, without_waits))
    midpoint = deepcopy(state)
    midpoint['cutoffs'] = dict(child.default_cutoffs)
    midpoint_cuts = evaluated(*evaluator(assignment, midpoint))

    begun = time.perf_counter()
    cpu_begun = time.process_time()
    score, rebuilt_state, rebuilt, issues = _best_construction(child, evaluator, assignment,
        random.Random(42), time.perf_counter()+2., cutoffs=state['cutoffs'])
    construction = evaluated(score, rebuilt, issues)
    construction.update(wall_seconds=time.perf_counter()-begun, cpu_seconds=time.process_time()-cpu_begun,
                        max_time=2., root_seed=42)
    save(HERE/f'{case}_reconstructed.json', dict(state=rebuilt_state, result=rebuilt, issues=issues))
    variable = parent.for_count(k)
    balanced = balanced_assignments(variable, limit=16, width=64)
    seeds = list(deadline_calendar_seeds(variable))
    opt_visit = [dict(K=v['batch_count'], phase=v['phase'], budget=v['budget'],
        objective=v['objective'], iterations=v['iterations'],
        retiming_attempts=v.get('calendar_retiming_attempts'),
        retiming_feasible=v.get('calendar_retiming_feasible'),
        reconstructions=v.get('calendar_reconstructions'),
        reconstruction_improvements=v.get('calendar_reconstruction_improvements'),
        reconstruction_seconds=v.get('calendar_reconstruction_seconds'),
        operator_attempts=v.get('operator_attempts'), operator_improvements=v.get('operator_improvements'))
        for v in alns['count_search_visits'] if v['batch_count'] == k]
    row = dict(case=case, exact=digest_result(exact), alns=digest_result(alns),
        optimal_plan_decoded_by_ALNS=encoded,
        optimal_plan_with_E2_waits_removed=earliest,
        optimal_plan_with_midpoint_cutoffs=midpoint_cuts,
        reconstruct_given_optimal_calendar_assignment_cutoffs=construction,
        balanced_initialization=dict(count=len(balanced), optimal_assignment_present=assignment in balanced,
             proposed_load_patterns=sorted({tuple(sorted(Counter(a.values()).values())) for a in balanced})),
        deadline_initialization=dict(count=len(seeds),
             optimal_assignment_present=any(a == assignment for _, a, _ in seeds),
             optimal_boundaries_present=any(tuple(b) == tuple(bounds) for b, _, _ in seeds)),
        visits_to_optimal_K=opt_visit)
    cases.append(row)
    print(json.dumps(dict(case=case, exact=row['exact']['objective'], ALNS=row['alns']['objective'],
        optimal_K=k, ALNS_K=row['alns']['K'], optimal_N=row['exact']['N'], ALNS_N=row['alns']['N'],
        reencoded=encoded, no_waits=earliest, midpoint_cutoffs=midpoint_cuts,
        reconstruction=construction, initialization=row['balanced_initialization'],
        visits=[{key: v[key] for key in ['K', 'phase', 'budget', 'objective', 'iterations']}
                for v in opt_visit]), ensure_ascii=False), flush=True)

assert all(sha(ROOT/path) == digest for path, digest in source_hashes.items())
save(HERE/'diagnosis.json', dict(purpose='Offline counterfactual diagnosis; optimal reference plans supplied deliberately.',
     root_seeds=[42], no_cold_ALNS_runs_performed=True, no_exact_solver_invoked=True,
     source_hashes=source_hashes, reference_hashes=reference_hashes, cases=cases))
print('DIAGNOSIS_COMPLETE', flush=True)
