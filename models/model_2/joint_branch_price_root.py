# -*- coding: utf-8 -*-
"""Joint branch-price root node for Model 2.

Column-generation loop that solves the LP relaxation of the route-pool master
with E1↔E2 linking, extracts joint duals, and prices new E2 routes against the
linking duals. This is the root node of a full branch-price tree — it proves LP
optimality over the generated column pool, but does NOT by itself prove global
integer optimality unless the LP bound meets the incumbent.

Termination states:
  PROVEN_OPTIMAL      — lp_bound >= incumbent - tol  (gap closed at root)
  LP_PROVED           — all satellites pricing proved; lp_bound < incumbent
  PRICING_STALL       — some satellite unproved, no new columns generated
  MASTER_{status}     — master LP failed
  MAX_ITERATIONS      — CG iteration limit reached
  NO_COVER            — some E2 task has no feasible column
"""

import time

import gurobipy as gp
from gurobipy import GRB

from .route_pool_exact import (
    build_route_pool_master,
    extract_joint_duals,
    _normalize_columns,
    _status_name,
)
from .e2_route_cg import (
    EPS,
    PRICING_PROOF_TOL,
    _singleton_column,
)
from .joint_pricing import solve_joint_pricing_mip


def solve_joint_branch_price_root(problem, e1_tasks, e2_tasks_by_sat,
                                  vehicles_per_satellite,
                                  initial_columns,
                                  incumbent=None,
                                  max_cg_iterations=200,
                                  pricing_time_limit=20.0,
                                  proof_as_feasibility=True,
                                  master_time_limit=600.0,
                                  satellite_cost_lbs=None,
                                  output_flag=False):
    """Run column generation on the joint E1+E2 route-pool master LP.

    Parameters
    ----------
    problem : ProblemInstance
    e1_tasks : list[Task]
    e2_tasks_by_sat : dict[str, list[Task]]
    vehicles_per_satellite : int
    initial_columns : list[dict]
        Seed columns with keys sat_id, path, tasks, cost, source.
    incumbent : float | None
        Known upper bound (feasible integer solution). If lp_bound >= incumbent - EPS,
        returns PROVEN_OPTIMAL.
    max_cg_iterations : int
    pricing_time_limit : float
        Time limit per satellite pricing MIP (seconds).
    proof_as_feasibility : bool
    master_time_limit : float
        Time limit per master LP solve (seconds).
    satellite_cost_lbs : dict[str, float] | None
        Per-satellite BCP cost lower bounds (strengthen the LP).
    output_flag : bool

    Returns
    -------
    dict with keys:
        status, lp_bound, incumbent, gap_pct, proven_optimal,
        iterations, columns_added, column_count, master_status,
        link_dual_max_abs, per_sat, elapsed, message
    """
    started = time.time()
    e2_task_map = {
        t.id: t
        for tasks in e2_tasks_by_sat.values()
        for t in tasks
    }
    all_task_ids = set(e2_task_map)

    # Normalize and validate initial columns
    cols = _normalize_columns(problem, e2_task_map, list(initial_columns))
    seen_paths = {c["path"] for c in cols}

    # Coverage guard: every E2 task must appear in at least one column
    covered_tasks = set()
    for c in cols:
        covered_tasks.update(c["tasks"])
    missing_tasks = all_task_ids - covered_tasks
    if missing_tasks:
        for tid in sorted(missing_tasks):
            task = e2_task_map[tid]
            sat_id = None
            for s, tasks in e2_tasks_by_sat.items():
                if task in tasks:
                    sat_id = s
                    break
            if sat_id is None:
                continue
            singleton = _singleton_column(problem, sat_id, task)
            if singleton is not None:
                col_dict = {
                    "sat_id": singleton.sat_id,
                    "path": singleton.path,
                    "tasks": singleton.tasks,
                    "cost": singleton.cost,
                    "source": "singleton_fallback",
                }
                path_tuple = col_dict["path"]
                if path_tuple not in seen_paths:
                    cols.append(col_dict)
                    seen_paths.add(path_tuple)
                    missing_tasks.discard(tid)
        if missing_tasks:
            return {
                "status": "NO_COVER",
                "proven_optimal": False,
                "message": f"Uncoverable tasks: {sorted(missing_tasks)}",
                "elapsed": time.time() - started,
            }

    # CG loop
    per_sat_info = {}
    link_dual_max_abs = 0.0
    total_columns_added = 0
    iteration = 0
    lp_bound = None

    for iteration in range(max_cg_iterations):
        # Build and solve the relaxed master LP
        m, vars_dict, ctx = build_route_pool_master(
            problem, e1_tasks, e2_tasks_by_sat, cols,
            vehicles_per_satellite,
            satellite_cost_lbs=satellite_cost_lbs,
            relax=True, return_duals=True,
        )
        m.Params.Method = 2        # barrier
        m.Params.Crossover = 0     # no crossover (faster, valid duals)
        m.Params.OutputFlag = 1 if output_flag else 0
        m.Params.TimeLimit = master_time_limit
        m.optimize()

        if m.status != GRB.OPTIMAL:
            status_msg = f"MASTER_{_status_name(m.status)}"
            return {
                "status": status_msg,
                "proven_optimal": False,
                "message": f"Master LP not optimal at iteration {iteration}: {status_msg}",
                "lp_bound": lp_bound,
                "incumbent": incumbent,
                "iterations": iteration,
                "columns_added": total_columns_added,
                "column_count": len(cols),
                "elapsed": time.time() - started,
            }

        lp_bound = float(m.ObjVal)
        e1_cost = _compute_e1_cost(problem, vars_dict, ctx)
        e2_cost = lp_bound - e1_cost

        # Check for early termination: LP bound meets incumbent
        if incumbent is not None and lp_bound >= incumbent - 1e-6:
            return {
                "status": "PROVEN_OPTIMAL",
                "proven_optimal": True,
                "lp_bound": lp_bound,
                "incumbent": incumbent,
                "gap_pct": 0.0,
                "iterations": iteration + 1,
                "columns_added": total_columns_added,
                "column_count": len(cols),
                "link_dual_max_abs": link_dual_max_abs,
                "message": f"Root LP bound {lp_bound:.4f} >= incumbent {incumbent:.4f}",
                "elapsed": time.time() - started,
            }

        # Extract duals
        duals = extract_joint_duals(m, vars_dict, ctx, e2_tasks_by_sat)
        if duals is None:
            return {
                "status": "DUAL_EXTRACTION_FAILED",
                "proven_optimal": False,
                "message": "extract_joint_duals returned None",
                "lp_bound": lp_bound,
                "iterations": iteration,
                "elapsed": time.time() - started,
            }
        link_dual_max_abs = duals["link_dual_max_abs"]

        # Price each satellite
        all_sats_proved = True
        new_cols_this_iter = 0

        for sat_id, tasks in e2_tasks_by_sat.items():
            if not tasks:
                continue
            pricing_cols, pinfo = solve_joint_pricing_mip(
                problem, sat_id, tasks,
                pi_cover=duals["pi_cover"],
                nu_vehicle=duals["nu_vehicle"].get(sat_id, 0.0),
                sigma_start=duals["sigma_start"].get(sat_id, 0.0),
                rho_pickup=duals["rho_pickup"].get(sat_id, 0.0),
                alpha_lo=duals["alpha_lo"],
                alpha_hi=duals["alpha_hi"],
                pricing_time_limit=pricing_time_limit,
                proof_as_feasibility=proof_as_feasibility,
                output_flag=output_flag,
            )
            sat_proved = pinfo.get("reduced_cost_bound", float("-inf")) >= -PRICING_PROOF_TOL
            if not sat_proved:
                all_sats_proved = False

            sat_added = 0
            for col_obj in pricing_cols:
                if col_obj.path in seen_paths:
                    continue
                cols.append({
                    "sat_id": col_obj.sat_id,
                    "path": col_obj.path,
                    "tasks": col_obj.tasks,
                    "cost": col_obj.cost,
                    "source": col_obj.source,
                })
                seen_paths.add(col_obj.path)
                sat_added += 1

            new_cols_this_iter += sat_added
            per_sat_info[sat_id] = {
                "pricing_status": pinfo.get("status"),
                "rc_bound": pinfo.get("reduced_cost_bound"),
                "rc": pinfo.get("reduced_cost"),
                "proved": sat_proved,
                "sigma": duals["sigma_start"].get(sat_id, 0.0),
                "rho": duals["rho_pickup"].get(sat_id, 0.0),
                "cols_added": sat_added,
                "runtime": pinfo.get("runtime"),
            }

        total_columns_added += new_cols_this_iter

        # Termination checks
        if all_sats_proved:
            proven = incumbent is not None and lp_bound >= incumbent - 1e-6
            gap_pct = (float(incumbent - lp_bound) / incumbent * 100.0
                       if incumbent and lp_bound else None)
            return {
                "status": "PROVEN_OPTIMAL" if proven else "LP_PROVED",
                "proven_optimal": proven,
                "lp_bound": lp_bound,
                "incumbent": incumbent,
                "gap_pct": gap_pct,
                "iterations": iteration + 1,
                "columns_added": total_columns_added,
                "column_count": len(cols),
                "link_dual_max_abs": link_dual_max_abs,
                "per_sat": per_sat_info,
                "e1_cost": e1_cost,
                "e2_cost": e2_cost,
                "message": ("Root LP proved optimal over column universe"
                            if not proven else
                            f"PROVEN OPTIMAL: lp_bound={lp_bound:.4f} >= incumbent={incumbent}"),
                "elapsed": time.time() - started,
            }

        if new_cols_this_iter == 0:
            return {
                "status": "PRICING_STALL",
                "proven_optimal": False,
                "message": (f"No new columns at iteration {iteration}. "
                            f"link_dual_max_abs={link_dual_max_abs:.2e}. "
                            f"E1 LP integrality gap likely prevents root proof."),
                "lp_bound": lp_bound,
                "incumbent": incumbent,
                "gap_pct": (float(incumbent - lp_bound) / incumbent * 100.0
                            if incumbent and lp_bound else None),
                "iterations": iteration + 1,
                "columns_added": total_columns_added,
                "column_count": len(cols),
                "link_dual_max_abs": link_dual_max_abs,
                "per_sat": per_sat_info,
                "e1_cost": e1_cost,
                "e2_cost": e2_cost,
                "elapsed": time.time() - started,
            }

    # Max iterations reached
    return {
        "status": "MAX_ITERATIONS",
        "proven_optimal": False,
        "message": f"CG iteration limit ({max_cg_iterations}) reached",
        "lp_bound": lp_bound,
        "incumbent": incumbent,
        "gap_pct": (float(incumbent - lp_bound) / incumbent * 100.0
                    if incumbent and lp_bound else None),
        "iterations": iteration + 1,
        "columns_added": total_columns_added,
        "column_count": len(cols),
        "link_dual_max_abs": link_dual_max_abs,
        "per_sat": per_sat_info,
        "elapsed": time.time() - started,
    }


