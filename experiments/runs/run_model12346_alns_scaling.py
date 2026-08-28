# -*- coding: utf-8 -*-
"""Unified ALNS benchmark for Models 1, 2, 3, 4 and 6.

The five models share one wall-time budget, iteration ceiling and seed.  Every
returned incumbent is checked through the model's own Evaluator before it is
written.  Small-instance rows also carry the corresponding certified exact
objective and heuristic gap.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.routing.common.domain.instance import ProblemInstance
from models.routing.alns.variants.model_1 import solve_alns as solve_m1
from models.routing.alns.variants.model_2 import solve_alns as solve_m2
from models.routing.alns.variants.model_3 import solve_alns as solve_m3
from models.routing.alns.variants.model_4 import solve_alns as solve_m4
from models.routing.alns.variants.model_6 import solve_alns as solve_m6
from models.routing.common.data_loader import parse_dataset
from models.routing.common.task_builder import build_tasks_for_model


MODELS = (1, 2, 3, 4, 6)


def load_problem(path, fleet):
    nodes, _ = parse_dataset(str(path))
    problem = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    return problem


def physical(node_id):
    if node_id.startswith("MEET@"):
        node_id = node_id.split("@", 1)[1]
    if node_id.startswith(("TRANS_PICK_", "TRANS_DROP_")):
        payload = node_id.split("_", 2)[-1]
        return payload.rsplit("@", 1)[-1]
    if node_id.endswith(("d", "p")) and not node_id.startswith("TRANS"):
        return node_id[:-1]
    return node_id


def route_cost(problem, e1_routes, e2_routes):
    if e1_routes is None or e2_routes is None:
        return None
    routes = list(e1_routes)
    for sat_routes in e2_routes.values():
        routes.extend(sat_routes)
    return sum(
        problem.distance(physical(left), physical(right))
        for route in routes
        for left, right in zip(route, route[1:])
    )


def exact_references():
    result = {}
    base_path = (
        ROOT / "output" / "results" / "mainline_small_exact"
        / "small_exact_20260825_123124.jsonl"
    )
    if base_path.exists():
        for line in base_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            result[(row["instance"], int(row["model"]))] = row.get("objective")
    m6_path = (
        ROOT / "output" / "results" / "model6_standard_small_exact_unlimited"
        / "small_exact_20260826_171404.jsonl"
    )
    if m6_path.exists():
        for line in m6_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            result[(row["instance"], 6)] = row.get("objective")
    return result


def solve_one(model, problem, *, max_time, iterations, seed,
              target_objective=None, target_tolerance=1e-6,
              alns_config_overrides=None):
    vps = problem.fleet.e2_per_sat_count
    common = {
        "max_iterations": iterations,
        "max_time": max_time,
        "seed": seed,
        "verbose": False,
        "return_info": True,
        "target_objective": target_objective,
        "target_tolerance": target_tolerance,
        "alns_config_overrides": alns_config_overrides,
    }
    if model == 1:
        e1, e2 = build_tasks_for_model(problem, 1)
        r1, r2, info = solve_m1(
            problem, e1, e2, vps, strong_initial_solution=True, **common)
        assignment = None
    elif model == 2:
        r1, r2, assignment, info = solve_m2(
            problem, vehicles_per_satellite=vps, **common
        )
    elif model == 3:
        e1, e2 = build_tasks_for_model(problem, 3)
        r1, r2, info = solve_m3(
            problem, e1, e2, vps, TFp=9999.0,
            strong_initial_solution=True, **common,
        )
        assignment = None
    elif model == 4:
        r1, r2, assignment, info = solve_m4(
            problem, vps, TFp=9999.0, **common
        )
    elif model == 6:
        e1, e2 = build_tasks_for_model(problem, 6)
        stage2_budget = min(2.0, max(0.0, 0.2 * max_time))
        r1, r2, info = solve_m6(
            problem, e1, e2, vps, TFp=9999.0,
            strong_initial_solution=True,
            stage1_time_ratio=0.8,
            transshipment_time_budget=stage2_budget,
            transshipment_sample_pickups=12,
            transshipment_sample_locations=6,
            transshipment_sample_carriers=4,
            transshipment_position_top_k=4,
            **common,
        )
        assignment = None
    else:
        raise ValueError(model)
    return r1, r2, assignment, info


def safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", choices=["small", "large"], required=True)
    parser.add_argument("--scenario", default="standard_same_day")
    parser.add_argument("--time", type=float, required=True)
    parser.add_argument("--iterations", type=int, default=1_000_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--instances", nargs="*")
    parser.add_argument("--models", nargs="*", type=int, choices=MODELS)
    parser.add_argument("--fleet-config")
    parser.add_argument(
        "--output-dir", default="output/results/model12346_alns_scaling"
    )
    args = parser.parse_args(argv)
    if args.time <= 0 or args.iterations <= 0:
        parser.error("--time and --iterations must be positive")

    fleet_path = Path(args.fleet_config) if args.fleet_config else (
        ROOT / "config" / "routing" / "validation" /
        ("zhou_large.json" if args.scale == "large" else "zhou_small.json")
    )
    if not fleet_path.is_absolute():
        fleet_path = ROOT / fleet_path
    fleet = json.loads(fleet_path.read_text(encoding="utf-8"))
    data_dir = (
        ROOT / "data" / "zhou_later_pickup" / args.scale / args.scenario
    )
    paths = sorted(data_dir.glob("*.csv"))
    if args.instances:
        wanted = {name if name.endswith(".csv") else f"{name}.csv"
                  for name in args.instances}
        paths = [path for path in paths if path.name in wanted]

    exact = exact_references() if args.scale == "small" else {}
    out_dir = ROOT / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    stem = f"{args.scale}_{args.scenario}_t{args.time:g}_seed{args.seed}_{stamp}"
    jsonl_path = out_dir / f"{stem}.jsonl"
    csv_path = out_dir / f"{stem}.csv"
    rows = []

    for path in paths:
        problem = load_problem(path, fleet)
        for model in (tuple(args.models) if args.models else MODELS):
            started = time.perf_counter()
            try:
                e1_routes, e2_routes, assignment, info = solve_one(
                    model, problem, max_time=args.time,
                    iterations=args.iterations, seed=args.seed,
                )
                wall_time = time.perf_counter() - started
                audited_cost = route_cost(problem, e1_routes, e2_routes)
                objective = info.get("objective")
                objective_delta = (
                    None if objective is None or audited_cost is None
                    else audited_cost - objective
                )
                feasible = bool(info.get("feasible"))
                status = info.get("status", "UNKNOWN")
                if not feasible:
                    objective = None
                    status = "NO_FEASIBLE_SOLUTION"
                reference = exact.get((path.stem, model))
                gap_pct = (
                    None if objective is None or reference is None
                    else (objective - reference) / reference * 100.0
                )
                row = {
                    "scale": args.scale,
                    "scenario": args.scenario,
                    "instance": path.stem,
                    "customers": len(problem.customers),
                    "satellites": len(problem.satellites),
                    "model": model,
                    "status": status,
                    "feasible": feasible,
                    "objective": safe(objective),
                    "exact_objective": safe(reference),
                    "gap_to_exact_pct": safe(gap_pct),
                    "runtime": safe(info.get("runtime")),
                    "wall_time": wall_time,
                    "iterations": info.get("iterations"),
                    "route_cost_audit": safe(audited_cost),
                    "route_cost_delta": safe(objective_delta),
                    "transshipment_count": info.get("transshipment_count", 0),
                    "stage1_objective": info.get("stage1_objective"),
                    "transshipment_improvement": info.get(
                        "transshipment_improvement", 0.0
                    ),
                    "assignment": assignment,
                    "seed": args.seed,
                    "time_budget": args.time,
                    "iteration_limit": args.iterations,
                    "e1_vehicle_count": problem.e1_vehicle_count,
                    "e2_vehicles_per_satellite": (
                        problem.fleet.e2_per_sat_count),
                    "fleet_config": fleet_path.name,
                }
            except Exception as exc:
                row = {
                    "scale": args.scale, "scenario": args.scenario,
                    "instance": path.stem,
                    "customers": len(problem.customers),
                    "satellites": len(problem.satellites),
                    "model": model, "status": "SCRIPT_ERROR",
                    "feasible": False, "objective": None,
                    "exact_objective": exact.get((path.stem, model)),
                    "gap_to_exact_pct": None, "runtime": None,
                    "wall_time": time.perf_counter() - started,
                    "iterations": None, "route_cost_audit": None,
                    "route_cost_delta": None, "transshipment_count": 0,
                    "stage1_objective": None,
                    "transshipment_improvement": 0.0,
                    "assignment": None, "seed": args.seed,
                    "time_budget": args.time,
                    "iteration_limit": args.iterations,
                    "e1_vehicle_count": problem.e1_vehicle_count,
                    "e2_vehicles_per_satellite": (
                        problem.fleet.e2_per_sat_count),
                    "fleet_config": fleet_path.name,
                    "error": repr(exc),
                }
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            with jsonl_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {jsonl_path}")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
