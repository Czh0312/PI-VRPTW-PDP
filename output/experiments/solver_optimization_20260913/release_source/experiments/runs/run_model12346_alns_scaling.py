# -*- coding: utf-8 -*-
"""Unified ALNS benchmark for Models 1, 2, 3, 4, 6 and 7.

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
from models.routing.alns.variants.model_7 import solve_alns as solve_m7
from models.routing.common.data_loader import parse_dataset
from models.routing.common.task_builder import build_tasks_for_model
from models.routing.alns.types import Route, Solution


MODELS = (1, 2, 3, 4, 6, 7)
FIXED_ASSIGNMENT_MODELS = (1, 3, 6)
INHERITANCE_BASE = {2: 1, 4: 3, 6: 3, 7: 4}


def load_problem(path, fleet):
    nodes, _ = parse_dataset(str(path))
    problem = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    return problem


def as_solution(e1_paths, e2_paths):
    """Convert public path output back to the internal ALNS solution type."""
    sol = Solution()
    sol.e1_routes = [Route(path[0], list(path[1:-1])) for path in e1_paths]
    sol.e2_routes = {
        sat: [Route(path[0], list(path[1:-1])) for path in paths]
        for sat, paths in e2_paths.items()
    }
    return sol


def fixed_assignment_early_due_conflicts(problem, tolerance=1e-9):
    """Return proof-safe direct-travel conflicts for fixed assignments.

    Every fixed-assignment E2 delivery must first travel from the depot to its
    assigned satellite and then from that satellite to the customer.  If even
    this service-free direct lower bound misses the delivery due time, no route
    construction or ALNS seed can make that fixed assignment feasible.
    """
    satellite_by_id = {sat.id: sat for sat in problem.satellites}
    depot_id = problem.depot.id
    speed = float(problem.speed)
    conflicts = []
    for customer in problem.customers:
        sat_id = customer.assigned_sat
        satellite = satellite_by_id.get(sat_id)
        if satellite is None:
            continue
        due = float(customer.tw_del[1])
        if not math.isfinite(due):
            continue
        earliest = (
            problem.distance(depot_id, sat_id)
            + problem.distance(sat_id, customer.id)
        ) / speed
        if earliest > due + tolerance:
            conflicts.append({
                "customer": customer.id,
                "assigned_satellite": sat_id,
                "earliest_direct_delivery": float(earliest),
                "delivery_due": due,
                "lateness_lower_bound": float(earliest - due),
            })
    return conflicts


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
    exact_dir = ROOT / "output" / "results" / "mainline_small_exact"
    canonical_path = exact_dir / "small_exact_final.jsonl"
    legacy_path = exact_dir / "small_exact_20260825_123124.jsonl"
    base_path = canonical_path if canonical_path.exists() else legacy_path
    if base_path.exists():
        for line in base_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            result[(row["instance"], int(row["model"]))] = row.get("objective")
    # Older exact batches did not contain M6.  Keep the legacy supplement only
    # when the canonical/current mainline file has no M6 rows.
    m6_path = (
        ROOT / "output" / "results" / "model6_standard_small_exact_unlimited"
        / "small_exact_20260826_171404.jsonl"
    )
    if not any(model == 6 for _, model in result) and m6_path.exists():
        for line in m6_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            result[(row["instance"], 6)] = row.get("objective")
    return result


def solve_one(model, problem, *, max_time, iterations, seed,
              target_objective=None, target_tolerance=1e-6,
              alns_config_overrides=None, initial_solution=None):
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
            problem, vehicles_per_satellite=vps,
            initial_solution=initial_solution, **common
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
            problem, vps, TFp=9999.0,
            initial_solution=initial_solution, **common
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
            initial_solution=initial_solution,
            **common,
        )
        assignment = None
    elif model == 7:
        stage2_budget = min(2.5, max(0.0, 0.25 * max_time))
        r1, r2, assignment, info = solve_m7(
            problem,
            vehicles_per_satellite=vps,
            TFp=9999.0,
            stage1_time_ratio=0.75,
            transshipment_time_budget=stage2_budget,
            transshipment_sample_pickups=12,
            transshipment_sample_locations=6,
            transshipment_sample_carriers=4,
            transshipment_position_top_k=4,
            initial_solution=initial_solution,
            **common,
        )
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
    parser.add_argument(
        "--inherit-incumbents", action="store_true",
        help=(
            "Pass M1->M2, M3->M4/M6 and M4->M7 incumbents when their base "
            "models were run earlier for the same instance."
        ),
    )
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
        fixed_conflicts = fixed_assignment_early_due_conflicts(problem)
        solution_cache = {}
        for model in (tuple(args.models) if args.models else MODELS):
            started = time.perf_counter()
            inherited_from = (
                INHERITANCE_BASE.get(model) if args.inherit_incumbents else None
            )
            initial_solution = solution_cache.get(inherited_from)
            if initial_solution is None:
                inherited_from = None
            try:
                if model in FIXED_ASSIGNMENT_MODELS and fixed_conflicts:
                    e1_routes = e2_routes = assignment = None
                    info = {
                        "objective": None,
                        "runtime": 0.0,
                        "iterations": 0,
                        "feasible": False,
                        "status": "INFEASIBLE_FIXED_ASSIGNMENT",
                        "failure_reason": (
                            "earliest_direct_delivery_exceeds_due_time"
                        ),
                        "failure_details": fixed_conflicts,
                    }
                else:
                    e1_routes, e2_routes, assignment, info = solve_one(
                        model, problem, max_time=args.time,
                        iterations=args.iterations, seed=args.seed,
                        initial_solution=initial_solution,
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
                    if status != "INFEASIBLE_FIXED_ASSIGNMENT":
                        status = "NO_FEASIBLE_SOLUTION"
                elif model in (1, 3, 4):
                    solution_cache[model] = as_solution(
                        e1_routes, e2_routes)
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
                    "stage1_objective": info.get(
                        "stage1_objective",
                        info.get("free_assignment_objective"),
                    ),
                    "transshipment_improvement": info.get(
                        "transshipment_improvement", 0.0
                    ),
                    "assignment": assignment,
                    "moved_customers": info.get("moved_customers"),
                    "seed": args.seed,
                    "time_budget": args.time,
                    "iteration_limit": args.iterations,
                    "e1_vehicle_count": problem.e1_vehicle_count,
                    "e2_vehicles_per_satellite": (
                        problem.fleet.e2_per_sat_count),
                    "fleet_config": fleet_path.name,
                    "protocol": (
                        "incumbent_inheritance"
                        if args.inherit_incumbents else "independent"
                    ),
                    "inherited_from_model": inherited_from,
                    "initial_objective": info.get("initial_objective"),
                    "phase_diagnostics": info.get("phase_diagnostics"),
                    "refill_iterations": info.get("refill_iterations"),
                    "transshipment_search_elapsed_seconds": info.get(
                        "transshipment_search_elapsed_seconds"),
                    "failure_reason": info.get("failure_reason"),
                    "failure_details": info.get("failure_details"),
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
                    "protocol": (
                        "incumbent_inheritance"
                        if args.inherit_incumbents else "independent"
                    ),
                    "inherited_from_model": inherited_from,
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

    if args.inherit_incumbents:
        by_instance = {}
        for row in rows:
            by_instance.setdefault(row["instance"], {})[row["model"]] = row
        violations = []
        for instance, model_rows in by_instance.items():
            for model, base_model in INHERITANCE_BASE.items():
                ext = model_rows.get(model)
                base = model_rows.get(base_model)
                if not ext or not base or not ext.get("feasible") or not base.get("feasible"):
                    continue
                if float(ext["objective"]) > float(base["objective"]) + 1e-7:
                    violations.append((instance, model, base_model))
        print(
            "Inherited-incumbent dominance: "
            f"{'passed' if not violations else 'FAILED'}; "
            f"violations={violations}"
        )
        if violations:
            sys.exit(2)


if __name__ == "__main__":
    main()
