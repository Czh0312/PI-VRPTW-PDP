"""Independent model-local K scheduler with shared budgets and certificates.

The adaptive policy scouts small counts, explores the remaining domain, then
refines the best audited count. The recycle/single-pass policies retain equal
shares for comparisons. Revisits restart from physical incumbents, not saved
ALNS states. No candidate is removed by heuristic ranking.
"""
from __future__ import annotations

from copy import deepcopy
import math
import time

from models.common.cycle.joint import joint_empty_result
from models.common.cycle.calendar import STRATEGIES, attach_calendar_decisions, audit_calendar_solution


def _audit(problem, result):
    return audit_calendar_solution(problem, result)


def _attach(problem, child, result, count):
    boundaries = [b['start'] for b in result['batches']] + [child.operating_policy.cycle_end]
    attach_calendar_decisions(problem, result, count, boundaries)


def _metadata(problem, result, method, found):
    result.update(model=problem.model_number, strategy=STRATEGIES[problem.model_number], solver=method,
                  model_module=f'models.routing.model_{problem.model_number}.{method}',
                  solver_module=f'models.routing.model_{problem.model_number}.{method}.solver',
                  schedule_mode=problem.schedule_mode, batch_mode=problem.schedule_mode,
                  calendar_config=problem.calendar_contract)
    if not found:
        result.update(cycle_batch_count=None, batch_boundaries=None, batch_durations_ticks=None,
                      cutoff_offsets_ticks=None, cutoff_ratios=None, actual_recovery_round_count=None)

def _finite(value):
    return value is not None and not isinstance(value, bool) and math.isfinite(value)


def _certificate(rows, best, method):
    # For a union of K domains, take the maximum bound within K and minimum
    # across K. Unknown/unvisited bounds cannot be silently replaced by zero.
    clean = all(not r['audit_failed'] and not r['certificate_errors'] for r in rows)
    infeasible = bool(rows) and best is None and clean and all(r['proven_infeasible'] for r in rows)
    remaining = [r for r in rows if not r['proven_infeasible']]
    bound = (min(r['best_bound'] for r in remaining)
             if remaining and all(_finite(r['best_bound']) for r in remaining) else None)
    resolved = bool(rows) and all(r['proven_optimal'] or r['proven_infeasible'] for r in rows)
    dominated = best is not None and bound is not None and bound >= best['info']['objective']
    optimal = method == 'exact' and best is not None and clean and (resolved or dominated)
    reason = ('all_counts_resolved' if resolved else 'all_count_bounds_dominate_incumbent') if optimal else None
    return optimal, infeasible, bound if clean and method == 'exact' else None, reason


