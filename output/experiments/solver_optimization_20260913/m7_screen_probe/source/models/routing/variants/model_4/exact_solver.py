"""Strict exact-solver execution and result extraction for Model 4."""

from gurobipy import GRB

from models.routing.common.exact_utils import extract_routes_e1, extract_routes_e2

from .model import _apply_m4_warm_start, build_model


def _status_name(status_code):
    """Human-readable Gurobi status without assuming every code is present."""
    names = {
        GRB.OPTIMAL: "OPTIMAL",
        GRB.TIME_LIMIT: "TIME_LIMIT",
        GRB.INFEASIBLE: "INFEASIBLE",
        GRB.INF_OR_UNBD: "INF_OR_UNBD",
        GRB.INTERRUPTED: "INTERRUPTED",
        GRB.SUBOPTIMAL: "SUBOPTIMAL",
        GRB.NUMERIC: "NUMERIC",
        GRB.WORK_LIMIT: "WORK_LIMIT",
    }
    return names.get(status_code, str(status_code))


def solve_exact(problem, vehicles_per_satellite, TFp=9999.0,
                time_limit=None, gurobi_params=None, verbose=True,
                return_info=False, warm_start_routes=None,
                return_incumbent_on_time_limit=False,
                formulation_options=None, backend="commodity_flow"):
    """求解 M4 精确模型（仅适合小规模）。

    返回 (e1_routes, e2_routes_by_sat[, assignment][, info])。

    This is the strict certification entry point.  If a time limit leaves a
    feasible incumbent but not a Gurobi ``OPTIMAL`` certificate, routes are
    withheld by default.  Set ``return_incumbent_on_time_limit=True`` only for
    diagnostics or warm-start generation and always inspect
    ``info['proven_optimal']`` before reporting an exact result.
    """
    if backend == "commodity_flow":
        from models.routing.common.commodity_flow import solve_commodity_flow
        return solve_commodity_flow(problem, 4, vehicles_per_satellite, TFp=TFp,
            verbose=verbose, time_limit=time_limit,
            return_incumbent_on_time_limit=return_incumbent_on_time_limit,
            return_info=return_info, warm_start_routes=warm_start_routes,
            gurobi_params=gurobi_params)
    if backend != "indexed":
        raise ValueError(f"Unknown exact backend: {backend}")
    m, vars_dict, ctx = build_model(
        problem, TFp=TFp, vehicles_per_satellite=vehicles_per_satellite,
        formulation_options=formulation_options)
    m.update()
    if ctx["formulation_options"]["branch_assignment_first"]:
        for var in vars_dict["w"].values():
            var.BranchPriority = 100
        for var in vars_dict["served_s"].values():
            var.BranchPriority = 80
        for (i, _j, _v), var in vars_dict["z"].items():
            if i in ctx["F"]:
                var.BranchPriority = 40
    if not verbose:
        m.setParam("OutputFlag", 0)
    if time_limit is not None:
        m.setParam("TimeLimit", time_limit)
        # Gurobi's wall-clock TimeLimit may substantially overrun while a
        # long node operation is being finalized.  WorkLimit is deterministic
        # and provides an independent secondary stop; explicit caller values
        # below still take precedence.
        if not gurobi_params or "WorkLimit" not in gurobi_params:
            m.setParam("WorkLimit", float(time_limit))
    m.setParam("MIPGap", 0.0)
    m.setParam("IntFeasTol", 1e-9)
    m.setParam("FeasibilityTol", 1e-9)
    if gurobi_params:
        for name, value in gurobi_params.items():
            m.setParam(name, value)
    if warm_start_routes:
        try:
            ws_e1, ws_e2 = warm_start_routes
            _apply_m4_warm_start(m, vars_dict, ctx, problem,
                                  ws_e1, ws_e2, verbose=verbose)
        except Exception as exc:
            if verbose:
                print(f"[M4 MIPStart] warm-start failed, skipping: {exc}")
    m.optimize()

    has_incumbent = getattr(m, "SolCount", 0) > 0
    if m.status == GRB.OPTIMAL:
        solution_type = "proven_optimal"
    elif has_incumbent:
        solution_type = "feasible_not_proven"
    else:
        solution_type = "none"

    info = {
        "status_code": m.status,
        "status": _status_name(m.status),
        "solution_type": solution_type,
        "proven_optimal": m.status == GRB.OPTIMAL,
        "proof_scope": "full_arc_flow",
        "formulation": "complete_free_assignment_delivery_pickup_arc_flow",
        "runtime": getattr(m, "Runtime", None),
        "sol_count": getattr(m, "SolCount", 0),
        "objective": None,
        "best_bound": None,
        "mip_gap": None,
        "assignment": None,
        "node_count": getattr(m, "NodeCount", None),
        "iteration_count": getattr(m, "IterCount", None),
        "work": getattr(m, "Work", None),
        "time_limit": time_limit,
        "work_limit": getattr(m.Params, "WorkLimit", None),
        "model_rows": getattr(m, "NumConstrs", None),
        "model_columns": getattr(m, "NumVars", None),
        "model_binary": getattr(m, "NumBinVars", None),
        "model_nonzeros": getattr(m, "NumNZs", None),
        "formulation_options": dict(ctx["formulation_options"]),
        "formulation_stats": dict(ctx["formulation_stats"]),
    }
    if has_incumbent:
        info["objective"] = m.ObjVal
        info["mip_gap"] = m.MIPGap
    # A dual bound is meaningful even if no integer incumbent has been found.
    # Retain it for audited long-running experiments without treating it as a
    # feasible or certified solution.
    try:
        info["best_bound"] = m.ObjBound
    except Exception:
        pass

    if not has_incumbent:
        if verbose:
            print(f"Model 4 exact: 无可行解 (status={info['status']})")
        if return_info:
            return None, None, None, info
        return None, None, None

    if m.status != GRB.OPTIMAL and not return_incumbent_on_time_limit:
        if verbose:
            print(
                "Model 4 exact: feasible incumbent exists but optimality "
                f"is not certified (status={info['status']}); routes withheld."
            )
        if return_info:
            return None, None, None, info
        return None, None, None

    depot_id = ctx["depot_id"]
    sol_x = {key: var.X for key, var in vars_dict["x"].items()}
    sol_z = {key: var.X for key, var in vars_dict["z"].items()}
    e1_routes = [
        route for k, route in extract_routes_e1(
            sol_x, depot_id,
            ctx["e1_del_nodes"] + ctx["e1_pick_nodes"], ctx["K"])
    ]
    e2_routes_by_sat = {}
    for s in ctx["F"]:
        e2_routes_by_sat[s] = extract_routes_e2(
            sol_z, s, ctx["e2_task_ids"], ctx["V_by_sat"][s])

    w = vars_dict["w"]
    assignment = {
        c: max(ctx["F"], key=lambda s: w[c, s].X)
        for c in ctx["C"]
    }
    info["assignment"] = assignment

    if verbose:
        print(f"Model 4 {'exact' if info['proven_optimal'] else 'MIP incumbent'} "
              f"目标值: {m.ObjVal:.2f} "
              f"(proven_optimal={info['proven_optimal']})")
        for s in ctx["F"]:
            cnt = sum(1 for v in assignment.values() if v == s)
            if cnt:
                print(f"  {s}: {cnt} 顾客, "
                      f"{len(e2_routes_by_sat[s])} 条 E2 路线")

    if return_info:
        return e1_routes, e2_routes_by_sat, assignment, info
    return e1_routes, e2_routes_by_sat, assignment


def solve_mip(problem, vehicles_per_satellite, **kwargs):
    """Run the full M4 arc-flow MIP and explicitly allow an incumbent.

    ``solve_exact`` remains certificate-only by default.  Long-running 50c
    experiments can use this helper for bounds and incumbent diagnostics
    without accidentally treating them as a proven optimum.
    """
    kwargs["return_incumbent_on_time_limit"] = True
    return solve_exact(problem, vehicles_per_satellite, **kwargs)
