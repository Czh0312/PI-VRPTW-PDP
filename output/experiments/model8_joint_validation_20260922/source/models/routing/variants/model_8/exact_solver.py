"""One joint MILP solve: no M3 stage, fixed-route subproblem or conditional proof."""
import math
import time

from gurobipy import GRB

from models.routing.variants.model_6.model import apply_no_transfer_warm_start, extract_compact_solution, model_statistics
from .model import build_model
from .state import Evaluator, state_from_routes, initial_info, record_solution, export_routes


def solve_exact(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                TFp=9999.0, verbose=True, time_limit=300, mip_gap=0.0,
                return_incumbent_on_time_limit=False, return_info=False,
                warm_start_routes=None, gurobi_params=None, allow_transfers=True,
                strengthen=True):
    if mip_gap != 0:
        raise ValueError('精确入口要求 mip_gap=0')
    if time_limit is not None and time_limit <= 0:
        raise ValueError('time_limit 必须为正')
    started = time.perf_counter()
    e = Evaluator(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite, TFp)
    info = initial_info(allow_transfers, 'single_joint_compact_milp')
    best = None
    if warm_start_routes is not None:
        best = state_from_routes(problem, vehicles_per_satellite, warm_start_routes)
        record_solution(info, best, e.audit(best))
        info.update(status='FEASIBLE', solution_type='feasible_not_proven',
                    initial_solution_source='optional_warm_start_not_fixed')
    model, variables, ctx = build_model(problem, e1_tasks, e2_tasks_by_sat,
                                       vehicles_per_satellite, TFp, allow_transfers, strengthen)
    try:
        for name, value in (gurobi_params or {}).items():
            model.setParam(name, value)
        model.Params.OutputFlag = int(verbose)
        model.Params.MIPGap = model.Params.MIPGapAbs = 0
        model.Params.IntFeasTol = 1e-9
        if warm_start_routes is not None:
            apply_no_transfer_warm_start(variables, ctx, warm_start_routes)
        remaining = math.inf if time_limit is None else time_limit - (time.perf_counter() - started)
        info['model_statistics'] = model_statistics(model, variables, ctx)
        info['mathematical_equivalent'] = 'model6_under_current_handoff_rules'
        if remaining <= 0:
            info['termination_reason'] = 'build_time_limit'
        else:
            if math.isfinite(remaining):
                model.Params.TimeLimit = remaining
            model.optimize()
            info['status_code'] = int(model.Status)
            info['sol_count'] = int(model.SolCount)
            info['status'] = {GRB.OPTIMAL: 'OPTIMAL', GRB.INFEASIBLE: 'INFEASIBLE',
                              GRB.TIME_LIMIT: 'TIME_LIMIT'}.get(model.Status, str(model.Status))
            info['termination_reason'] = 'optimal' if model.Status == GRB.OPTIMAL else str(model.Status)
            if model.SolCount:
                e1, compact_e2, vehicles, transfers, _ = extract_compact_solution(model, variables, ctx)
                paths = {s: [[s, s] for _ in range(vehicles_per_satellite)] for s in compact_e2}
                for s, routes in compact_e2.items():
                    for k, route in zip(vehicles[s], routes):
                        paths[s][k] = route
                flows = [{k: t[k] for k in ('satellite', 'event', 'sender_vehicle', 'receiver_vehicle', 'quantity')}
                         for t in transfers]
                state = state_from_routes(problem, vehicles_per_satellite, (e1, paths), flows)
                audit = e.audit(state)
                if not math.isclose(audit['objective'], model.ObjVal, abs_tol=1e-5, rel_tol=1e-8):
                    raise RuntimeError('联合 MILP 距离与独立审计不一致')
                if best is None or audit['objective'] <= info['objective'] + 1e-6:
                    best = state
                    record_solution(info, best, audit)
                if model.Status == GRB.OPTIMAL:
                    if info['objective'] < model.ObjVal - 1e-5:
                        raise RuntimeError('可行热启动优于求解器最优值，拒绝签发最优证书')
                    info.update(proven_optimal=True, status='OPTIMAL', solution_type='proven_optimal')
                else:
                    info.update(status='FEASIBLE', solution_type='feasible_not_proven')
            bound = float(model.ObjBound)
            if math.isfinite(bound) and abs(bound) < 1e90:
                info['best_bound'] = bound
                if info['objective'] is not None:
                    info['mip_gap'] = max(0.0, (info['objective'] - bound) / max(abs(info['objective']), 1e-12))
        info['runtime'] = time.perf_counter() - started
        routes = export_routes(best) if best is not None else (None, None)
        if not info['proven_optimal'] and not return_incumbent_on_time_limit:
            routes = (None, None)
        return (*routes, info) if return_info else routes
    finally:
        model.dispose()
