"""Strict exact-solver execution and result extraction for Model 2."""

from gurobipy import GRB

from models.routing.common.exact_utils import extract_routes_e1, extract_routes_e2

from .model import apply_route_warm_start, build_model


def _status_name(status_code):
    names = {
        GRB.OPTIMAL: "OPTIMAL",
        GRB.TIME_LIMIT: "TIME_LIMIT",
        GRB.INFEASIBLE: "INFEASIBLE",
        GRB.INF_OR_UNBD: "INF_OR_UNBD",
        GRB.INTERRUPTED: "INTERRUPTED",
        GRB.SUBOPTIMAL: "SUBOPTIMAL",
        GRB.NUMERIC: "NUMERIC",
    }
    return names.get(status_code, str(status_code))


def solve_exact(problem, vehicles_per_satellite, *, time_limit=None,
                gurobi_params=None, verbose=True, return_info=False,
                return_incumbent_on_time_limit=False,
                warm_start_routes=None, prune_e2_arcs=True, backend="auto"):
    """Solve small-scale Model 2 exactly.

    Returns ``(e1_routes, e2_routes_by_sat, assignment[, info])``.

    This is deliberately a *strict* exact entry point: unless
    ``return_incumbent_on_time_limit`` is explicitly enabled, it returns no
    routes after a time limit even if Gurobi found a feasible incumbent.  The
    incumbent objective/bound remain available through ``info`` for
    diagnostics, but only a Gurobi ``OPTIMAL`` status is a global certificate.
    """
    from models.routing.common.operating_policy import policy_for
    policy_for(problem).require_model(2)
    if backend == "auto":
        backend = "commodity_flow" if prune_e2_arcs else "indexed"
    if backend == "commodity_flow":
        from models.routing.common.commodity_flow import solve_commodity_flow
        return solve_commodity_flow(problem, 2, vehicles_per_satellite,
            verbose=verbose, time_limit=time_limit,
            return_incumbent_on_time_limit=return_incumbent_on_time_limit,
            return_info=return_info, warm_start_routes=warm_start_routes,
            gurobi_params=gurobi_params)
    if backend != "indexed":
        raise ValueError(f"Unknown exact backend: {backend}")
    m, vars_dict, ctx = build_model(
        problem,
        vehicles_per_satellite=vehicles_per_satellite,
        prune_e2_arcs=prune_e2_arcs,
    )
    apply_route_warm_start(vars_dict, ctx, problem, warm_start_routes)
    if not verbose:
        m.setParam("OutputFlag", 0)
    if time_limit is not None:
        m.setParam("TimeLimit", time_limit)
    m.setParam("MIPGap", 0.0)
    m.setParam("IntFeasTol", 1e-9)
    m.setParam("FeasibilityTol", 1e-9)
    if gurobi_params:
        for name, value in gurobi_params.items():
            m.setParam(name, value)
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
        "formulation": "complete_free_assignment_delivery_arc_flow",
        "runtime": getattr(m, "Runtime", None),
        "sol_count": getattr(m, "SolCount", 0),
        "objective": None,
        "best_bound": None,
        "mip_gap": None,
        "assignment": None,
        "external_m1_incumbent": warm_start_routes is not None,
        "formulation_stats": dict(ctx["formulation_stats"]),
        "model_rows": getattr(m, "NumConstrs", None),
        "model_columns": getattr(m, "NumVars", None),
        "model_binary": getattr(m, "NumBinVars", None),
        "model_nonzeros": getattr(m, "NumNZs", None),
    }
    if has_incumbent:
        info["objective"] = m.ObjVal
        info["mip_gap"] = m.MIPGap
    # Gurobi can provide a valid dual bound even before finding an incumbent.
    # Keep it in the diagnostic contract so a 50c time-limit result remains
    # informative without being mistaken for a feasible solution.
    try:
        info["best_bound"] = m.ObjBound
    except Exception:
        pass

    if not has_incumbent:
        if verbose:
            print(f"Model 2 exact: no feasible solution (status={info['status']})")
        if return_info:
            return None, None, None, info
        return None, None, None

    if m.status != GRB.OPTIMAL and not return_incumbent_on_time_limit:
        if verbose:
            print(
                "Model 2 exact: feasible incumbent exists but optimality "
                f"is not certified (status={info['status']}); routes withheld."
            )
        if return_info:
            return None, None, None, info
        return None, None, None

    sol_x = {key: var.X for key, var in vars_dict["x"].items()}
    sol_z = {key: var.X for key, var in vars_dict["z"].items()}
    e1_routes = [
        route
        for _, route in extract_routes_e1(
            sol_x, ctx["depot_id"], ctx["e1_del_nodes"], ctx["K"]
        )
    ]
    e2_routes_by_sat = {
        s: extract_routes_e2(
            sol_z, s, ctx["e2_task_ids"], ctx["V_by_sat"][s]
        )
        for s in ctx["F"]
    }
    w = vars_dict["w"]
    assignment = {
        c: max(ctx["F"], key=lambda s: w[c, s].X) for c in ctx["C"]
    }
    info["assignment"] = assignment

    if verbose:
        print(
            f"Model 2 {'exact' if info['proven_optimal'] else 'MIP incumbent'} "
            f"objective: {m.ObjVal:.4f} "
            f"(proven_optimal={info['proven_optimal']})"
        )
        for s in ctx["F"]:
            count = sum(1 for assigned in assignment.values() if assigned == s)
            if count:
                print(f"  {s}: {count} customers, "
                      f"{len(e2_routes_by_sat[s])} E2 routes")

    if return_info:
        return e1_routes, e2_routes_by_sat, assignment, info
    return e1_routes, e2_routes_by_sat, assignment


def solve_mip(problem, vehicles_per_satellite, **kwargs):
    """Run the full M2 arc-flow MIP and explicitly allow an incumbent.

    ``solve_exact`` is certification-first.  Long 50-customer experiments
    that need a warm start or an upper bound should call this opt-in helper
    instead, then inspect ``info['proven_optimal']`` before reporting an exact
    result.
    """
    kwargs["return_incumbent_on_time_limit"] = True
    return solve_exact(problem, vehicles_per_satellite, **kwargs)
