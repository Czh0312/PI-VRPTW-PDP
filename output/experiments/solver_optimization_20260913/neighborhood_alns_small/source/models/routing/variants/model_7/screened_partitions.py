"""Complete assignment enumeration, pruned jointly by a global relaxation.

The master excludes only assignments already sent to the complete M6 solver.
Unresolved subproblems keep their own lower bounds and prevent certification.
No heuristic route-pool bound or subproblem cost cut is used.
"""
import math
import time

from models.routing.common.commodity_flow import solve_commodity_flow
from models.routing.common.task_builder import build_tasks_from_assignment
from models.routing.variants.model_6.exact_solver import solve_exact as solve_m6


def solve_screened_partitions(problem, vehicles, *, TFp, remaining_time,
                              incumbent_routes, incumbent_assignment, incumbent_objective,
                              global_bound, gurobi_params, partition_time_limit=5.0):
    started = time.perf_counter()
    best = float(incumbent_objective) if incumbent_objective is not None else math.inf
    routes, assignment = incumbent_routes, incumbent_assignment
    best_info = {'transshipments':[], 'transshipment_count':0, 'transferred_container_count':0}
    excluded, summaries = [], []
    remaining_bound = max(0.0, global_bound or 0.0)
    screen_closed = False
    master_calls = 0
    while remaining_time() is None or remaining_time() > 0.03:
        available = remaining_time()
        screen_budget = None if available is None else min(5.0, max(0.15, available*0.25))
        params = dict(gurobi_params or {})
        params.pop('Cutoff', None)
        if math.isfinite(best):
            params['Cutoff'] = best - 1e-7
        _, _, candidate, master = solve_commodity_flow(problem,4,vehicles,
            TFp=TFp,time_limit=screen_budget,gurobi_params=params,return_info=True,
            return_incumbent_on_time_limit=True,auto_warm_start=False,
            zero_pickup_load=True,excluded_assignments=excluded)
        master_calls += 1
        bound = master.get('best_bound')
        if bound is not None and math.isfinite(bound):
            remaining_bound = max(remaining_bound, bound)
        if master['status_code'] in (3,6):
            screen_closed = True
            remaining_bound = best
            break
        if candidate is None:
            break
        available = remaining_time()
        if available is not None and available <= 0.02:
            break
        e1,e2 = build_tasks_from_assignment(problem,candidate)
        limit = partition_time_limit
        if available is not None:
            limit = min(available, limit or available)
        sub_params = dict(gurobi_params or {})
        sub_params.pop('Cutoff', None)
        if math.isfinite(best):
            sub_params['Cutoff'] = best - 1e-7
        r1,r2,sub = solve_m6(problem,e1,e2,vehicles,TFp=TFp,time_limit=limit,
            verbose=False,return_info=True,return_incumbent_on_time_limit=True,
            gurobi_params=sub_params,use_relaxation_certificate=False,compute_iis_on_infeasible=False)
        valid_bounds = [remaining_bound]
        if sub.get('best_bound') is not None and math.isfinite(sub['best_bound']):
            valid_bounds.append(sub['best_bound'])
        if sub['status'] in ('INFEASIBLE','CUTOFF'):
            valid_bounds.append(best)
        partition_bound = max(valid_bounds)
        value = sub.get('objective')
        if r1 is not None and value is not None and value < best:
            best, routes, assignment, best_info = value, (r1,r2), dict(candidate), sub
        summaries.append(dict(assignment=dict(candidate), status=sub['status'],
            objective=value, best_bound=partition_bound, proven_optimal=sub.get('proven_optimal',False),
            runtime=sub.get('runtime'), transshipment_count=sub.get('transshipment_count',0),
            source='global_relaxation_screened_complete_m6'))
        excluded.append(dict(candidate))

    # Spend residual time on unresolved complete M6 partitions. Never delete
    # their bounds merely because the master excluded their assignments.
    for row in sorted(summaries,key=lambda r:r['best_bound']):
        available = remaining_time()
        if row['best_bound'] >= best-1e-6 or (available is not None and available <= .03):
            continue
        candidate = row['assignment']
        e1,e2 = build_tasks_from_assignment(problem,candidate)
        pending = sum(r['best_bound'] < best-1e-6 for r in summaries)
        limit = None if available is None else available/max(1,pending)
        params = dict(gurobi_params or {})
        if math.isfinite(best):
            params['Cutoff'] = best-1e-7
        r1,r2,sub = solve_m6(problem,e1,e2,vehicles,TFp=TFp,time_limit=limit,
            verbose=False,return_info=True,return_incumbent_on_time_limit=True,
            gurobi_params=params,use_relaxation_certificate=False,compute_iis_on_infeasible=False)
        if sub.get('best_bound') is not None:
            row['best_bound'] = max(row['best_bound'],sub['best_bound'])
        if sub['status'] in ('CUTOFF','INFEASIBLE'):
            row['best_bound'] = max(row['best_bound'],best)
        row.update(status=sub['status'],proven_optimal=sub.get('proven_optimal',False),objective=sub.get('objective'))
        value = sub.get('objective')
        if r1 is not None and value is not None and value < best:
            best,routes,assignment,best_info = value,(r1,r2),dict(candidate),sub
    bound = min([remaining_bound]+[r['best_bound'] for r in summaries])
    bound = min(best,bound)
    proven = math.isfinite(best) and bound >= best-1e-6
    total = len(problem.satellites)**len(problem.customers)
    unresolved = sum(r['best_bound'] < best-1e-6 for r in summaries)
    info = dict(status='OPTIMAL' if proven else 'TIME_LIMIT',status_code=2 if proven else 9,
        proven_optimal=proven, solution_type='proven_optimal' if proven else 'feasible_not_proven',
        objective=best if math.isfinite(best) else None, best_bound=bound,
        mip_gap=max(0,(best-bound)/max(abs(best),1e-12)) if math.isfinite(best) else None,
        runtime=time.perf_counter()-started,sol_count=int(routes is not None),assignment=assignment,
        proof_scope='complete_assignment_enumeration_with_global_relaxation_screen',
        formulation='complete_m6_partitions_with_commodity_flow_assignment_screen',
        proof_time_strategy='global_relaxation_screened_partition_enumeration',
        assignment_partitions_total=total,assignment_partitions_evaluated=len(summaries),
        assignment_partitions_resolved=len(summaries)-unresolved,
        assignment_partitions_unresolved=unresolved,
        assignment_partitions_pruned_by_global_screen=total-len(summaries) if screen_closed else 0,
        assignment_master_calls=master_calls,assignment_master_closed=screen_closed,
        partition_summaries=summaries,
        transshipments=best_info.get('transshipments',[]),transshipment_count=best_info.get('transshipment_count',0),
        transferred_container_count=best_info.get('transferred_container_count',0),
        route_vehicle_indices=best_info.get('route_vehicle_indices'))
    return (*(routes or (None,None)),assignment,info)
