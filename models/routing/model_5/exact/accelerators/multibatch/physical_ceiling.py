"""Conditional lower bounds from objective-ceiling feasibility tests.

Infeasibility after adding cost <= C proves conditional cost >= C. It
does not prove that the original conditional physical model is infeasible.
For an unfinished restricted solve, min(C, its lower bound) remains valid:
removed schedules all cost more than C. Full M5 proof flags stay false.
"""
import math
import time
from pathlib import Path

from models.routing.model_5.exact.accelerators.multibatch.physical_site import build_physical_site, project_physical_site


def physical_ceiling_certificate(problem, site, ceiling, *, time_limit=60, seed=42,
        fallback_bound=0, supply_thresholds=None, minimum_returns=None, maximum_returns=None,
        trip_groups=None, partial_groups=False, initial_result=None, compress_routes=True,
        diagnostics_path=None, verbose=False):
    import gurobipy as gp
    if not math.isfinite(ceiling) or ceiling < 0:
        raise ValueError('Objective ceiling must be finite and nonnegative')
    if not math.isfinite(fallback_bound) or not 0 <= fallback_bound <= ceiling:
        raise ValueError('Certified fallback must be between zero and the ceiling')
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('Positive finite time limit required')
    started = time.perf_counter()
    model, context = build_physical_site(problem, site, cost_floor=fallback_bound,
        supply_thresholds=supply_thresholds, minimum_returns=minimum_returns,
        maximum_returns=maximum_returns, trip_groups=trip_groups, partial_groups=partial_groups,
        compress_routes=compress_routes, deadline=started+time_limit, verbose=verbose)
    try:
        model.addConstr(model.getObjective() <= ceiling, name='physical_objective_ceiling')
        if initial_result is not None:
            project_physical_site(problem, context, initial_result)
        model.update()
        if diagnostics_path is not None:
            path = Path(diagnostics_path); path.parent.mkdir(parents=True, exist_ok=True)
            model.write(str(path))
        model.Params.Threads = 1; model.Params.Seed = seed; model.Params.DualReductions = 0
        model.Params.IntFeasTol = model.Params.FeasibilityTol = 1e-9
        model.Params.MIPGap = model.Params.MIPGapAbs = 0
        model.Params.TimeLimit = max(0, time_limit-(time.perf_counter()-started))
        model.optimize()
        infeasible = model.Status == gp.GRB.INFEASIBLE
        try:
            solver_bound = float(model.ObjBound)
        except gp.GurobiError:
            solver_bound = None
        raw = ceiling if infeasible else (
            min(ceiling, solver_bound) if solver_bound is not None and math.isfinite(solver_bound) else None)
        margin = 0 if raw is None else 1e-7*max(1, abs(raw))
        return dict(satellite=site, bound=fallback_bound if raw is None else max(fallback_bound, raw-margin), raw_bound=raw,
            margin=margin, status=int(model.Status), objective_ceiling=ceiling,
            ceiling_model_infeasible=infeasible, ceiling_model_optimal=model.Status==gp.GRB.OPTIMAL,
            subproblem_infeasible=False, subproblem_optimal=False,
            scope='M5_conditional_physical_E2_site_cost_relaxation',
            inference='min(ceiling,restricted_bound); ceiling if restricted model infeasible',
            supply_thresholds=context['supply_thresholds'], minimum_returns=context['minimum_returns'],
            maximum_returns=context['maximum_returns'], trip_groups=context['trip_groups'],
            partial_groups=context['partial_groups'], projection_proof=context['projection_proof'],
            proven_optimal=False, proven_infeasible=False, runtime=time.perf_counter()-started,
            solver_runtime=model.Runtime, original_business_input_unchanged=True)
    finally:
        model.dispose()
