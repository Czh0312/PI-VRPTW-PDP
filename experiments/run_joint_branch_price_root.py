#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the Model 2 joint branch-price root on a problem instance.

Gate 0 (pre-flight): --lp-ceiling-only
  One-shot LP solve over the loaded column pool — measures the ceiling that
  column generation can reach. This is the UPPER BOUND on what CG can prove.

Gate 1 (smoke test): default mode on a small instance (e.g. 15_customers)
  Full CG loop with joint pricing. Verifies dual extraction and pricing.

Gate 2 (production): default mode on 100_customers/C101 with the merged 16,336-
  column pool. Results include the link_dual_max_abs diagnostic.

Examples
--------
# Pre-flight: measure LP ceiling
.venv/bin/python experiments/run_joint_branch_price_root.py \\
    --scale 100_customers --instance C101 \\
    --columns-json reference/exact_proof_evidence/merged_columns/100_customers__C101__columns.json \\
    --satellite-lb-json reference/exact_proof_evidence/exact_incumbents/m2_100_c101_satellite_bcp_exact_lbs.json \\
    --lp-ceiling-only

# Full CG on 15c smoke
.venv/bin/python experiments/run_joint_branch_price_root.py \\
    --scale 15_customers --instance C101 \\
    --max-cg-iterations 20 --pricing-time-limit 5 --master-time-limit 120
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
from models.model_2.joint_branch_price_root import (
    solve_joint_branch_price_root,
    solve_joint_lp_ceiling,
)
from models.model_2.e2_route_cg import solve_satellite_route_cg
from utils.data_loader import parse_dataset
from utils.task_builder import build_tasks_for_model

CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_COLUMNS_JSON = str(
    PROJECT_ROOT / "reference" / "exact_proof_evidence" / "merged_columns"
    / "100_customers__C101__columns.json"
)
DEFAULT_SAT_LB_JSON = str(
    PROJECT_ROOT / "reference" / "exact_proof_evidence" / "exact_incumbents"
    / "m2_100_c101_satellite_bcp_exact_lbs.json"
)


def load_fleet_config():
    return json.loads((CONFIG_DIR / "fleet_config.json").read_text(encoding="utf-8"))


