#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the certified exact decomposition for Model 3 at 100-customer scale.

Pipeline (all artifacts cached under --output-dir):

  1. columns      -- merged E2 route columns (JSON) or per-satellite CG
  2. satellite LB -- certified per-satellite lower bounds (JSON) or CG cert
  3. incumbent UB -- flag, or the restricted route-pool master MIP optimum
  4. E1 frontier  -- enumerate E1 plans below C0 = UB - sum L_s (no-good cuts)
  5. floor certs  -- per-satellite s_max>=floor column generation certificates
  6. verdict      -- PROVEN_OPTIMAL iff every Pareto class closes

Examples
--------
# C101 with all cached artifacts (fast, reproduces the strict-closure proof)
.venv/bin/python experiments/run_exact_m3_decomposition.py \\
    --scale 100_customers --instance C101 \\
    --columns-json reference/exact_proof_evidence/merged_columns/100_customers__C101__columns.json \\
    --satellite-lb-json reference/exact_proof_evidence/exact_incumbents/m3_100_c101_satellite_bcp_exact_lbs.json \\
    --incumbent 1829.0093866572092 \\
    --output-dir output/exact_m3_decomp

# Fresh instance, generating everything on the fly with time budgets
.venv/bin/python experiments/run_exact_m3_decomposition.py \\
    --scale 100_customers --instance C103 \\
    --per-sat-lb-max-iter 80 --per-sat-lb-pricing-time 60 \\
    --floor-cert-time-limit 300 --output-dir output/exact_m3_decomp
