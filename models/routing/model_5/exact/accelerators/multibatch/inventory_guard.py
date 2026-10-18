"""Bounded independent search checks for the complete inventory relaxation.

This is a numerical safeguard, not a repair of the original discrepancy or
an exact-arithmetic proof. An unchecked primary bound never improves fallback.
Only this separate replay disables cuts; the primary search is unchanged.
"""
import math
from pathlib import Path
import time


def rounded_inventory_witness(model):
    """Check original bounds, linear rows and active indicators independently."""
    import gurobipy as gp
    values = {v.VarName: float(v.X) if v.VType == gp.GRB.CONTINUOUS else round(v.X)
              for v in model.getVars()}
    if not all(math.isfinite(x) for x in values.values()):
        raise ValueError('Nonfinite inventory witness')

    def activity(expr):
        return math.fsum(expr.getCoeff(i)*values[expr.getVar(i).VarName]
                         for i in range(expr.size()))

    def violation(lhs, sense, rhs):
        return max(0.0, lhs-rhs) if sense == '<' else max(0.0, rhs-lhs) if sense == '>' else abs(lhs-rhs)

    bound = max((max(0.0, v.LB-values[v.VarName], values[v.VarName]-v.UB)
                 for v in model.getVars()), default=0.0)
    linear = max((violation(activity(model.getRow(c)), c.Sense, c.RHS)
                  for c in model.getConstrs()), default=0.0)
    indicator = 0.0
    for constraint in model.getGenConstrs():
        if constraint.GenConstrType != gp.GRB.GENCONSTR_INDICATOR:
            raise ValueError('Unsupported inventory general constraint')
        variable, active, expr, sense, rhs = model.getGenConstrIndicator(constraint)
        if values[variable.VarName] == active:
            indicator = max(indicator, violation(activity(expr), sense, rhs))
    objective = model.ObjCon+math.fsum(v.Obj*values[v.VarName] for v in model.getVars())
    return dict(objective=objective, bound_violation=bound, linear_violation=linear,
                active_indicator_violation=indicator,
                valid=max(bound, linear, indicator) <= 1e-7)


def check_inventory_bound(model, reported_raw, *, fallback_bound, deadline,
                          time_limit=10.0, diagnostics_path=None):
    """Return the smaller independent complete-model bound, or no new bound.

The live model is copied after update, without transferring search cuts,
solutions or starts. No variables/rows/domains/objective are changed. A replay
incumbent is only a relaxation witness; it is never an M5 feasible upper bound.
"""
    import gurobipy as gp
    begun, replay = time.perf_counter(), None
    answer = dict(status='NO_VERIFIED_IMPROVEMENT', accepted_raw_bound=None,
                  reported_raw_bound=reported_raw if math.isfinite(reported_raw) else None,
                  replay_raw_bound=None, reported_bound_consistent=None,
                  seed=43, cuts=0, runtime=0.0, witness=None)
    if not math.isfinite(reported_raw) or reported_raw <= fallback_bound:
        return answer
    if time.perf_counter() >= deadline or time_limit <= 0:
        answer['status'] = 'VERIFICATION_BUDGET_EXHAUSTED'
        return answer
    try:
        model.update()
        if model.ModelSense != gp.GRB.MINIMIZE or model.NumQConstrs or model.NumQNZs:
            raise ValueError('Inventory guard requires linear minimization')
        replay = model.copy()
        replay.Params.OutputFlag = 0
        replay.Params.Threads = 1
        replay.Params.Seed = 43
        replay.Params.Cuts = 0
        replay.Params.DualReductions = 0
        replay.Params.IntFeasTol = replay.Params.FeasibilityTol = 1e-9
        replay.Params.MIPGap = replay.Params.MIPGapAbs = 0
        answer['model_size'] = dict(variables=replay.NumVars, linear_rows=replay.NumConstrs,
                                    indicators=replay.NumGenConstrs)
        if diagnostics_path is not None:
            path = Path(diagnostics_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            replay.write(str(path))
        remaining = min(time_limit, deadline-time.perf_counter())
        if remaining <= 0:
            answer['status'] = 'VERIFICATION_BUDGET_EXHAUSTED'
            return answer
        replay.Params.TimeLimit = remaining
        replay.optimize()
        answer['replay_status'] = int(replay.Status)
        raw = float(replay.ObjBound)
        answer['replay_raw_bound'] = raw if math.isfinite(raw) else None
        if replay.Status not in (gp.GRB.OPTIMAL, gp.GRB.TIME_LIMIT) or not math.isfinite(raw):
            answer['status'] = 'REPLAY_NO_VALID_BOUND'
            return answer
        witness = rounded_inventory_witness(replay) if replay.SolCount else None
        answer['witness'] = witness
        if witness is not None and (not witness['valid'] or raw > witness['objective']+1e-5):
            answer['status'] = 'REPLAY_WITNESS_REJECTED'
            return answer
        contradicted = witness is not None and reported_raw > witness['objective']+1e-5
        answer.update(accepted_raw_bound=min(reported_raw, raw),
                      reported_bound_consistent=False if contradicted else (
                          abs(reported_raw-raw) <= 1e-5 if replay.Status == gp.GRB.OPTIMAL else None),
                      status='PRIMARY_BOUND_REJECTED' if contradicted else 'REPLAY_BOUND_CHECKED')
    except (gp.GurobiError, ValueError, AttributeError) as error:
        answer.update(status='VERIFICATION_ERROR', error=str(error), accepted_raw_bound=None)
    finally:
        if replay is not None:
            replay.dispose()
        answer['runtime'] = time.perf_counter()-begun
    return answer