def _compute_e1_cost(problem, vars_dict, ctx):
    """Compute E1 portion of the LP objective from solution values."""
    x = vars_dict["x"]
    depot_id = ctx["depot_id"]
    n1e = ctx["N1E"]
    K = ctx["K"]
    e1_task_map = ctx["e1_task_map"]
    e2_task_map = ctx["e2_task_map"]

    def _loc(node_id):
        if node_id == depot_id or node_id in {s for s in ctx.get("F", [])}:
            return node_id
        task = e1_task_map.get(node_id) or e2_task_map.get(node_id)
        return task.cust if task is not None else node_id

    total = 0.0
    for k in K:
        for i in n1e:
            for j in n1e:
                if i != j:
                    total += problem.distance(_loc(i), _loc(j)) * x[i, j, k].X
    return float(total)


def solve_joint_lp_ceiling(problem, e1_tasks, e2_tasks_by_sat,
                           vehicles_per_satellite, columns,
                           satellite_cost_lbs=None,
                           time_limit=600, output_flag=False):
    """One-shot LP solve: measure the ceiling that CG can achieve.

    This is the pre-flight diagnostic (Gate 0). Returns the LP optimum
    with the given column pool — this is the UPPER BOUND on what CG can
    prove (minimization, so CG can only push the LP value DOWN).
    """
    m, vars_dict, ctx = build_route_pool_master(
        problem, e1_tasks, e2_tasks_by_sat, columns,
        vehicles_per_satellite,
        satellite_cost_lbs=satellite_cost_lbs,
        relax=True, return_duals=True,
    )
    m.Params.Method = 2
    m.Params.Crossover = 0
    m.Params.OutputFlag = 1 if output_flag else 0
    m.Params.TimeLimit = time_limit
    started = time.time()
    m.optimize()
    elapsed = time.time() - started

    result = {
        "status": _status_name(m.status),
        "status_code": int(m.status),
        "elapsed": elapsed,
        "column_count": len(ctx["route_columns"]),
    }
    if m.SolCount:
        result["obj"] = float(m.ObjVal)
        e1_cost = _compute_e1_cost(problem, vars_dict, ctx)
        result["e1_cost"] = e1_cost
        result["e2_cost"] = float(m.ObjVal) - e1_cost
        duals = extract_joint_duals(m, vars_dict, ctx, e2_tasks_by_sat)
        if duals:
            result["link_dual_max_abs"] = duals["link_dual_max_abs"]
    return result
