"""Budget/proof aggregation for M7/M8; algorithm imports stay lazy."""
from __future__ import annotations

from copy import deepcopy
import math
import time

from models.routing.common.cycle.calendar import STRATEGIES, attach_calendar_decisions, audit_calendar_solution
from models.routing.common.cycle.joint import joint_empty_result


def solve_counts(problem, *, method, budget, seed, max_iterations=0, initial_result=None, verbose=False):
    if isinstance(budget, bool) or not math.isfinite(budget) or budget <= 0:
        raise ValueError('Solver time budget must be finite and positive')
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations < 0:
        raise ValueError('ALNS iteration budget must be a nonnegative integer')
    begun = time.perf_counter()
    deadline = begun+budget
    best = None
    if initial_result is not None:
        checked = audit_calendar_solution(problem, initial_result)
        if not checked['valid']:
            raise ValueError(f'Initial calendar solution fails independent audit: {checked["errors"]}')
        best = deepcopy(initial_result)
        best['info'] = dict(objective=checked['objective'], proven_optimal=False,
                            proven_infeasible=False, status='FEASIBLE')
    rows = []
    remaining_iterations = max_iterations
    for index, count in enumerate(problem.batch_count_candidates):
        unresolved = len(problem.batch_count_candidates)-index
        allocation = max(0., (deadline-time.perf_counter())/unresolved)
        row = dict(batch_count=count, budget=allocation, status='NOT_RUN_TIME_LIMIT', objective=None,
                   valid=False, proven_optimal=False, proven_infeasible=False, best_bound=None,
                   iterations=0, runtime=0.)
        if allocation <= 0:
            rows.append(row)
            continue
        child = problem.for_count(count)
        allocation = max(0., (deadline-time.perf_counter())/unresolved)
        row['budget'] = allocation
        if allocation <= 0:
            rows.append(row)
            continue
        if method == 'exact':
            from models.routing.variants.model_6.exact.solver import solve_exact
            candidate = solve_exact(child, time_limit=allocation, seed=seed, verbose=verbose)
        else:
            iterations = remaining_iterations//unresolved
            seed_result = initial_result if initial_result is not None and initial_result['cycle_batch_count'] == count else None
            if problem.model_number == 8:
                from models.routing.variants.model_8.alns.search import solve_variable_count
                candidate = solve_variable_count(child, max_time=allocation, max_iterations=iterations,
                                                 seed=seed, initial_result=seed_result)
            else:
                from models.routing.variants.model_6.alns.solver import solve_alns
                candidate = solve_alns(child, max_time=allocation, max_iterations=iterations,
                                       seed=seed, initial_result=seed_result)
            remaining_iterations -= candidate['info'].get('iterations', 0)
        info = candidate['info']
        valid = candidate.get('audit', {}).get('valid', False)
        if valid:
            boundaries = [b['start'] for b in candidate['batches']]+[problem.operating_policy.cycle_end]
            attach_calendar_decisions(problem, candidate, count, boundaries)
            candidate['audit'] = audit_calendar_solution(problem, candidate)
            valid = candidate['audit']['valid']
            if not valid:
                info.update(status='AUDIT_FAILED', objective=None, proven_optimal=False)
        row.update(status=info['status'], objective=info.get('objective') if valid else None,
                   valid=valid, proven_optimal=bool(valid and info.get('proven_optimal')),
                   proven_infeasible=info.get('proven_infeasible', False), best_bound=info.get('best_bound'),
                   runtime=info.get('runtime', 0.), iterations=info.get('iterations', 0),
                   container_pool_size=candidate.get('container_pool_size') if valid else None,
                   operator_attempts=info.get('operator_attempts', {}),
                   operator_improvements=info.get('operator_improvements', {}),
                   evaluations=info.get('evaluations', 0), cache_hits=info.get('cache_hits', 0),
                   calendar_evaluations=info.get('calendar_evaluations', 0))
        rows.append(row)
        if valid and (best is None or info['objective'] < best['info']['objective']-1e-8):
            best = candidate
    resolved = all(r['proven_optimal'] or r['proven_infeasible'] for r in rows)
    infeasible = best is None and all(r['proven_infeasible'] for r in rows)
    bounds = [r['best_bound'] for r in rows if not r['proven_infeasible']]
    bound = min(bounds) if bounds and all(v is not None and math.isfinite(v) for v in bounds) else None
    # A count need not finish its own search if its certified lower bound
    # already rules out improving the audited incumbent from another count.
    # Unknown bounds (including unrun counts) and failed audits block this
    # certificate. Use a strict comparison without rounding away a gap.
    dominated = (best is not None and bound is not None and bound >= best['info']['objective']
                 and all(r['status'] != 'AUDIT_FAILED' for r in rows))
    optimal = method == 'exact' and best is not None and (resolved or dominated)
    result = best if best is not None else joint_empty_result(problem, method)
    if best is None:
        result.update(cycle_batch_count=None, batch_boundaries=None, batch_durations_ticks=None,
                      cutoff_offsets_ticks=None, cutoff_ratios=None, actual_recovery_round_count=None)
    result.update(model=problem.model_number, strategy=STRATEGIES[problem.model_number], solver=method,
                  model_module=f'models.routing.variants.model_{problem.model_number}',
                  solver_module=f'models.routing.variants.model_{problem.model_number}.{method}.solver',
                  schedule_mode=problem.schedule_mode, batch_mode=problem.schedule_mode,
                  calendar_config=problem.calendar_contract, count_search=rows)
    def operators(key):
        total = {}
        for row in rows:
            for op, value in row.get(key, {}).items():
                total[op] = total.get(op, 0)+value
        return total
    result['info'].update(status='OPTIMAL' if optimal else 'INFEASIBLE' if infeasible else
                          'FEASIBLE' if best is not None else 'TIME_LIMIT' if method == 'exact' else 'HEURISTIC_NO_SOLUTION',
                          proven_optimal=optimal, proven_infeasible=infeasible,
                          best_bound=bound if method == 'exact' else None,
                          mip_gap=(max(0., result['info']['objective']-bound)/max(1e-9, abs(result['info']['objective']))
                                   if method == 'exact' and best is not None and bound is not None else None),
                          runtime=time.perf_counter()-begun, time_budget=budget, seed=seed,
                          iterations=sum(r['iterations'] for r in rows),
                          iteration_budget=max_iterations if method == 'alns' else None,
                          operator_attempts=operators('operator_attempts'),
                          operator_improvements=operators('operator_improvements'),
                          evaluations=sum(r.get('evaluations', 0) for r in rows),
                          cache_hits=sum(r.get('cache_hits', 0) for r in rows),
                          calendar_evaluations=sum(r.get('calendar_evaluations', 0) for r in rows),
                          history_scope='selected_batch_count_only',
                          batch_count_candidates=list(problem.batch_count_candidates),
                          selected_count_proven_optimal=next((r['proven_optimal'] for r in rows
                                    if r['batch_count'] == result.get('cycle_batch_count')), False),
                          proof_aggregation=('all_counts_resolved' if resolved else 'all_count_bounds_dominate_incumbent')
                                            if optimal else None,
                          initial_incumbent_cost=initial_result['info']['objective'] if initial_result else None,
                          proof_scope=f'model_{problem.model_number}_all_configured_counts_integer_time_cost_MILP'
                                      if method == 'exact' else None)
    if best is not None:
        result['audit'] = audit_calendar_solution(problem, result)
        if not result['audit']['valid']:
            result['info'].update(status='AUDIT_FAILED', proven_optimal=False, objective=None)
    return result
