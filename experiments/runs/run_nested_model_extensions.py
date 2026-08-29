# -*- coding: utf-8 -*-
"""Nested ALNS experiment for incumbent-preserving model extensions.

The paired extensions are M2<-M1, M4/M6<-M3, and M7<-M4.  Each
extension receives the feasible incumbent produced by its base model, so the
experiment measures the value of the added mechanism instead of conflating it
with independent-search noise.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.runs.run_model12346_alns_scaling import load_problem
from models.routing.alns.types import Route, Solution
from models.routing.alns.variants.model_1 import solve_alns as solve_m1
from models.routing.alns.variants.model_2 import solve_alns as solve_m2
from models.routing.alns.variants.model_3 import solve_alns as solve_m3
from models.routing.alns.variants.model_4 import solve_alns as solve_m4
from models.routing.alns.variants.model_6 import solve_alns as solve_m6
from models.routing.alns.variants.model_7 import solve_alns as solve_m7
from models.routing.common.task_builder import build_tasks_for_model


def as_solution(e1_paths, e2_paths):
    sol = Solution()
    sol.e1_routes = [Route(path[0], list(path[1:-1])) for path in e1_paths]
    sol.e2_routes = {
        sat: [Route(path[0], list(path[1:-1])) for path in paths]
        for sat, paths in e2_paths.items()
    }
    return sol


def extension_row(instance, customers, model, base_model, base_info,
                  ext_info, assignment, seed, base_time, extension_time,
                  inherited_objective=None):
    base_solver_obj = float(base_info["objective"])
    base_obj = (base_solver_obj if inherited_objective is None
                else float(inherited_objective))
    obj = float(ext_info["objective"])
    gain = base_obj - obj
    return {
        "instance": instance,
        "customers": customers,
        "model": model,
        "base_model": base_model,
        "base_solver_objective": base_solver_obj,
        "base_objective": base_obj,
        "objective": obj,
        "incremental_gain": gain,
        "incremental_gain_pct": gain / base_obj * 100.0,
        "dominance_preserved": obj <= base_obj + 1e-7,
        "feasible": bool(ext_info["feasible"]),
        "base_runtime": float(base_info["runtime"]),
        "extension_runtime": float(ext_info["runtime"]),
        "total_runtime": float(base_info["runtime"] + ext_info["runtime"]),
        "base_iterations": int(base_info["iterations"]),
        "extension_iterations": int(ext_info["iterations"]),
        "transshipment_count": int(ext_info.get("transshipment_count", 0)),
        "transshipment_improvement": float(
            ext_info.get("transshipment_improvement", 0.0)),
        "assignment": assignment,
        "seed": seed,
        "base_time_budget": base_time,
        "extension_time_budget": extension_time,
        "fleet_config": "config/routing/validation/zhou_large.json",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-time", type=float, default=10.0)
    parser.add_argument("--extension-time", type=float, default=2.0)
    parser.add_argument("--iterations", type=int, default=1_000_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--instances", nargs="*")
    parser.add_argument("--extensions", nargs="*", type=int,
                        choices=(2, 4, 6, 7), default=(2, 4, 6, 7))
    parser.add_argument(
        "--output-dir", default="output/results/model123467_nested_extensions")
    args = parser.parse_args()

    fleet = json.loads(
        (ROOT / "config" / "routing" / "validation" / "zhou_large.json").read_text(
            encoding="utf-8"))
    data_dir = ROOT / "data" / "zhou_later_pickup" / "large" / "standard_same_day"
    paths = sorted(data_dir.glob("*.csv"))
    # This fixed-assignment instance is structurally infeasible (C93 due 75).
    paths = [p for p in paths if p.stem != "Set2_200-10-3"]
    if args.instances:
        wanted = {v if v.endswith(".csv") else f"{v}.csv"
                  for v in args.instances}
        paths = [p for p in paths if p.name in wanted]

    output_dir = ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    stem = (f"nested_large_base{args.base_time:g}_ext{args.extension_time:g}"
            f"_seed{args.seed}_{stamp}")
    jsonl_path = output_dir / f"{stem}.jsonl"
    csv_path = output_dir / f"{stem}.csv"
    rows = []

    for path in paths:
        problem = load_problem(path, fleet)
        vps = problem.fleet.e2_per_sat_count
        common_base = dict(
            max_iterations=args.iterations, max_time=args.base_time,
            seed=args.seed, verbose=False, return_info=True)
        common_ext = dict(
            max_iterations=args.iterations, max_time=args.extension_time,
            seed=args.seed, verbose=False, return_info=True)

        before = len(rows)
        if 2 in args.extensions:
            e1_m1, e2_m1 = build_tasks_for_model(problem, 1)
            m1_e1, m1_e2, m1_info = solve_m1(
                problem, e1_m1, e2_m1, vps,
                strong_initial_solution=True, **common_base)
            if not m1_info["feasible"]:
                raise RuntimeError(f"M1 base infeasible: {path.stem}")
            m1_sol = as_solution(m1_e1, m1_e2)
            _, _, m2_assignment, m2_info = solve_m2(
                problem, vehicles_per_satellite=vps,
                initial_solution=m1_sol, **common_ext)
            if not m2_info["feasible"]:
                raise RuntimeError(f"M2 extension infeasible: {path.stem}")
            rows.append(extension_row(
                path.stem, len(problem.customers), 2, 1, m1_info, m2_info,
                m2_assignment, args.seed, args.base_time,
                args.extension_time,
                inherited_objective=m2_info.get("initial_objective")))

        if 4 in args.extensions or 6 in args.extensions:
            e1_m3, e2_m3 = build_tasks_for_model(problem, 3)
            m3_e1, m3_e2, m3_info = solve_m3(
                problem, e1_m3, e2_m3, vps, TFp=9999.0,
                strong_initial_solution=True, **common_base)
            if not m3_info["feasible"]:
                raise RuntimeError(f"M3 base infeasible: {path.stem}")
            m3_sol = as_solution(m3_e1, m3_e2)

        if 4 in args.extensions:
            _, _, m4_assignment, m4_info = solve_m4(
                problem, vps, TFp=9999.0,
                initial_solution=m3_sol, **common_ext)
            if not m4_info["feasible"]:
                raise RuntimeError(f"M4 extension infeasible: {path.stem}")
            rows.append(extension_row(
                path.stem, len(problem.customers), 4, 3, m3_info, m4_info,
                m4_assignment, args.seed, args.base_time,
                args.extension_time,
                inherited_objective=m4_info.get("initial_objective")))

        if 6 in args.extensions:
            _, _, m6_info = solve_m6(
                problem, e1_m3, e2_m3, vps, TFp=9999.0,
                initial_solution=m3_sol,
                stage1_time_ratio=0.0,
                refill_unused_time=False,
                transshipment_time_budget=args.extension_time,
                transshipment_sample_pickups=16,
                transshipment_sample_locations=8,
                transshipment_sample_carriers=6,
                transshipment_position_top_k=5,
                **common_ext)
            if not m6_info["feasible"]:
                raise RuntimeError(f"M6 extension infeasible: {path.stem}")
            rows.append(extension_row(
                path.stem, len(problem.customers), 6, 3, m3_info, m6_info,
                None, args.seed, args.base_time, args.extension_time,
                inherited_objective=m6_info["stage1_objective"]))

        if 7 in args.extensions:
            # M7 adds asynchronous relay to M4's free-assignment solution.
            # Run M4 for the full base budget, then reserve the complete
            # extension budget for relay.  With stage1_time_ratio=0, M7 keeps
            # the inherited M4 incumbent and cannot lose its objective value.
            m4_e1, m4_e2, _, m4_base_info = solve_m4(
                problem, vps, TFp=9999.0, **common_base)
            if not m4_base_info["feasible"]:
                raise RuntimeError(f"M4 base infeasible: {path.stem}")
            m4_sol = as_solution(m4_e1, m4_e2)
            _, _, m7_assignment, m7_info = solve_m7(
                problem,
                vehicles_per_satellite=vps,
                TFp=9999.0,
                initial_solution=m4_sol,
                stage1_time_ratio=0.0,
                transshipment_time_budget=args.extension_time,
                transshipment_sample_pickups=16,
                transshipment_sample_locations=8,
                transshipment_sample_carriers=6,
                transshipment_position_top_k=5,
                **common_ext,
            )
            if not m7_info["feasible"]:
                raise RuntimeError(f"M7 extension infeasible: {path.stem}")
            rows.append(extension_row(
                path.stem, len(problem.customers), 7, 4,
                m4_base_info, m7_info, m7_assignment, args.seed,
                args.base_time, args.extension_time,
                inherited_objective=m7_info["free_assignment_objective"]))

        with jsonl_path.open("a", encoding="utf-8") as handle:
            for row in rows[before:]:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(json.dumps(row, ensure_ascii=False), flush=True)

    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {jsonl_path}")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
