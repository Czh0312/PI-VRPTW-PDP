"""Enumerate K with one shared-cutoff MILP or an independent ALNS search per K.

The whole call shares one wall-time and iteration budget. A global cost proof
requires every configured count to be resolved, including infeasible counts.
"""
from __future__ import annotations

from copy import deepcopy
import math
import time

from models.routing.common.cycle.joint import joint_empty_result
from models.routing.common.cycle.uniform import attach_uniform_decisions, audit_uniform_solution


def solve_uniform(problem, *, method, budget, seed, max_iterations=0, initial_result=None, verbose=False):
    begun = time.perf_counter()
    deadline = begun+budget
    best = None
    initial_count = None
    if initial_result is not None:
        checked = audit_uniform_solution(problem,initial_result)
        if not checked['valid']:
            raise ValueError(f'Initial uniform solution fails independent replay: {checked["errors"]}')
        best = deepcopy(initial_result)
        best['info'] = dict(objective=checked['objective'],status='FEASIBLE',proven_optimal=False,
                            proven_infeasible=False,initial_source_solver=initial_result.get('solver'))
        initial_count = best['cycle_batch_count']
    rows = []
    remaining_iterations = max_iterations
    counts = problem.batch_count_candidates
    for index,count in enumerate(counts):
        remaining = deadline-time.perf_counter()
        unresolved = len(counts)-index
        allocation = max(0.,remaining/unresolved)
        if allocation <= 0:
            rows.append(dict(batch_count=count,status='NOT_RUN_TIME_LIMIT',objective=None,
                             proven_optimal=False,proven_infeasible=False,best_bound=None,budget=0.,iterations=0))
            continue
        child = problem.for_count(count)
        # Calendar construction is part of the shared wall budget.
        allocation = max(0.,(deadline-time.perf_counter())/unresolved)
        if allocation <= 0:
            rows.append(dict(batch_count=count,status='NOT_RUN_TIME_LIMIT',objective=None,
                             proven_optimal=False,proven_infeasible=False,best_bound=None,budget=0.,iterations=0))
            continue
        if method == 'exact':
            from .exact.solver import solve_exact
            candidate = solve_exact(child,time_limit=allocation,seed=seed,verbose=verbose)
            iteration_allocation = 0
        else:
            from .alns.solver import solve_alns
            iteration_allocation = remaining_iterations//unresolved
            seed_result = deepcopy(initial_result) if count == initial_count else None
            if seed_result is not None:
                # Retain only the physical incumbent, not outer proof/search history.
                seed_result.pop('count_search',None)
                seed_result['info'] = dict(objective=initial_result['info']['objective'])
            candidate = solve_alns(child,max_time=allocation,max_iterations=iteration_allocation,
                                   seed=seed,initial_result=seed_result)
            remaining_iterations -= candidate['info'].get('iterations',0)
        info = candidate['info']
        valid = candidate.get('audit',{}).get('valid',False)
        row = dict(batch_count=count,status=info['status'],objective=info.get('objective') if valid else None,
                   valid=valid,proven_optimal=bool(valid and info.get('proven_optimal')),
                   proven_infeasible=info.get('proven_infeasible',False),best_bound=info.get('best_bound'),
                   proof_scope=info.get('proof_scope'),budget=allocation,runtime=info.get('runtime'),
                   iteration_budget=iteration_allocation,iterations=info.get('iterations',0),
                   evaluations=info.get('evaluations',0),cache_hits=info.get('cache_hits',0),
                   operator_attempts=info.get('operator_attempts',{}),
                   operator_improvements=info.get('operator_improvements',{}),
                   container_pool_size=candidate.get('container_pool_size') if valid else None)
        rows.append(row)
        if valid:
            attach_uniform_decisions(problem,candidate,count)
            if best is None or candidate['info']['objective'] < best['info']['objective']-1e-8:
                best = candidate
    resolved = all(row['proven_optimal'] or row['proven_infeasible'] for row in rows)
    globally_optimal = method == 'exact' and best is not None and resolved
    infeasible = best is None and all(row['proven_infeasible'] for row in rows)
    feasible_bounds = [row['best_bound'] for row in rows if not row['proven_infeasible']]
    bound = min(feasible_bounds) if feasible_bounds and all(v is not None and math.isfinite(v) for v in feasible_bounds) else None
    result = best if best is not None else joint_empty_result(problem,method)
    result.update(schedule_mode='uniform_cycle',batch_mode='uniform_cycle',uniform_cycle_config=problem.uniform_contract,
                  count_search=rows,solver=method,solver_module=__name__)
    if best is None:
        result.update(cycle_batch_count=None,batch_duration_ticks=None,shared_cutoff_offset_ticks=None,
                      shared_cutoff_ratio=None,actual_recovery_round_count=None)
    initial_cost = initial_result['info']['objective'] if initial_result is not None else None
    def sum_operators(key):
        totals={}
        for row in rows:
            for name,value in row.get(key,{}).items():
                totals[name]=totals.get(name,0)+value
        return totals
    result['info'].update(status='OPTIMAL' if globally_optimal else 'INFEASIBLE' if infeasible else
                          'FEASIBLE' if best is not None else 'TIME_LIMIT' if method=='exact' else 'HEURISTIC_NO_SOLUTION',
                          proven_optimal=globally_optimal,proven_infeasible=infeasible,
                          best_bound=bound if method=='exact' else None,
                          mip_gap=(max(0.,result['info']['objective']-bound)/max(1e-9,abs(result['info']['objective']))
                                   if best is not None and method=='exact' and bound is not None else None),
                          runtime=time.perf_counter()-begun,iterations=sum(row['iterations'] for row in rows),
                          evaluations=sum(row.get('evaluations',0) for row in rows),
                          cache_hits=sum(row.get('cache_hits',0) for row in rows),
                          operator_attempts=sum_operators('operator_attempts'),
                          operator_improvements=sum_operators('operator_improvements'),
                          history_scope='selected_batch_count_only',
                          initial_incumbent_cost=initial_cost,selected_count_proven_optimal=(
                              next((row['proven_optimal'] for row in rows if row['batch_count']==result.get('cycle_batch_count')),False)),
                          batch_count_candidates=list(counts),seed=seed,
                          proof_scope='uniform_cycle_all_configured_counts_shared_cutoff_integer_time_cost_MILP'
                                      if method=='exact' else None,
                          time_budget=budget,iteration_budget=max_iterations if method=='alns' else None)
    if best is not None:
        result['audit'] = audit_uniform_solution(problem,result)
        if not result['audit']['valid']:
            result['info'].update(status='AUDIT_FAILED',proven_optimal=False,objective=None)
    return result