def solve_counts(problem, *, solve_candidate, method, budget, seed, max_iterations=0,
                  initial_result=None, verbose=False, budget_strategy='recycle'):
    if method not in ('alns', 'exact'):
        raise ValueError('Expected alns or exact')
    if isinstance(budget, bool) or not math.isfinite(budget) or budget <= 0:
        raise ValueError('Solver time budget must be finite and positive')
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations < 0:
        raise ValueError('ALNS iteration budget must be a nonnegative integer')
    if budget_strategy not in ('single_pass', 'recycle', 'adaptive'):
        raise ValueError('Expected single_pass, recycle or adaptive budget strategy')
    if budget_strategy == 'adaptive' and method != 'alns':
        raise ValueError('Adaptive count allocation requires ALNS')
    begun = time.perf_counter()
    deadline = begun + budget
    counts = tuple(problem.batch_count_candidates)
    if not counts or len(set(counts)) != len(counts):
        raise ValueError('Expected a nonempty unique K domain')
    rows = [dict(batch_count=k, status='NOT_RUN_TIME_LIMIT', objective=None, valid=False,
                 proven_optimal=False, proven_infeasible=False, best_bound=None,
                 budget=0., runtime=0., construction_time=0., iterations=0, iteration_budget=0,
                 visits=[], audit_failed=False, certificate_errors=[]) for k in counts]
    by_count = {r['batch_count']: r for r in rows}
    incumbents, visits = {}, []
    best = None
    anytime = [dict(elapsed=0., objective=None, batch_count=None, source='call_start')]
    if initial_result is not None:
        checked = _audit(problem, initial_result)
        if not checked['valid']:
            raise ValueError(f'Initial solution fails independent audit: {checked["errors"]}')
        best = deepcopy(initial_result)
        best['info'] = dict(objective=checked['objective'], status='FEASIBLE',
                            proven_optimal=False, proven_infeasible=False)
        count = best['cycle_batch_count']
        incumbents[count] = best
        by_count[count].update(objective=checked['objective'], valid=True, status='FEASIBLE')
        anytime.append(dict(elapsed=time.perf_counter()-begun, objective=checked['objective'],
                            batch_count=count, source='audited_initial_incumbent'))
    remaining_iterations = max_iterations
    round_number = 0
    stop_reason = 'TIME_LIMIT'
    while time.perf_counter() < deadline:
        optimal, infeasible, _, _ = _certificate(rows, best, method)
        if optimal or infeasible:
            stop_reason = 'PROOF_COMPLETE'
            break
        active = [r for r in rows if not (r['proven_optimal'] or r['proven_infeasible']
                                         or r['audit_failed'] or r['certificate_errors'])]
        if not active:
            stop_reason = 'NO_ELIGIBLE_CANDIDATE'
            break
        if round_number and budget_strategy == 'single_pass':
            stop_reason = 'SINGLE_PASS_COMPLETE'
            break
        if method == 'alns' and remaining_iterations == 0 and (round_number or max_iterations > 0):
            stop_reason = 'ITERATION_LIMIT'
            for row in active:
                if not row['visits']:
                    row['status'] = 'NOT_RUN_ITERATION_LIMIT'
            break
        phase_deadline = deadline
        phase = 'recycle'
        weights = None
        if budget_strategy == 'adaptive' and len(counts) > 3 and max_iterations > 0:
            if round_number == 0:
                phase = 'scout'
                active = sorted(active, key=lambda r: r['batch_count'])[:3]
                phase_deadline = begun + .5 * budget
                weights = [4., 1., 1.][:len(active)]
            elif round_number == 1:
                phase = 'explore'
                active = [r for r in active if not r['visits']]
                phase_deadline = begun + .75 * budget
            else:
                phase = 'refine'
                feasible = [r for r in active if r['valid']]
                active = ([min(feasible, key=lambda r: r['objective'])] if feasible else
                          sorted(active, key=lambda r: (len(r['visits']), r['batch_count']))[:3])
        round_number += 1
        # Construction/auditing may exhaust a phase. Advance to the next one
        # without borrowing its reserved time or dropping unvisited K rows.
        if not active or time.perf_counter() >= phase_deadline:
            continue
        round_start = time.perf_counter()
        for index, row in enumerate(active):
            if time.perf_counter() >= phase_deadline:
                break
            if method == 'alns' and max_iterations > 0 and remaining_iterations <= 0:
                break
            count = row['batch_count']
            construction_start = time.perf_counter()
            child = problem.for_count(count)
            construction_time = time.perf_counter()-construction_start
            row['construction_time'] += construction_time
            share = (weights[index]/sum(weights[index:]) if weights else
                     1./(len(active)-index))
            allocation = max(0., (phase_deadline-time.perf_counter())*share)
            if allocation <= 0:
                break
            visit_number = len(row['visits']) + 1
            child_seed = (int(seed) if visit_number == 1 else
                          (int(seed)+104729*(visit_number-1)+1009*count) % 2147483647)
            iterations = (max(1, remaining_iterations//(len(active)-index))
                          if method == 'alns' and remaining_iterations > 0 else 0)
            seed_result = deepcopy(incumbents.get(count))
            if seed_result is not None:
                for key in ('count_search', 'count_search_visits', 'anytime'):
                    seed_result.pop(key, None)
                seed_result['info'] = dict(objective=seed_result['info']['objective'],
                                           status='FEASIBLE', proven_optimal=False,
                                           proven_infeasible=False, best_bound=None)
            # Copying a large incumbent is part of the outer budget as well.
            allocation = max(0., (phase_deadline-time.perf_counter())*share)
            if allocation <= 0:
                break
            started = time.perf_counter()
            if method == 'exact':
                candidate = solve_candidate(child, time_limit=allocation, seed=child_seed, verbose=verbose)
            else:
                candidate = solve_candidate(child, max_time=allocation, max_iterations=iterations,
                                            seed=child_seed, initial_result=seed_result)
            returned = time.perf_counter()
            info = candidate['info']
            used = info.get('iterations', 0)
            if not isinstance(used, int) or used < 0 or (method == 'alns' and used > iterations):
                raise ValueError('Candidate exceeded its allocated iteration budget')
            remaining_iterations -= used
            valid = bool(candidate.get('audit', {}).get('valid'))
            audit_start = time.perf_counter()
            if valid:
                _attach(problem, child, candidate, count)
                candidate['audit'] = _audit(problem, candidate)
                valid = bool(candidate['audit']['valid']) and _finite(info.get('objective'))
                if not valid:
                    info.update(status='AUDIT_FAILED', proven_optimal=False)
            failed = info.get('status') == 'AUDIT_FAILED' or (info.get('objective') is not None and not valid)
            row['audit_failed'] = row['audit_failed'] or failed
            cost = info.get('objective') if valid else None
            visit = dict(batch_count=count, visit=visit_number, round=round_number, phase=phase, seed=child_seed,
                         budget=allocation, iteration_budget=iterations, iterations=used,
                         started=started-begun, returned=returned-begun,
                         finished=time.perf_counter()-begun, construction_time=construction_time,
                         solve_wall_time=returned-started, audit_time=time.perf_counter()-audit_start,
                         runtime=info.get('runtime', returned-started), status=info['status'], valid=valid,
                         objective=cost, best_bound=info.get('best_bound'),
                         proven_optimal=bool(method == 'exact' and valid and info.get('proven_optimal')),
                         proven_infeasible=bool(info.get('proven_infeasible')),
                         restart='audited_incumbent' if method == 'alns' and seed_result is not None else 'cold',
                         local_history=deepcopy(info.get('history', [])))
            for key in ('proof_scope', 'build_time', 'variables', 'constraints', 'node_count',
                        'sol_count', 'evaluations', 'cache_hits', 'calendar_evaluations',
                        'operator_attempts', 'operator_improvements', 'cutoff_polish_calls',
                        'cutoff_polish_improvements', 'cutoff_polish_evaluations',
                        'cutoff_polish_runtime', 'cutoff_polish_budget'):
                if key in info:
                    visit[key] = deepcopy(info[key])
            visits.append(visit)
            row['visits'].append(visit)
            row['last_status'] = info['status']
            row['budget'] += allocation
            row['iteration_budget'] += iterations
            row['iterations'] += used
            row['runtime'] += visit['solve_wall_time'] + construction_time + visit['audit_time']
            if valid and (count not in incumbents or cost < incumbents[count]['info']['objective']):
                incumbents[count] = deepcopy(candidate)
                row.update(valid=True, objective=cost, container_pool_size=candidate.get('container_pool_size'))
            if valid and (best is None or cost < best['info']['objective']):
                best = deepcopy(candidate)
            lower = info.get('best_bound') if method == 'exact' and not failed else None
            if visit['proven_optimal']:
                lower = cost
            if _finite(lower):
                row['best_bound'] = max(lower, row['best_bound']) if _finite(row['best_bound']) else lower
            if visit['proven_infeasible'] and row['valid']:
                row['certificate_errors'].append('INFEASIBLE_WITH_AUDITED_INCUMBENT')
            if row['valid'] and _finite(row['best_bound']):
                tolerance = 1e-6 + 1e-7*abs(row['objective'])
                if row['best_bound'] > row['objective'] + tolerance:
                    row['certificate_errors'].append('LOWER_BOUND_EXCEEDS_AUDITED_UPPER_BOUND')
                elif row['best_bound'] > row['objective']:
                    row['best_bound'] = row['objective']
            row['proven_optimal'] = row['proven_optimal'] or visit['proven_optimal']
            row['proven_infeasible'] = row['proven_infeasible'] or visit['proven_infeasible']
            if row['audit_failed'] or row['certificate_errors']:
                row.update(proven_optimal=False, proven_infeasible=False, best_bound=None)
            row['status'] = ('CERTIFICATE_CONFLICT' if row['certificate_errors'] else
                             'AUDIT_FAILED' if row['audit_failed'] else 'OPTIMAL' if row['proven_optimal'] else
                             'INFEASIBLE' if row['proven_infeasible'] else 'FEASIBLE' if row['valid'] else info['status'])
            anytime.append(dict(elapsed=time.perf_counter()-begun,
                                objective=best['info']['objective'] if best else None,
                                batch_count=best.get('cycle_batch_count') if best else None,
                                visited_count=count, visit=visit_number, source='audited_candidate_return'))
            optimal, infeasible, _, _ = _certificate(rows, best, method)
            if optimal or infeasible:
                break
        # Real solvers consume time; a nonadvancing injected clock must not spin.
        if time.perf_counter() <= round_start:
            stop_reason = 'NO_CLOCK_PROGRESS'
            break
    optimal, infeasible, bound, proof = _certificate(rows, best, method)
    if optimal or infeasible:
        stop_reason = 'PROOF_COMPLETE'
    result = deepcopy(best) if best is not None else joint_empty_result(problem, method)
    _metadata(problem, result, method, best is not None)
    result.update(count_search=rows, count_search_visits=visits, anytime=anytime)

    def operators(key, selected):
        totals = {}
        for visit in selected:
            for name, value in visit.get(key, {}).items():
                totals[name] = totals.get(name, 0) + value
        return totals

    sum_keys = ('evaluations', 'cache_hits', 'calendar_evaluations', 'cutoff_polish_calls',
                'cutoff_polish_improvements', 'cutoff_polish_evaluations',
                'cutoff_polish_runtime', 'cutoff_polish_budget')
    for row in rows:
        row.update({key: sum(v.get(key, 0) for v in row['visits']) for key in sum_keys})
        for key in ('operator_attempts', 'operator_improvements'):
            row[key] = operators(key, row['visits'])
    result['info'].update(
        status='OPTIMAL' if optimal else 'INFEASIBLE' if infeasible else 'FEASIBLE' if best is not None
               else 'TIME_LIMIT' if method == 'exact' else 'HEURISTIC_NO_SOLUTION',
        proven_optimal=optimal, proven_infeasible=infeasible, best_bound=bound,
        mip_gap=(max(0., best['info']['objective']-bound)/max(1e-9, abs(best['info']['objective']))
                 if best is not None and bound is not None else None),
        runtime=time.perf_counter()-begun, time_budget=budget, seed=seed,
        iterations=sum(r['iterations'] for r in rows), iteration_budget=max_iterations if method == 'alns' else None,
        budget_strategy=budget_strategy, stop_reason=stop_reason, rounds=round_number,
        restart_semantics='incumbent_restart_not_state_continuation',
        seed_derivation='first=seed; revisit=(seed+104729*(visit-1)+1009*K)%2147483647',
        history_scope='selected_batch_count_only', anytime_scope='global_all_counts',
        anytime_granularity='audited_candidate_return', batch_count_candidates=list(counts),
        selected_count_proven_optimal=next((r['proven_optimal'] for r in rows
                                          if r['batch_count'] == result.get('cycle_batch_count')), False),
        proof_aggregation=proof, proof_scope=(f'model_{problem.model_number}_all_configured_counts_integer_time_cost_MILP'
                                             if method == 'exact' else None),
        audit_failure_count=sum(r['audit_failed'] for r in rows),
        certificate_error_count=sum(len(r['certificate_errors']) for r in rows),
        initial_incumbent_cost=initial_result['info']['objective'] if initial_result else None,
        operator_attempts=operators('operator_attempts', visits),
        operator_improvements=operators('operator_improvements', visits),
        **{key: sum(v.get(key, 0) for v in visits) for key in sum_keys})
    if best is not None:
        result['audit'] = _audit(problem, result)
        if not result['audit']['valid']:
            result['info'].update(status='AUDIT_FAILED', proven_optimal=False, objective=None,
                                  best_bound=None, mip_gap=None)
    result['info']['runtime'] = time.perf_counter()-begun
    return result