"""

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from core.instance import ProblemInstance
from models.model_3.exact_decomposition import (
    certify_satellite_unrestricted_lb,
    rebuild_e1_plan,
    solve_m3_exact_certificate,
)
from models.model_3.route_pool_exact import build_route_pool_master
from utils.data_loader import parse_dataset
from utils.task_builder import build_tasks_for_model

CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_COLUMNS_JSON = (
    PROJECT_ROOT / "reference" / "exact_proof_evidence" / "merged_columns"
    / "100_customers__C101__columns.json"
)
DEFAULT_SAT_LB_JSON = (
    PROJECT_ROOT / "reference" / "exact_proof_evidence" / "exact_incumbents"
    / "m3_100_c101_satellite_bcp_exact_lbs.json"
)


def load_fleet_config():
    return json.loads((CONFIG_DIR / "fleet_config.json").read_text(encoding="utf-8"))


def build_problem(data_path, fleet_cfg):
    nodes_dict, _ = parse_dataset(str(data_path))
    problem = ProblemInstance(nodes_dict, fleet_cfg, speed=fleet_cfg.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    return problem


def load_columns(columns_json_path):
    payload = json.loads(Path(columns_json_path).read_text(encoding="utf-8"))
    columns = payload.get("columns", payload if isinstance(payload, list) else [])
    if not isinstance(columns, list):
        columns = []
    print(f"Loaded {len(columns)} columns from {columns_json_path}")
    return columns


def load_satellite_lbs(path):
    if not path or not Path(path).exists():
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if "satellite_cost_lbs" in payload:
        lbs = payload["satellite_cost_lbs"]
    elif "bounds" in payload:
        lbs = payload["bounds"]
    elif "details" in payload:
        lbs = {d.get("sat_id", d.get("satellite")): d["bound"]
               for d in payload["details"]}
    else:
        lbs = payload
    out = {str(k): float(v) for k, v in lbs.items()}
    print(f"Loaded {len(out)} satellite lower bounds from {path}")
    return out


def generate_columns(problem, e2_tasks_by_sat, vehicles_per_satellite,
                     max_iter, pricing_time_limit, pool_solutions, output_flag):
    print("Generating per-satellite route columns via CG...")
    all_columns = []
    for sat_id, tasks in e2_tasks_by_sat.items():
        if not tasks:
            continue
        result = certify_satellite_unrestricted_lb(
            problem, sat_id, tasks, vehicles_per_satellite, [],
            max_iter=max_iter, pricing_time_limit=pricing_time_limit,
            pool_solutions=pool_solutions, output_flag=output_flag,
            verbose=True,
        )
        all_columns.extend(result["columns"])
    print(f"Generated {len(all_columns)} columns total")
    return all_columns


def certify_satellite_lbs(problem, e2_tasks_by_sat, vehicles_per_satellite,
                          initial_columns, max_iter, pricing_time_limit,
                          pool_solutions, output_flag):
    print("Certifying per-satellite unrestricted lower bounds via CG...")
    cols_by_sat = {}
    for c in initial_columns:
        cols_by_sat.setdefault(c["sat_id"], []).append(c)
    lbs = {}
    lb_details = {}
    for sat_id, tasks in e2_tasks_by_sat.items():
        if not tasks:
            continue
        result = certify_satellite_unrestricted_lb(
            problem, sat_id, tasks, vehicles_per_satellite,
            cols_by_sat.get(sat_id, []),
            max_iter=max_iter, pricing_time_limit=pricing_time_limit,
            pool_solutions=pool_solutions, output_flag=output_flag,
            verbose=True,
        )
        lb_details[sat_id] = result
        if result["lp_valid"] and result["lp_bound"] is not None:
            lbs[sat_id] = float(result["lp_bound"])
        else:
            print(f"  WARNING: {sat_id} LB not certified "
                  f"(status={result['status']}, valid={result['lp_valid']})")
    print(f"Certified LBs for {len(lbs)}/{len(e2_tasks_by_sat)} satellites: "
          f"sum={sum(lbs.values()):.4f}")
    return lbs, lb_details


def solve_incumbent_master(problem, e1_tasks, e2_tasks_by_sat, columns,
                           vehicles_per_satellite, satellite_lbs,
                           time_limit, output_flag):
    print("Solving restricted route-pool master MIP for a feasible incumbent...")
    m, vars_dict, ctx = build_route_pool_master(
        problem, e1_tasks, e2_tasks_by_sat, columns,
        vehicles_per_satellite,
        satellite_cost_lbs=satellite_lbs or None,
    )
    m.Params.OutputFlag = 1 if output_flag else 0
    m.Params.TimeLimit = time_limit
    m.Params.MIPGap = 0.0
    m.optimize()
    if m.SolCount:
        print(f"Incumbent from pool master: {m.ObjVal:.6f} "
              f"(status={m.Status}, gap={m.MIPGap:.4%})")
        return float(m.ObjVal), {
            "status": int(m.Status),
            "objective": float(m.ObjVal),
            "mip_gap": float(m.MIPGap),
            "bound": float(m.ObjBound),
        }
    return None, {"status": int(m.Status), "objective": None}


def main():
    ap = argparse.ArgumentParser(
        description="Model 3 certified exact decomposition (100-customer scale)")
    ap.add_argument("--scale", default="100_customers")
    ap.add_argument("--instance", default="C101")
    ap.add_argument("--columns-json", default=None,
                    help="Merged columns JSON (default: C101 reference pool)")
    ap.add_argument("--satellite-lb-json", default=None,
                    help="Certified per-satellite LB JSON")
    ap.add_argument("--incumbent", type=float, default=None,
                    help="Feasible upper bound (default: route-pool master MIP)")
    ap.add_argument("--incumbent-time-limit", type=float, default=1800.0)
    ap.add_argument("--max-frontier-plans", type=int, default=5000)
    ap.add_argument("--frontier-solve-time-limit", type=float, default=60.0)
    ap.add_argument("--e1-mip-gap", type=float, default=0.01,
                    help="MIPGap for each E1 frontier solve (0.01 = stop at "
                         "the first acceptable plan; completeness only needs "
                         "the final infeasibility proof)")
    ap.add_argument("--floor-cert-time-limit", type=float, default=300.0)
    ap.add_argument("--floor-cert-max-iter", type=int, default=80)
    ap.add_argument("--floor-cert-pool-solutions", type=int, default=10)
    ap.add_argument("--total-time-limit", type=float, default=3600.0,
                    help="Hard wall-clock budget for the whole certified "
                         "decomposition (default 3600s); the run stops cleanly "
                         "with a verdict at the deadline.")
    ap.add_argument("--resume-frontier-json", default=None,
                    help="JSON of previously enumerated E1 plans (arcs) to "
                         "resume the frontier enumeration from where it stopped")
    ap.add_argument("--per-sat-lb-max-iter", type=int, default=60)
    ap.add_argument("--per-sat-lb-pricing-time", type=float, default=60.0)
    ap.add_argument("--per-sat-lb-pool-solutions", type=int, default=10)
    ap.add_argument("--output-dir", type=Path,
                    default=str(PROJECT_ROOT / "output" / "exact_m3_decomp"))
    ap.add_argument("--verbose-gurobi", action="store_true")
    args = ap.parse_args()

    out_dir = Path(args.output_dir) / f"{args.scale}__{args.instance}"
    out_dir.mkdir(parents=True, exist_ok=True)
    run_started = time.time()

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
    print(f"E2 tasks/sat: { {s: len(t) for s, t in e2_tasks_by_sat.items()} }")

    # 1. columns
    columns_json = args.columns_json
    if columns_json is None and args.scale == "100_customers" and args.instance == "C101":
        columns_json = str(DEFAULT_COLUMNS_JSON)
    columns = None
    if columns_json and Path(columns_json).exists():
        columns = load_columns(columns_json)
    if columns is None:
        cols_path = out_dir / "columns.json"
        if cols_path.exists():
            columns = load_columns(str(cols_path))
        else:
            columns = generate_columns(
                problem, e2_tasks_by_sat, vehicles,
                args.per_sat_lb_max_iter, args.per_sat_lb_pricing_time,
                args.per_sat_lb_pool_solutions, args.verbose_gurobi,
            )
            cols_path.write_text(
                json.dumps({"columns": columns}, indent=1), encoding="utf-8")
            print(f"Saved columns: {cols_path}")

    # 2. satellite lower bounds
    sat_lb_json = args.satellite_lb_json
    if sat_lb_json is None and args.scale == "100_customers" and args.instance == "C101":
        sat_lb_json = str(DEFAULT_SAT_LB_JSON)
    satellite_lbs = {}
    lb_details = {}
    if sat_lb_json and Path(sat_lb_json).exists():
        satellite_lbs = load_satellite_lbs(sat_lb_json)
    else:
        lbs_path = out_dir / "satellite_lbs.json"
        if lbs_path.exists():
            satellite_lbs = load_satellite_lbs(str(lbs_path))
            lb_details = {"cached": True}
        else:
            satellite_lbs, lb_details = certify_satellite_lbs(
                problem, e2_tasks_by_sat, vehicles, columns,
                args.per_sat_lb_max_iter, args.per_sat_lb_pricing_time,
                args.per_sat_lb_pool_solutions, args.verbose_gurobi,
            )
            if satellite_lbs:
                lbs_path.write_text(
                    json.dumps({
                        "scale": args.scale,
                        "instance": args.instance,
                        "total": sum(satellite_lbs.values()),
                        "satellite_cost_lbs": satellite_lbs,
                        "details": lb_details,
                    }, indent=1, default=str),
                    encoding="utf-8")
                print(f"Saved satellite LBs: {lbs_path}")

    active_sats = [s for s, tasks in e2_tasks_by_sat.items() if tasks]
    missing = [s for s in active_sats if s not in satellite_lbs]
    if missing:
        print(f"\nERROR: certified lower bounds missing for {missing}. "
              f"Per-satellite CG did not certify them within the budget; "
              f"raise --per-sat-lb-* limits or supply --satellite-lb-json.")
        sys.exit(2)

    # 3. incumbent
    incumbent = args.incumbent
    incumbent_info = None
    if incumbent is None:
        incumbent, incumbent_info = solve_incumbent_master(
            problem, e1_tasks, e2_tasks_by_sat, columns, vehicles,
            satellite_lbs, args.incumbent_time_limit, args.verbose_gurobi,
        )
        if incumbent is None:
            print("ERROR: no feasible incumbent found.")
            sys.exit(2)

    # 4-6. certified exact decomposition
    print(f"\n=== Certified exact decomposition ===")
    resume_arcs = None
    resume_plans = None
    if args.resume_frontier_json and Path(args.resume_frontier_json).exists():
        raw = json.loads(
            Path(args.resume_frontier_json).read_text(encoding="utf-8"))
        if raw and isinstance(raw[0], dict):
            resume_plans = [
                {"cost": p.get("cost"), "t_del": p.get("t_del"),
                 "t_pick": p.get("t_pick"),
                 "arcs": [tuple(a) for a in p["arcs"]]}
                for p in raw
            ]
            resume_arcs = [p["arcs"] for p in resume_plans]
        else:
            resume_arcs = [[tuple(a) for a in arc_set] for arc_set in raw]
            # legacy arc-only format: rebuild full plans so the resumed
            # frontier participates in the Pareto closure analysis
            resume_plans = [
                rebuild_e1_plan(problem, e1_tasks, arc_set)
                for arc_set in resume_arcs
            ]
        print(f"Resuming E1 frontier from {len(resume_arcs)} enumerated plans")
    result = solve_m3_exact_certificate(
        problem, e1_tasks, e2_tasks_by_sat, vehicles,
        incumbent=incumbent,
        satellite_lbs=satellite_lbs,
        columns=columns,
        max_frontier_plans=args.max_frontier_plans,
        frontier_solve_time_limit=args.frontier_solve_time_limit,
        floor_cert_max_iter=args.floor_cert_max_iter,
        floor_cert_time_limit=args.floor_cert_time_limit,
        floor_cert_pool_solutions=args.floor_cert_pool_solutions,
        total_time_limit=args.total_time_limit,
        resume_frontier_arcs=resume_arcs,
        resume_frontier_plans=resume_plans,
        frontier_known_complete=(
            (out_dir / "frontier_complete.flag").exists()
            and resume_plans is not None
        ),
        e1_mip_gap=args.e1_mip_gap,
        output_flag=args.verbose_gurobi,
        verbose=True,
    )
    result["scale"] = args.scale
    result["instance"] = args.instance
    result["incumbent_info"] = incumbent_info
    result["total_elapsed"] = time.time() - run_started

    print(f"\n{'=' * 62}")
    print(f"Verdict: {result['status']}")
    print(f"proven_optimal: {result.get('proven_optimal')}")
    print(f"incumbent: {result.get('incumbent')}")
    print(f"e2_lb: {result.get('e2_lb')}")
    print(f"c0 (E1 threshold): {result.get('c0')}")
    frontier = result.get("frontier", {})
    if frontier:
        print(f"E1 plans below C0: {frontier.get('plans_count')}  "
              f"pareto classes: {frontier.get('pareto_classes')}  "
              f"complete: {frontier.get('complete')}")
    for c in result.get("per_class", []):
        print(f"  class {c['index']}: e1={c['e1_cost']:.4f} "
              f"total_lb={c['total_lb']:.4f} closed={c['closed']}")
    print(f"elapsed: {result.get('elapsed', 0.0):.0f}s "
          f"(total {result.get('total_elapsed', 0.0):.0f}s)")

    out_path = out_dir / "verdict.json"
    out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"\nOutput: {out_path}")
    all_plans = result.get("all_plans") or result.get("all_arcs") or []
    if all_plans:
        merged = []
        seen = set()
        for item in (resume_arcs or []) + all_plans:
            if isinstance(item, dict):
                key = tuple(tuple(a) for a in item["arcs"])
                plan_out = {
                    "cost": item["cost"],
                    "t_del": item["t_del"],
                    "t_pick": item["t_pick"],
                    "arcs": [list(a) for a in item["arcs"]],
                }
            else:
                key = tuple(tuple(a) for a in item)
                plan_out = [list(a) for a in item]
            if key in seen:
                continue
            seen.add(key)
            merged.append(plan_out)
        frontier_path = out_dir / "frontier_plans.json"
        frontier_path.write_text(
            json.dumps(merged, indent=1), encoding="utf-8")
        print(f"Saved {len(merged)} enumerated E1 plans (for resume): "
              f"{frontier_path}")
        if result.get("frontier", {}).get("complete"):
            (out_dir / "frontier_complete.flag").write_text("1")
            print("Frontier marked COMPLETE (resume will skip re-verification)")


if __name__ == "__main__":
    main()
