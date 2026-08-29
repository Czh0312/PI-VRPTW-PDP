# -*- coding: utf-8 -*-
"""Exact M3/M5/M6 experiment under progressively tighter E2 fleets.

The experiment keeps geometry, time windows and objective unchanged.  It
changes only E2 vehicle count/container capacity, and skips a profile-instance
pair when its aggregate pickup volume already exceeds the fleet's total
capacity (transshipment cannot repair aggregate infeasibility).
"""

import argparse
import csv
import json
import math
import sys
import time
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GUROBI_PY = Path(r"C:\gurobi\win64\python311\lib")
if GUROBI_PY.exists() and str(GUROBI_PY) not in sys.path:
    sys.path.append(str(GUROBI_PY))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.routing.common.data_loader import parse_dataset
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.task_builder import build_tasks_for_model
from models.routing.variants.model_3.exact_solver import solve_exact as solve_m3
from models.routing.variants.model_5.exact_solver import solve_exact as solve_m5
from models.routing.variants.model_6.exact_solver import solve_exact as solve_m6


PROFILES = {
    "baseline_4x5": "config/routing/validation/zhou_small.json",
    "vehicle_tight_3x4": (
        "config/routing/experiments/zhou_small_relay_vehicle_tight.json"
    ),
    "capacity_tight_4x3": (
        "config/routing/experiments/zhou_small_relay_capacity_tight.json"
    ),
    "extreme_capacity_4x2": (
        "config/routing/experiments/zhou_small_relay_extreme_capacity.json"
    ),
}


def _safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    return value


def _problem(dataset, fleet):
    nodes, _ = parse_dataset(str(dataset))
    problem = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    return problem


def _pressure(problem):
    pickup_by_sat = Counter()
    for customer in problem.customers:
        if customer.assigned_sat:
            pickup_by_sat[customer.assigned_sat] += (
                max(0.0, customer.container_count_pick)
                * problem.fleet.container_volume
            )
    fleet_capacity = (
        problem.fleet.e2_per_sat_count * problem.e2_volume_cap
    )
    utilization = {
        satellite.id: (
            pickup_by_sat[satellite.id] / fleet_capacity
            if fleet_capacity > 0 else math.inf
        )
        for satellite in problem.satellites
    }
    return pickup_by_sat, fleet_capacity, utilization


def _record(profile, dataset, model, problem, info, wall_time, m3_objective):
    objective = info.get("objective")
    saving = None
    saving_pct = None
    if objective is not None and m3_objective is not None:
        saving = float(m3_objective) - float(objective)
        if abs(float(m3_objective)) > 1e-12:
            saving_pct = 100.0 * saving / float(m3_objective)
    pickup_by_sat, fleet_capacity, utilization = _pressure(problem)
    return _safe({
        "profile": profile,
        "instance": dataset.stem,
        "model": model,
        "status": info.get("status"),
        "proven_optimal": bool(info.get("proven_optimal")),
        "solution_type": info.get("solution_type"),
        "objective": objective,
        "m3_objective": m3_objective,
        "saving_vs_m3": saving,
        "saving_pct_vs_m3": saving_pct,
        "best_bound": info.get("best_bound"),
        "mip_gap": info.get("mip_gap"),
        "runtime": info.get("runtime"),
        "wall_time": wall_time,
        "transshipment_count": info.get("transshipment_count", 0),
        "transferred_container_count": info.get(
            "transferred_container_count", 0
        ),
        "formulation": info.get("formulation"),
        "proof_scope": info.get("proof_scope"),
        "e2_vehicles_per_satellite": problem.fleet.e2_per_sat_count,
        "e2_container_capacity": problem.e2_volume_cap,
        "aggregate_capacity_per_satellite": fleet_capacity,
        "pickup_volume_by_satellite": dict(pickup_by_sat),
        "capacity_utilization_by_satellite": utilization,
        "max_capacity_utilization": max(utilization.values(), default=0.0),
    })


