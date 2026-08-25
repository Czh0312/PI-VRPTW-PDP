#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""50-customer route-pool experiments for Model 3 and Model 4 (C101).

The masters below optimize only the generated E2 route columns.  Their Gurobi
``OPTIMAL`` status is therefore a restricted-route-pool result, *not* a global
exact certificate.  Keep these experiments for incumbent generation and
diagnostics; use a full arc-flow MIP or complete branch-price-and-cut before
claiming an unrestricted 50-customer optimum.

Usage:
    PYTHONPYCACHEPREFIX=/tmp/pycache .venv/bin/python \
        experiments/run_50c_exact_m3_m4.py [--m3-time 600] [--m4-time 600] \
        [--alns-time 90] [--instance C101]
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.instance import ProblemInstance
from utils.data_loader import parse_dataset
from utils.task_builder import build_tasks_for_model
from models.alns_base import Route, Solution

DATA_DIR = ROOT / "data"
CONFIG_DIR = ROOT / "config"
OUT_DIR = ROOT / "output" / "exact_50c_m3_m4"
BCP_LB_PATH = (ROOT / "reference" / "exact_proof_evidence" / "exact_incumbents"
               / "m3_50_c101_e2routecg_s5_branchprice_ip_lb.json")


def load_fleet_config():
    return json.loads((CONFIG_DIR / "fleet_config.json").read_text(encoding="utf-8"))


