"""Sequential M3 optimum followed by a conditional exact relay MILP."""
import math
import time

from gurobipy import GRB

from models.routing.variants.model_3.exact_solver import solve_exact as solve_m3
from models.routing.variants.model_5.model import extract_solution
from .baseline import prepare_baseline, initial_info
from .model import build_model


def optimize_stage2(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                    baseline, deadline, TFp=9999.0, verbose=False,
                    gurobi_params=None, relay_fixes=None, handoff_fixes=None):
    """Shared exact repair. Restrictions are explicit and never global proofs."""
    if time.perf_counter() >= deadline:
        return None
    model, variables, ctx = build_model(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
        baseline, TFp=TFp, relay_fixes=relay_fixes, handoff_fixes=handoff_fixes,
    )
    try:
        model.Params.OutputFlag = int(verbose)
        for name, value in (gurobi_params or {}).items():
            model.setParam(name, value)
        model.Params.OutputFlag = int(verbose)
        model.Params.MIPGap = 0
        model.Params.MIPGapAbs = 0
        model.Params.IntFeasTol = 1e-9
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return None
        if math.isfinite(remaining):
            model.Params.TimeLimit = remaining
        model.optimize()
        bound = float(model.ObjBound)
        result = {
            "status_code": int(model.Status),
            "conditional_best_bound": bound if math.isfinite(bound) else None,
            "conditional_proven_optimal": model.Status == GRB.OPTIMAL,
            "feasible": model.SolCount > 0,
            "restricted": bool(relay_fixes or handoff_fixes),
        }
        if not model.SolCount:
            return result
        e1, e2, transfers = extract_solution(model, variables, ctx)
        modes = {node.original_task: node.relocated_to
                 for nid, node in ctx["e2_info"].items()
                 if nid in variables["y"] and variables["y"][nid].X > 0.5}
        # Preserve unambiguous operation IDs, not only plotting-friendly names.
        raw = {}
        for sat, paths in baseline["e2_routes"].items():
            raw[sat] = []
            for index in range(len(paths)):
                successor = {i: j for (i, j, v), arc in variables["z"].items()
                             if v == (sat, index) and arc.X > 0.5}
                path = [sat]
                seen = set()
                while path[-1] in successor:
                    nxt = successor[path[-1]]
                    path.append(nxt)
                    if nxt == sat:
                        break
                    if nxt in seen:
                        raise RuntimeError("M8 extraction found a cycle")
                    seen.add(nxt)
                raw[sat].append(path)
        result.update(
            routes=(e1, e2), raw_e2_routes=raw, modes=modes,
            objective=float(model.ObjVal), transshipments=transfers,
            conditional_mip_gap=float(model.MIPGap),
        )
        return result
    finally:
        model.dispose()


def merge_stage2(info, result):
    """Retain the known feasible no-relay solution if a repair is worse."""
    if result is None:
        return None
    info["stage2_status_code"] = result["status_code"]
    # A restricted repair bound is not a bound on the full stage-two problem.
    if not result["restricted"]:
        info["conditional_best_bound"] = result["conditional_best_bound"]
    if not result.get("feasible"):
        return None
    if result["objective"] > info["objective"] + 1e-6:
        return None
    info.update(
        objective=result["objective"],
        transshipments=result["transshipments"],
        transshipment_count=len(result["transshipments"]),
        transshipment_improvement=info["stage1_objective"] - result["objective"],
        raw_e2_routes=result["raw_e2_routes"],
        selected_modes=result["modes"],
    )
    proven = result["conditional_proven_optimal"] and not result["restricted"]
    info["conditional_proven_optimal"] = proven
    if not result["restricted"]:
        info["conditional_mip_gap"] = result["conditional_mip_gap"]
    info["two_stage_proven_optimal"] = proven and info["stage1_proven_optimal"]
    info["status"] = "CONDITIONAL_OPTIMAL" if proven else "FEASIBLE"
    info["solution_type"] = "conditional_optimal" if proven else "feasible_not_proven"
    # proven_optimal remains False: this is not the joint routing optimum.
    return result["routes"]


def solve_exact(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                TFp=9999.0, verbose=True, time_limit=300, mip_gap=0.0,
                return_incumbent_on_time_limit=False, return_info=False,
                stage1_time_ratio=0.6, base_routes=None, base_info=None,
                gurobi_params=None):
    """Do not start relay optimization until stage-one optimality is certified.

    Supplied base_info is a caller-provided M3 certificate, recorded verbatim;
    supplied routes are independently checked for M3 feasibility. Use a result
    from the same M3 solve, never a certificate for a different input instance.
    """
    if mip_gap != 0:
        raise ValueError("M8 精确求解要求 mip_gap=0")
    if not 0 < stage1_time_ratio < 1:
        raise ValueError("stage1_time_ratio 必须在 (0, 1) 内")
    if time_limit is not None and time_limit <= 0:
        raise ValueError("time_limit 必须为正数或 None")
    started = time.perf_counter()
    deadline = math.inf if time_limit is None else started + time_limit
    if base_routes is None:
        params = dict(gurobi_params or {})
        params.update(MIPGap=0, MIPGapAbs=0)
        e1, e2, stage1 = solve_m3(
            problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
            TFp=TFp, verbose=verbose,
            time_limit=None if time_limit is None else time_limit * stage1_time_ratio,
            return_info=True, return_incumbent_on_time_limit=True,
            gurobi_params=params,
        )
        if e1 is None or e2 is None:
            info = {"model": 8, "status": "NO_BASE_SOLUTION", "objective": None,
                    "proven_optimal": False, "conditional_proven_optimal": False,
                    "stage1": stage1, "runtime": time.perf_counter() - started}
            return (None, None, info) if return_info else (None, None)
        base_routes, base_info = (e1, e2), stage1
    stage1 = dict(base_info or {})
    base = prepare_baseline(problem, e1_tasks, e2_tasks_by_sat,
                            vehicles_per_satellite, base_routes, TFp)
    if stage1.get("objective") is not None and not math.isclose(
            stage1["objective"], base["objective"], rel_tol=1e-8, abs_tol=1e-6):
        raise ValueError("M3 证书目标值与基础路线不一致")
    info = initial_info(base, stage1)
    routes = (base["e1_routes"], base["e2_routes"])
    if not info["stage1_proven_optimal"]:
        info["status"] = "BASE_OPTIMUM_NOT_PROVEN"
        info["stage2_skipped_reason"] = "M3 最优性未证明；不启动第二阶段"
    else:
        result = optimize_stage2(
            problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
            base, deadline, TFp, verbose, gurobi_params,
        )
        routes = merge_stage2(info, result) or routes
    info["runtime"] = time.perf_counter() - started
    if verbose:
        print(f"M8: M3={base['objective']:.6f}, relay={info['objective']:.6f}, "
              f"conditional_optimal={info['conditional_proven_optimal']}")
    if not info["conditional_proven_optimal"] and not return_incumbent_on_time_limit:
        routes = (None, None)
    return (*routes, info) if return_info else routes