def build_problem(data_path, fleet_cfg):
    nodes_dict, _ = parse_dataset(str(data_path))
    problem = ProblemInstance(nodes_dict, fleet_cfg, speed=fleet_cfg.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    return problem


def read_satellite_cost_lbs(path):
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
    return {str(k): float(v) for k, v in lbs.items()}


def load_columns(columns_json_path, sat_lbs):
    """Load columns from JSON. Returns list of column dicts."""
    payload = json.loads(Path(columns_json_path).read_text(encoding="utf-8"))
    columns = payload.get("columns", payload if isinstance(payload, list) else [])
    if not isinstance(columns, list):
        columns = []
    print(f"Loaded {len(columns)} columns from {columns_json_path}")
    return columns


def generate_seed_columns(problem, e2_tasks_by_sat, vehicles_per_satellite):
    """Generate seed columns via per-satellite CG when no JSON is provided."""
    print("Generating seed columns via per-satellite CG...")
    all_columns = []
    for sat_id, tasks in e2_tasks_by_sat.items():
        if not tasks:
            continue
        try:
            result = solve_satellite_route_cg(
                problem, sat_id, tasks, vehicles_per_satellite,
                max_iterations=10, pricing_time_limit=5.0,
                pricing_pool_solutions=5, pricing_method="mip",
                integer_master=False, return_columns=True,
                output_flag=False,
            )
            for col in result.get("columns_data", []):
                all_columns.append(col)
            print(f"  {sat_id}: {len(result.get('columns_data', []))} columns, "
                  f"lp={result.get('lp_obj', 'N/A')}")
        except Exception as e:
            print(f"  {sat_id}: CG failed — {e}")
    print(f"Generated {len(all_columns)} seed columns total")
    return all_columns


def write_rows(path, rows):
    import csv
    if not rows:
        return
    keys = list(rows[0].keys())
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def main():
    parser = argparse.ArgumentParser(
        description="Model 2 Joint Branch-Price Root")
    parser.add_argument("--scale", default="15_customers",
                        help="Data scale subdirectory (default: 15_customers)")
    parser.add_argument("--instance", default="C101")
    parser.add_argument("--columns-json", default=None,
                        help="Path to columns JSON (default: reference merged columns)")
    parser.add_argument("--satellite-lb-json", default=None,
                        help="Path to satellite BCP lower bounds JSON")
    parser.add_argument("--incumbent", type=float, default=None,
                        help="Known upper bound (feasible integer solution)")
    parser.add_argument("--max-cg-iterations", type=int, default=200)
    parser.add_argument("--pricing-time-limit", type=float, default=20.0)
    parser.add_argument("--master-time-limit", type=float, default=600.0)
    parser.add_argument("--no-proof-feasibility", action="store_true",
                        help="Disable proof-as-feasibility pricing mode")
    parser.add_argument("--lp-ceiling-only", action="store_true",
                        help="Only solve the LP relaxation once (Gate 0)")
    parser.add_argument("--lp-ceiling-time-limit", type=float, default=600.0)
    parser.add_argument("--output-dir", type=Path,
                        default=str(PROJECT_ROOT / "output" / "m2_joint_bcp_root"))
    parser.add_argument("--verbose-gurobi", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    fleet_cfg = load_fleet_config()
    data_path = DATA_DIR / args.scale / f"{args.instance}.csv"
    if not data_path.exists():
        print(f"ERROR: data file not found: {data_path}")
        sys.exit(1)

    problem = build_problem(data_path, fleet_cfg)
    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, 2)

    # Resolve column source
    if args.columns_json:
        columns_json = args.columns_json
    elif args.scale == "100_customers" and args.instance == "C101":
        columns_json = DEFAULT_COLUMNS_JSON
    else:
        columns_json = None

    # Resolve satellite LB source
    if args.satellite_lb_json:
        sat_lb_json = args.satellite_lb_json
    elif args.scale == "100_customers" and args.instance == "C101":
        sat_lb_json = DEFAULT_SAT_LB_JSON
    else:
        sat_lb_json = None

    sat_lbs = read_satellite_cost_lbs(sat_lb_json) if sat_lb_json else {}

    if columns_json and Path(columns_json).exists():
        columns = load_columns(columns_json, sat_lbs)
    else:
        print(f"No columns JSON provided; generating seed columns via CG.")
        columns = generate_seed_columns(
            problem, e2_tasks_by_sat, problem.fleet.e2_per_sat_count)

    # Resolve incumbent
    incumbent = args.incumbent
    if incumbent is None and args.scale == "100_customers" and args.instance == "C101":
        incumbent = 1829.0093866572092

    print(f"Instance: {args.scale}/{args.instance}")
    print(f"E1 trucks: {problem.e1_vehicle_count}, "
          f"veh/sat: {problem.fleet.e2_per_sat_count}")
    print(f"E2 tasks: {sum(len(v) for v in e2_tasks_by_sat.values())}, "
          f"satellites: {len(e2_tasks_by_sat)}")
    print(f"Columns: {len(columns)}, satellite LBs: {len(sat_lbs)}")
    if incumbent:
        print(f"Incumbent: {incumbent:.4f}")

    if args.lp_ceiling_only:
        # Gate 0: LP ceiling measurement
        print("\n=== Gate 0: LP Ceiling ===")
        log_file = out_dir / f"{args.scale}__{args.instance}__lp_ceiling.log"
        result = solve_joint_lp_ceiling(
            problem, e1_tasks, e2_tasks_by_sat,
            problem.fleet.e2_per_sat_count,
            columns,
            satellite_cost_lbs=sat_lbs or None,
            time_limit=args.lp_ceiling_time_limit,
            output_flag=args.verbose_gurobi,
        )
        print(json.dumps(result, indent=2, default=str))
        result["scale"] = args.scale
        result["instance"] = args.instance
        result["sat_lbs"] = bool(sat_lbs)
        (out_dir / f"{args.scale}__{args.instance}__lp_ceiling.json").write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8")
        return

    # Full CG
    print("\n=== Joint Branch-Price Root CG ===")
    started = time.time()
    result = solve_joint_branch_price_root(
        problem, e1_tasks, e2_tasks_by_sat,
        problem.fleet.e2_per_sat_count,
        columns,
        incumbent=incumbent,
        max_cg_iterations=args.max_cg_iterations,
        pricing_time_limit=args.pricing_time_limit,
        proof_as_feasibility=not args.no_proof_feasibility,
        master_time_limit=args.master_time_limit,
        satellite_cost_lbs=sat_lbs or None,
        output_flag=args.verbose_gurobi,
    )
    result["scale"] = args.scale
    result["instance"] = args.instance

    print(f"\nStatus: {result['status']}")
    print(f"proven_optimal: {result.get('proven_optimal', False)}")
    print(f"lp_bound: {result.get('lp_bound')}")
    print(f"incumbent: {result.get('incumbent')}")
    print(f"gap_pct: {result.get('gap_pct')}")
    print(f"iterations: {result.get('iterations')}")
    print(f"columns: {result.get('column_count')} "
          f"(+{result.get('columns_added', 0)} added)")
    print(f"link_dual_max_abs: {result.get('link_dual_max_abs')}")
    print(f"message: {result.get('message')}")
    print(f"elapsed: {result.get('elapsed', time.time() - started):.1f}s")

    # Write outputs
    base = out_dir / f"{args.scale}__{args.instance}__joint_bcp_root"
    (out_dir / f"{args.scale}__{args.instance}__joint_bcp_root.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8")
    write_rows(str(base) + ".csv", [result])
    print(f"\nOutput: {base}.json")


if __name__ == "__main__":
    main()