def build_problem(data_path, fleet_cfg):
    nodes, _ = parse_dataset(str(data_path))
    problem = ProblemInstance(nodes, fleet_cfg, speed=fleet_cfg.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    return problem


# ---------------------------------------------------------------------------
# M3 restricted route-pool master with BCP bounds
# ---------------------------------------------------------------------------

def solve_m3_50c(problem, e1_tasks, e2_tasks_by_sat, vehicles,
                  alns_time=90, solve_time=600):
    from models.model_3.alns_solver import solve_alns as solve_m3_alns, Evaluator
    from models.model_3.route_pool_exact import build_route_pool_master, _normalize_columns
    from models.model_3.e2_route_cg import solve_satellite_route_cg
    import gurobipy as gp
    from gurobipy import GRB

    print("\n" + "=" * 70)
    print("M3: Restricted route-pool MIP with BCP lower bounds")
    print("=" * 70)

    # 1. ALNS incumbent
    t0 = time.time()
    e1_routes, e2_routes = solve_m3_alns(
        problem, e1_tasks, e2_tasks_by_sat, vehicles,
        TFp=9999.0, max_iterations=3000, max_time=alns_time,
        seed=42, verbose=False, strong_initial_solution=True)
    alns_elapsed = time.time() - t0
    ev = Evaluator(problem, e1_tasks, e2_tasks_by_sat, 9999.0,
                   vehicles_per_satellite=vehicles)
    sol = Solution()
    depot = problem.depot.id
    sol.e1_routes = [Route(depot, list(r[1:-1])) for r in e1_routes]
    for sat, routes in e2_routes.items():
        sol.e2_routes[sat] = [Route(sat, list(r[1:-1])) for r in routes]
    alns_cost = ev.total_cost(sol)
    print(f"ALNS incumbent: {alns_cost:.2f} ({alns_elapsed:.1f}s)")

    # 2. Per-satellite CG for route columns
    print("\nGenerating route columns via per-satellite CG...")
    all_columns = []
    for sat_id, tasks in e2_tasks_by_sat.items():
        if not tasks:
            continue
        result = solve_satellite_route_cg(
            problem, sat_id, tasks, vehicles,
            max_iterations=80, pricing_time_limit=60.0,
            pricing_pool_solutions=10, pricing_method="mip",
            integer_master=True, return_columns=True, output_flag=False)
        lb = result.get("lp_obj", "?")
        n_cols = result.get("columns", 0)
        iters = result.get("iterations", 0)
        elapsed_sat = result.get("elapsed", 0)
        print(f"  {sat_id}: LB={lb:.4f}, cols={n_cols}, "
              f"iters={iters}, {elapsed_sat:.1f}s" if isinstance(lb, float)
              else f"  {sat_id}: status={result.get('status')}")
        for cd in result.get("columns_data", []):
            all_columns.append(cd)
    print(f"Total columns: {len(all_columns)}")

    # 3. Load cached BCP satellite lower bounds
    if BCP_LB_PATH.exists():
        bcp_data = json.loads(BCP_LB_PATH.read_text(encoding="utf-8"))
        sat_lbs = bcp_data["bounds"]
        print(f"\nUsing cached BCP satellite lower bounds:")
        for s, lb in sat_lbs.items():
            print(f"  {s}: {lb:.4f}")
    else:
        sat_lbs = {}
        print("WARNING: No cached BCP bounds found, running without LB cuts")

    # 4. Build and solve route-pool master
    print(f"\nBuilding route-pool master...")
    m, vars_dict, ctx = build_route_pool_master(
        problem, e1_tasks, e2_tasks_by_sat, all_columns, vehicles,
        satellite_cost_lbs=sat_lbs or None)
    m.Params.OutputFlag = 1
    m.Params.TimeLimit = solve_time
    m.Params.MIPGap = 0.0
    m.Params.Presolve = 2
    m.Params.Cuts = 2

    # 5. Warm start from ALNS
    e2_task_map = {t.id: t for tasks in e2_tasks_by_sat.values() for t in tasks}
    cols = _normalize_columns(problem, e2_task_map, all_columns)
    path_to_rid = {col["path"]: rid for rid, col in enumerate(cols)}
    matched = 0
    for sat_id, routes in e2_routes.items():
        for route_path in routes:
            path = tuple(route_path)
            if path in path_to_rid:
                vars_dict["route"][path_to_rid[path]].Start = 1.0
                matched += 1
    K = list(range(problem.e1_vehicle_count))
    for k_idx, route in enumerate(e1_routes):
        if k_idx >= len(K):
            break
        k = K[k_idx]
        for i in range(len(route) - 1):
            ni, nj = route[i], route[i + 1]
            if (ni, nj, k) in vars_dict["x"]:
                vars_dict["x"][ni, nj, k].Start = 1.0
    print(f"Warm start: {matched} E2 routes matched")

    t0 = time.time()
    m.optimize()
    solve_elapsed = time.time() - t0

    result = {
        "instance": "50_customers/C101",
        "model": "M3",
        "method": "route_pool_master_with_bcp_bounds",
        "alns_incumbent": alns_cost,
        "alns_time": alns_elapsed,
        "columns": len(all_columns),
        "satellite_lbs": sat_lbs,
        "objective": float(m.ObjVal) if m.SolCount else None,
        "best_bound": float(m.ObjBound) if m.SolCount else None,
        "mip_gap": float(m.MIPGap) if m.SolCount else None,
        "status": int(m.Status),
        "solution_type": (
            "restricted_route_pool_optimal" if m.Status == GRB.OPTIMAL
            else "restricted_route_pool_feasible_not_proven"
        ),
        "proven_optimal": False,
        "proof_scope": "restricted_route_pool_only",
        "route_universe_complete": False,
        "solve_time": solve_elapsed,
        "total_time": alns_elapsed + solve_elapsed,
    }
    print("\nM2 RESULT: restricted route-pool result (not a global proof)")
    if result["objective"]:
        print(f"  Objective: {result['objective']:.4f}")
        print(f"  Gap: {result['mip_gap']:.4%}")
        print(f"  Solve time: {solve_elapsed:.1f}s")
        print(f"  Total time: {result['total_time']:.1f}s")
    return result


# ---------------------------------------------------------------------------
# M4 restricted route-pool master with free assignment
# ---------------------------------------------------------------------------

def solve_m4_50c(problem, vehicles, alns_time=90, solve_time=600):
    from models.model_4.alns_solver import solve_alns as solve_m4_alns, Evaluator
    from models.model_4.route_pool import solve_m4_exact

    print("\n" + "=" * 70)
    print("M4: Restricted route-pool MIP with free assignment")
    print("=" * 70)

    # 1. ALNS incumbent
    t0 = time.time()
    e1_routes, e2_routes, assignment = solve_m4_alns(
        problem, vehicles, TFp=9999.0, max_iterations=3000,
        max_time=alns_time, seed=42, verbose=False)
    alns_elapsed = time.time() - t0
    ev = Evaluator(problem, 9999.0, vehicles)
    sol = Solution()
    depot = problem.depot.id
    sol.e1_routes = [Route(depot, list(r[1:-1])) for r in e1_routes]
    for sat, routes in e2_routes.items():
        sol.e2_routes[sat] = [Route(sat, list(r[1:-1])) for r in routes]
    alns_cost = ev.total_cost(sol)
    print(f"ALNS incumbent: {alns_cost:.2f} ({alns_elapsed:.1f}s)")
    print(f"ALNS assignment: {dict(Counter(assignment.values()))}")

    # 2. Route-pool exact
    t0 = time.time()
    result = solve_m4_exact(
        problem, vehicles,
        incumbent_cost=alns_cost,
        e1_routes=e1_routes, e2_routes_by_sat=e2_routes,
        assignment=assignment,
        time_limit=solve_time, verbose=True, return_info=True)
    solve_elapsed = time.time() - t0

    info = result["info"] if isinstance(result, dict) else result[3]
    result_out = {
        "instance": "50_customers/C101",
        "model": "M4",
        "method": "route_pool_master_free_assignment",
        "alns_incumbent": alns_cost,
        "alns_time": alns_elapsed,
        "columns": info.get("columns"),
        "objective": info.get("objective"),
        "best_bound": info.get("best_bound"),
        "mip_gap": info.get("mip_gap"),
        "status": info.get("status"),
        "solution_type": info.get("solution_type"),
        "proven_optimal": info.get("proven_optimal"),
        "proof_scope": info.get("proof_scope"),
        "route_universe_complete": info.get("route_universe_complete"),
        "solve_time": solve_elapsed,
        "total_time": alns_elapsed + solve_elapsed,
        "assignment": result.get("assignment"),
    }
    print("\nM6 RESULT: restricted route-pool result (not a global proof)")
    if result_out["objective"]:
        print(f"  Objective: {result_out['objective']:.4f}")
        print(f"  Gap: {result_out['mip_gap']:.4%}")
        print(f"  Solve time: {solve_elapsed:.1f}s")
        print(f"  Total time: {result_out['total_time']:.1f}s")
    if result_out.get("assignment"):
        print(f"  Assignment: {dict(Counter(result_out['assignment'].values()))}")
    return result_out


def main():
    ap = argparse.ArgumentParser(
        description="50-customer exact solving for M3 and M4")
    ap.add_argument("--scale", default="50_customers")
    ap.add_argument("--instance", default="C101")
    ap.add_argument("--alns-time", type=float, default=90.0,
                    help="ALNS time budget for incumbent (seconds)")
    ap.add_argument("--m3-time", type=float, default=600.0,
                    help="Gurobi time limit for M3 route-pool master")
    ap.add_argument("--m4-time", type=float, default=600.0,
                    help="Gurobi time limit for M4 route-pool master")
    ap.add_argument("--skip-m3", action="store_true")
    ap.add_argument("--skip-m4", action="store_true")
    args = ap.parse_args()

    out_dir = OUT_DIR / f"{args.scale}__{args.instance}"
    out_dir.mkdir(parents=True, exist_ok=True)

    fleet_cfg = load_fleet_config()
    data_path = DATA_DIR / args.scale / f"{args.instance}.csv"
    if not data_path.exists():
        print(f"ERROR: data file not found: {data_path}")
        sys.exit(1)

    problem = build_problem(data_path, fleet_cfg)
    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, 3)
    vehicles = problem.fleet.e2_per_sat_count

    print(f"Instance: {args.scale}/{args.instance}")
    print(f"E1 trucks={problem.e1_vehicle_count} E1 tasks={len(e1_tasks)} "
          f"sats={len(e2_tasks_by_sat)} veh/sat={vehicles}")
    print(f"E2 tasks/sat: {dict({s: len(t) for s, t in e2_tasks_by_sat.items()})}")

    # M3
    if not args.skip_m3:
        m3_result = solve_m3_50c(problem, e1_tasks, e2_tasks_by_sat, vehicles,
                                  args.alns_time, args.m3_time)
        (out_dir / "m3_result.json").write_text(
            json.dumps(m3_result, indent=2, default=str), encoding="utf-8")
        print(f"\nSaved: {out_dir / 'm3_result.json'}")

    # M4
    if not args.skip_m4:
        m4_result = solve_m4_50c(problem, vehicles, args.alns_time, args.m4_time)
        (out_dir / "m4_result.json").write_text(
            json.dumps(m4_result, indent=2, default=str), encoding="utf-8")
        print(f"\nSaved: {out_dir / 'm4_result.json'}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    if not args.skip_m3 and m3_result:
        prov = "✓" if m3_result["proven_optimal"] else "✗"
        obj = f"{m3_result['objective']:.2f}" if m3_result["objective"] else "N/A"
        print(f"M3: {prov} PROVEN OPTIMAL  obj={obj}  "
              f"time={m3_result.get('total_time', 0):.1f}s")
    if not args.skip_m4 and m4_result:
        prov = "✓" if m4_result["proven_optimal"] else "✗"
        obj = f"{m4_result['objective']:.2f}" if m4_result["objective"] else "N/A"
        print(f"M4: {prov} PROVEN OPTIMAL  obj={obj}  "
              f"time={m4_result.get('total_time', 0):.1f}s")


if __name__ == "__main__":
    main()