def _solve(model, problem, tasks, time_limit, warm_start):
    e1_tasks, e2_tasks = tasks
    common = dict(
        problem=problem,
        e1_tasks=e1_tasks,
        e2_tasks_by_sat=e2_tasks,
        vehicles_per_satellite=problem.fleet.e2_per_sat_count,
        time_limit=time_limit,
        mip_gap=0.0,
        verbose=False,
        return_info=True,
        return_incumbent_on_time_limit=True,
        warm_start_routes=warm_start,
        gurobi_params={"Seed": 0},
    )
    if model == 3:
        return solve_m3(TFp=9999.0, **common)
    if model == 5:
        return solve_m5(**common)
    if model == 6:
        return solve_m6(TFp=9999.0, **common)
    raise ValueError(model)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profiles", nargs="+", choices=tuple(PROFILES),
        default=list(PROFILES),
    )
    parser.add_argument(
        "--instances", nargs="+",
        default=["Set1_7-2-3b", "Set1_10-2-3", "Set1_12-2-2"],
    )
    parser.add_argument("--scenario", default="standard_same_day")
    parser.add_argument("--models", nargs="+", type=int, choices=(3, 5, 6),
                        default=[3, 6])
    parser.add_argument("--time-limit", type=float, default=90.0)
    parser.add_argument(
        "--output-dir",
        default="output/results/relay_fleet_stress_exact",
    )
    args = parser.parse_args()

    output_dir = ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    jsonl_path = output_dir / f"relay_fleet_stress_{stamp}.jsonl"
    csv_path = output_dir / f"relay_fleet_stress_{stamp}.csv"
    records = []

    dataset_dir = (
        ROOT / "data" / "zhou_later_pickup" / "small" / args.scenario
    )
    for profile in args.profiles:
        fleet = json.loads(
            (ROOT / PROFILES[profile]).read_text(encoding="utf-8")
        )
        for instance in args.instances:
            dataset = dataset_dir / f"{instance}.csv"
            problem = _problem(dataset, fleet)
            pickup_by_sat, fleet_capacity, utilization = _pressure(problem)
            if any(volume > fleet_capacity + 1e-9
                   for volume in pickup_by_sat.values()):
                record = _safe({
                    "profile": profile,
                    "instance": instance,
                    "model": None,
                    "status": "SKIPPED_AGGREGATE_INFEASIBLE",
                    "proven_optimal": False,
                    "e2_vehicles_per_satellite": (
                        problem.fleet.e2_per_sat_count
                    ),
                    "e2_container_capacity": problem.e2_volume_cap,
                    "aggregate_capacity_per_satellite": fleet_capacity,
                    "pickup_volume_by_satellite": dict(pickup_by_sat),
                    "capacity_utilization_by_satellite": utilization,
                    "max_capacity_utilization": max(
                        utilization.values(), default=0.0
                    ),
                })
                records.append(record)
                print(json.dumps(record, ensure_ascii=False), flush=True)
                with jsonl_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                continue

            tasks = build_tasks_for_model(problem, 3)
            m3_routes = None
            m3_objective = None
            ordered_models = [3] + [m for m in args.models if m != 3]
            for model in ordered_models:
                if model not in args.models and model != 3:
                    continue
                started = time.perf_counter()
                try:
                    e1_routes, e2_routes, info = _solve(
                        model, problem, tasks, args.time_limit,
                        m3_routes if model in (5, 6) else None,
                    )
                    wall_time = time.perf_counter() - started
                    if model == 3:
                        m3_routes = (
                            (e1_routes, e2_routes)
                            if e1_routes is not None else None
                        )
                        m3_objective = info.get("objective")
                    record = _record(
                        profile, dataset, model, problem, info, wall_time,
                        m3_objective,
                    )
                except Exception as exc:
                    record = {
                        "profile": profile,
                        "instance": instance,
                        "model": model,
                        "status": "SCRIPT_ERROR",
                        "proven_optimal": False,
                        "wall_time": time.perf_counter() - started,
                        "error": repr(exc),
                    }
                records.append(record)
                print(json.dumps(record, ensure_ascii=False), flush=True)
                with jsonl_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    fields = sorted({key for row in records for key in row})
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
    print(f"Wrote {jsonl_path}")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
