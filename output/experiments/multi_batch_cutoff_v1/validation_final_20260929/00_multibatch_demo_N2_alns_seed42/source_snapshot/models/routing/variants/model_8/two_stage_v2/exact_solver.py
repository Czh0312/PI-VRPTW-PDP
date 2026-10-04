"""Conditional exact solution of M8 v2, including a matched no-handoff control."""
import math
import time

from gurobipy import GRB

from .baseline import prepare_baseline
from .model import build_model
from .state import Evaluator, baseline_state, initial_info, record_solution


def optimize_stage2(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                    baseline, deadline, TFp=9999.0, verbose=False,
                    gurobi_params=None, allow_transfers=True):
    if time.perf_counter() >= deadline:
        return None
    built = time.perf_counter()
    model, variables, ctx = build_model(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
        baseline, TFp, allow_transfers=allow_transfers)
    try:
        build_time = time.perf_counter() - built
        model.Params.OutputFlag = int(verbose)
        for name, value in (gurobi_params or {}).items():
            model.setParam(name, value)
        model.Params.OutputFlag = int(verbose)
        model.Params.MIPGap = model.Params.MIPGapAbs = 0
        model.Params.IntFeasTol = 1e-9
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return {'feasible': False, 'build_seconds': build_time, 'status': 'BUILD_TIME_LIMIT'}
        if math.isfinite(remaining):
            model.Params.TimeLimit = remaining
        model.optimize()
        bound = float(model.ObjBound)
        result = {'status_code': int(model.Status), 'feasible': model.SolCount > 0,
                  'conditional_proven_optimal': model.Status == GRB.OPTIMAL,
                  'conditional_best_bound': bound if math.isfinite(bound) and abs(bound) < 1e90 else None,
                  'build_seconds': build_time, 'variables': model.NumVars,
                  'constraints': model.NumConstrs, 'allow_transfers': allow_transfers}
        if not model.SolCount:
            return result
        state = baseline_state(baseline)
        for s, paths in state['e2_routes'].items():
            for k in range(len(paths)):
                v = (s, k)
                if variables['used'][v].X < .5:
                    paths[k] = [s, s]
                    continue
                successor = {i: j for (i, j, vehicle), x in variables['z'].items()
                             if vehicle == v and x.X > .5}
                raw, display = [s], [s]
                while True:
                    n = successor.get(raw[-1])
                    if n is None:
                        raise RuntimeError('M8 route extraction found an open route')
                    if n == s:
                        display.append(s)
                        break
                    if n in raw:
                        raise RuntimeError('M8 route extraction found a subtour')
                    raw.append(n)
                    display.append(n if variables['serve'][n, v].X > .5 else 'MEET@' + n)
                if len(successor) != len(raw):
                    raise RuntimeError('M8 disconnected arcs')
                paths[k] = display
        for (n, a, b), var in variables['transfer'].items():
            q = int(round(var.X))
            if q:
                state['transfers'].append({'satellite': a[0], 'event': n,
                                          'sender_vehicle': a[1], 'receiver_vehicle': b[1],
                                          'quantity': q})
        audit = Evaluator(problem, e1_tasks, e2_tasks_by_sat, baseline, TFp).audit(state)
        if not math.isclose(audit['objective'], model.ObjVal, abs_tol=1e-5, rel_tol=1e-8):
            raise RuntimeError('M8 extracted distance does not match MILP objective')
        result.update(state=state, audit=audit, objective=audit['objective'],
                      conditional_mip_gap=float(model.MIPGap))
        return result
    finally:
        model.dispose()


def solve_exact(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                TFp=9999.0, verbose=True, time_limit=300, mip_gap=0.0,
                return_incumbent_on_time_limit=False, return_info=False,
                stage1_time_ratio=0.6, base_routes=None, base_info=None,
                gurobi_params=None, allow_transfers=True):
    if mip_gap != 0 or not 0 < stage1_time_ratio < 1:
        raise ValueError('M8 要求 mip_gap=0，stage1_time_ratio 在 (0,1) 内')
    if time_limit is not None and time_limit <= 0:
        raise ValueError('time_limit 必须为正')
    started = time.perf_counter()
    deadline = started + time_limit if time_limit is not None else math.inf
    if base_routes is None:
        from models.routing.variants.model_3.exact_solver import solve_exact as solve_m3
        a, b, base_info = solve_m3(
            problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite, TFp=TFp,
            time_limit=None if time_limit is None else time_limit * stage1_time_ratio,
            verbose=verbose, return_info=True, return_incumbent_on_time_limit=True,
            gurobi_params=gurobi_params)
        if a is None:
            info = {'model': 8, 'status': 'NO_BASE_SOLUTION', 'objective': None,
                    'proven_optimal': False, 'stage1': base_info}
            return (None, None, info) if return_info else (None, None)
        base_routes = (a, b)
    base = prepare_baseline(problem, e1_tasks, e2_tasks_by_sat,
                            vehicles_per_satellite, base_routes, TFp)
    stage1 = dict(base_info or {})
    if stage1.get('objective') is not None and not math.isclose(
            stage1['objective'], base['objective'], abs_tol=1e-6, rel_tol=1e-8):
        raise ValueError('M3 证书目标值与基础路线不一致')
    info = initial_info(base, stage1)
    info.update(algorithm='compact_conditional_milp', allow_transfers=allow_transfers)
    state = baseline_state(base)
    record_solution(info, state, Evaluator(problem, e1_tasks, e2_tasks_by_sat, base, TFp).audit(state))
    result = optimize_stage2(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                             base, deadline, TFp, verbose, gurobi_params, allow_transfers)
    if result:
        info['stage2'] = {k: v for k, v in result.items() if k not in ('state', 'audit')}
        info['conditional_best_bound'] = result.get('conditional_best_bound')
        if result.get('feasible') and result['objective'] <= info['objective'] + 1e-6:
            state = result['state']
            record_solution(info, state, result['audit'])
            proven = result['conditional_proven_optimal']
            info.update(conditional_proven_optimal=proven,
                        conditional_mip_gap=result['conditional_mip_gap'],
                        two_stage_proven_optimal=proven and info['stage1_proven_optimal'],
                        status='CONDITIONAL_OPTIMAL' if proven else 'FEASIBLE',
                        solution_type='conditional_optimal' if proven else 'feasible_not_proven')
    info['runtime'] = time.perf_counter() - started
    routes = (base['e1_routes'], state['e2_routes'])
    if not info['conditional_proven_optimal'] and not return_incumbent_on_time_limit:
        routes = (None, None)
    if verbose:
        print(f"M8 v2: base={base['objective']:.6f}, best={info['objective']:.6f}, "
              f"handoffs={info['transshipment_count']}, conditional_optimal={info['conditional_proven_optimal']}")
    return (*routes, info) if return_info else routes
