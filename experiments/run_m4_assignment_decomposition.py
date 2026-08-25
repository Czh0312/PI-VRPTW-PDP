# -*- coding: utf-8 -*-
"""Exact assignment enumeration decomposition for two-satellite M4.

For a fixed customer-to-satellite assignment, M4 is exactly Model 3.  The
union over all 2^|C| assignments is therefore an exact (but exponential)
decomposition.  A global incumbent is passed as Gurobi's Cutoff so a fixed
assignment may be certified as unable to improve without finding a solution.

The runner is primarily a research comparison and a small-instance fallback;
it writes an append-only checkpoint after every fixed-assignment subproblem.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUROBI_PY = Path(r"C:\gurobi\win64\python311\lib")
if GUROBI_PY.exists() and str(GUROBI_PY) not in sys.path:
    sys.path.append(str(GUROBI_PY))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.instance import ProblemInstance
from models.model_3.solver import solve_exact as solve_m3
from models.model_4.alns_solver import solve_alns as solve_m4_alns
from utils.data_loader import parse_dataset
from utils.task_builder import build_tasks_for_model


CERTIFIED_FIXED_STATUSES = {"OPTIMAL", "INFEASIBLE", "CUTOFF"}


def _fleet():
    return json.loads(
        (ROOT / "config" / "fleet_config.json").read_text(encoding="utf-8")
    )


def load_problem(path: Path) -> ProblemInstance:
    fleet = _fleet()
    nodes, _ = parse_dataset(str(path))
    problem = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    return problem


def assignment_mask(problem, assignment) -> int:
    sats = [sat.id for sat in problem.satellites]
    if len(sats) != 2:
        raise ValueError("assignment enumeration currently requires exactly two satellites")
    mask = 0
    for idx, customer in enumerate(problem.customers):
        if assignment.get(customer.id, customer.assigned_sat) == sats[1]:
            mask |= 1 << idx
    return mask


def apply_mask(problem, mask: int):
    sats = [sat.id for sat in problem.satellites]
    assignment = {}
    for idx, customer in enumerate(problem.customers):
        sat_id = sats[1] if mask & (1 << idx) else sats[0]
        customer.assigned_sat = sat_id
        assignment[customer.id] = sat_id
    problem.aggregate_demands_from_pre_assignment()
    return assignment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--total-time-limit", type=float, default=120.0)
    parser.add_argument("--per-assignment-time-limit", type=float, default=30.0)
    parser.add_argument("--alns-time", type=float, default=10.0)
    parser.add_argument("--alns-seed", type=int, default=42)
    parser.add_argument("--output-dir", type=Path,
                        default=Path("results/m4_assignment_decomposition"))
    args = parser.parse_args()

    dataset = args.dataset if args.dataset.is_absolute() else ROOT / args.dataset
    out_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    checkpoint = out_dir / f"{dataset.stem}_{stamp}.jsonl"
    summary_path = out_dir / f"{dataset.stem}_{stamp}_summary.json"

    seed_problem = load_problem(dataset)
    alns_started = time.perf_counter()
    e1_seed, e2_seed, seed_assignment = solve_m4_alns(
        seed_problem,
        seed_problem.fleet.e2_per_sat_count,
        max_iterations=100000,
        max_time=args.alns_time,
        seed=args.alns_seed,
        verbose=False,
    )
    alns_wall = time.perf_counter() - alns_started
    if seed_assignment is None:
        seed_assignment = {
            customer.id: customer.assigned_sat
            for customer in seed_problem.customers
        }
    seed_mask = assignment_mask(seed_problem, seed_assignment)
    customer_count = len(seed_problem.customers)
    masks = sorted(
        range(1 << customer_count),
        key=lambda mask: ((mask ^ seed_mask).bit_count(), mask),
    )

    incumbent = math.inf
    best_assignment = None
    best_mask = None
    certified = 0
    cutoff_count = 0
    optimal_count = 0
    infeasible_count = 0
    incomplete_count = 0
    started = time.perf_counter()

    for position, mask in enumerate(masks):
        elapsed = time.perf_counter() - started
        remaining = args.total_time_limit - elapsed
        if remaining <= 0.0:
            break
        problem = load_problem(dataset)
        assignment = apply_mask(problem, mask)
        e1_tasks, e2_tasks = build_tasks_for_model(problem, 3)
        params = {}
        if math.isfinite(incumbent):
            params["Cutoff"] = incumbent - 1e-7
        sub_limit = min(args.per_assignment_time_limit, remaining)
        sub_started = time.perf_counter()
        e1, e2, info = solve_m3(
            problem,
            e1_tasks,
            e2_tasks,
            problem.fleet.e2_per_sat_count,
            TFp=9999.0,
            verbose=False,
            time_limit=sub_limit,
            return_info=True,
            return_incumbent_on_time_limit=True,
            gurobi_params=params,
        )
        sub_wall = time.perf_counter() - sub_started
        status = info["status"]
        is_certified = status in CERTIFIED_FIXED_STATUSES
        certified += int(is_certified)
        cutoff_count += int(status == "CUTOFF")
        optimal_count += int(status == "OPTIMAL")
        infeasible_count += int(status == "INFEASIBLE")
        incomplete_count += int(not is_certified)
        objective = info.get("objective")
        if objective is not None and objective < incumbent - 1e-8:
            incumbent = objective
            best_assignment = assignment
            best_mask = mask
        record = {
            "position": position,
            "mask": mask,
            "hamming_from_seed": (mask ^ seed_mask).bit_count(),
            "status": status,
            "certified_fixed_assignment": is_certified,
            "objective": objective,
            "best_bound": info.get("best_bound"),
            "runtime": info.get("runtime"),
            "wall_time": sub_wall,
            "global_incumbent": None if not math.isfinite(incumbent) else incumbent,
        }
        with checkpoint.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        if position % 25 == 0 or objective is not None:
            print(json.dumps(record, ensure_ascii=False), flush=True)

    processed = certified + incomplete_count
    all_certified = processed == len(masks) and incomplete_count == 0
    summary = {
        "method": "exact_two_satellite_assignment_enumeration",
        "dataset": str(dataset),
        "customers": customer_count,
        "assignment_count": len(masks),
        "processed": processed,
        "certified_fixed_assignments": certified,
        "incomplete_fixed_assignments": incomplete_count,
        "cutoff_count": cutoff_count,
        "optimal_count": optimal_count,
        "infeasible_count": infeasible_count,
        "all_assignments_certified": all_certified,
        "proven_optimal": all_certified and math.isfinite(incumbent),
        "objective": None if not math.isfinite(incumbent) else incumbent,
        "best_mask": best_mask,
        "best_assignment": best_assignment,
        "seed_mask": seed_mask,
        "alns_wall_time": alns_wall,
        "enumeration_wall_time": time.perf_counter() - started,
        "checkpoint": str(checkpoint),
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Wrote {summary_path}")


if __name__ == "__main__":
    main()
