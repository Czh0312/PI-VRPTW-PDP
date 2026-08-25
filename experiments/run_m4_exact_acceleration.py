# -*- coding: utf-8 -*-
"""Controlled A/B benchmark for exact M4 acceleration strategies.

The runner keeps every structural switch independent, uses the same M3 warm
start for isolated comparisons, and records certificate-bearing Gurobi bounds.
The ALNS variants change only the incumbent supplied to the same exact MIP.
"""
from __future__ import annotations

import argparse
import csv
import json
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
from models.model_4.exact import solve_exact as solve_m4
from utils.data_loader import parse_dataset
from utils.task_builder import build_tasks_for_model


STRUCTURAL = {
    "baseline": {},
    "arc_pruning": {"prune_e2_arcs": True},
    "arc_time_m": {"arc_specific_time_m": True},
    "drop_precedence": {"drop_implied_precedence": True},
    "route_symmetry": {"route_key_symmetry": True},
    "branching": {"branch_assignment_first": True},
    "combined": {
        "prune_e2_arcs": True,
        "arc_specific_time_m": True,
        "drop_implied_precedence": True,
        "route_key_symmetry": True,
        "branch_assignment_first": True,
    },
}

TUNED_PARAMS = {
    "MIPFocus": 3,
    "Cuts": 2,
    "Presolve": 2,
    "Symmetry": 2,
}

DEFAULT_VARIANTS = [
    "baseline",
    "arc_pruning",
    "arc_time_m",
    "drop_precedence",
    "route_symmetry",
    "branching",
    "solver_tuning",
    "alns_warm",
    "combined",
    "combined_alns",
    "combined_alns_tuned",
    "combined_fixed_tuned",
]


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


def _physical(node_id: str) -> str:
    if node_id.endswith(("d", "p")):
        return node_id[:-1]
    return node_id


def route_cost(problem, e1_routes, e2_routes) -> float:
    routes = list(e1_routes or [])
    routes.extend(
        route for sat_routes in (e2_routes or {}).values()
        for route in sat_routes
    )
    return sum(
        problem.distance(_physical(i), _physical(j))
        for route in routes for i, j in zip(route, route[1:])
    )


def build_m3_warm_start(dataset: Path, time_limit: float | None):
    problem = load_problem(dataset)
    e1_tasks, e2_tasks = build_tasks_for_model(problem, 3)
    e1, e2, info = solve_m3(
        problem,
        e1_tasks,
        e2_tasks,
        problem.fleet.e2_per_sat_count,
        TFp=9999.0,
        time_limit=time_limit,
        verbose=False,
        return_info=True,
        return_incumbent_on_time_limit=True,
    )
    return ((e1, e2) if e1 is not None else None), info


def build_fixed_satellite_warm_start(dataset: Path, sat_id: str,
                                     time_limit: float | None):
    """Solve the fixed-assignment M3 induced by assigning everyone to sat_id."""
    problem = load_problem(dataset)
    valid = {sat.id for sat in problem.satellites}
    if sat_id not in valid:
        raise ValueError(f"unknown fixed warm-start satellite {sat_id!r}; valid={sorted(valid)}")
    for customer in problem.customers:
        customer.assigned_sat = sat_id
    problem.aggregate_demands_from_pre_assignment()
    e1_tasks, e2_tasks = build_tasks_for_model(problem, 3)
    e1, e2, info = solve_m3(
        problem,
        e1_tasks,
        e2_tasks,
        problem.fleet.e2_per_sat_count,
        TFp=9999.0,
        time_limit=time_limit,
        verbose=False,
        return_info=True,
        return_incumbent_on_time_limit=True,
    )
    return ((e1, e2) if e1 is not None else None), info


def build_alns_warm_start(dataset: Path, seconds: float, seeds: list[int]):
    best = None
    trials = []
    for seed in seeds:
        problem = load_problem(dataset)
        started = time.perf_counter()
        e1, e2, assignment = solve_m4_alns(
            problem,
            problem.fleet.e2_per_sat_count,
            max_iterations=100000,
            max_time=seconds,
            seed=seed,
            verbose=False,
        )
        elapsed = time.perf_counter() - started
        cost = None if e1 is None else route_cost(problem, e1, e2)
        trial = {
            "seed": seed,
            "wall_time": elapsed,
            "objective": cost,
            "assignment": assignment,
        }
        trials.append(trial)
        if cost is not None and (best is None or cost < best[0] - 1e-9):
            best = (cost, (e1, e2), assignment, seed)
    return (None if best is None else best[1]), {
        "best_objective": None if best is None else best[0],
        "best_seed": None if best is None else best[3],
        "trials": trials,
    }


def variant_settings(name: str):
    if name in STRUCTURAL:
        return STRUCTURAL[name], {}, "m3"
    if name == "solver_tuning":
        return {}, dict(TUNED_PARAMS), "m3"
    if name == "alns_warm":
        return {}, {}, "alns"
    if name == "combined_alns":
        return STRUCTURAL["combined"], {}, "alns"
    if name == "combined_alns_tuned":
        return STRUCTURAL["combined"], dict(TUNED_PARAMS), "alns"
    if name == "combined_fixed_tuned":
        return STRUCTURAL["combined"], dict(TUNED_PARAMS), "fixed"
    raise ValueError(f"unknown variant {name!r}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--variants", nargs="+", default=DEFAULT_VARIANTS)
    parser.add_argument("--time-limit", type=float, default=120.0)
    parser.add_argument("--m3-warm-time", type=float, default=120.0)
    parser.add_argument("--alns-time", type=float, default=10.0)
    parser.add_argument("--alns-seeds", nargs="+", type=int,
                        default=[41, 42, 43])
    parser.add_argument(
        "--fixed-warm-satellite", default="S1",
        help="Satellite used by the combined_fixed_tuned warm-start variant.",
    )
    parser.add_argument("--output-dir", type=Path,
                        default=Path("results/m4_exact_acceleration"))
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    dataset = args.dataset if args.dataset.is_absolute() else ROOT / args.dataset
    out_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    stem = dataset.stem
    jsonl_path = out_dir / f"{stem}_{stamp}.jsonl"
    csv_path = out_dir / f"{stem}_{stamp}.csv"

    m3_warm, m3_info = build_m3_warm_start(dataset, args.m3_warm_time)
    need_alns = any(variant_settings(v)[2] == "alns" for v in args.variants)
    alns_warm, alns_info = (None, {"trials": []})
    if need_alns:
        alns_warm, alns_info = build_alns_warm_start(
            dataset, args.alns_time, args.alns_seeds)
    need_fixed = any(variant_settings(v)[2] == "fixed" for v in args.variants)
    fixed_warm, fixed_info = (None, {})
    if need_fixed:
        fixed_warm, fixed_info = build_fixed_satellite_warm_start(
            dataset, args.fixed_warm_satellite, args.m3_warm_time)

    metadata = {
        "dataset": str(dataset),
        "time_limit": args.time_limit,
        "m3_warm_info": m3_info,
        "alns_warm_info": alns_info,
        "fixed_warm_satellite": args.fixed_warm_satellite,
        "fixed_warm_info": fixed_info,
    }
    (out_dir / f"{stem}_{stamp}_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    records = []
    for variant in args.variants:
        structural, params, warm_kind = variant_settings(variant)
        if warm_kind == "alns":
            warm = alns_warm
        elif warm_kind == "fixed":
            warm = fixed_warm
        else:
            warm = m3_warm
        problem = load_problem(dataset)
        log_path = out_dir / f"{stem}_{stamp}_{variant}.log"
        params = dict(params)
        try:
            # Gurobi 11 on Windows cannot open a Unicode absolute log path,
            # while the same file is valid through an ASCII relative path.
            params["LogFile"] = str(log_path.relative_to(ROOT))
        except ValueError:
            params["LogFile"] = str(log_path)
        started = time.perf_counter()
        e1, e2, assignment, info = solve_m4(
            problem,
            problem.fleet.e2_per_sat_count,
            time_limit=args.time_limit,
            verbose=args.verbose,
            return_info=True,
            return_incumbent_on_time_limit=True,
            warm_start_routes=warm,
            gurobi_params=params,
            formulation_options=structural,
        )
        record = {
            "variant": variant,
            "dataset": stem,
            "warm_start": warm_kind,
            "warm_objective": (
                alns_info.get("best_objective") if warm_kind == "alns"
                else fixed_info.get("objective") if warm_kind == "fixed"
                else m3_info.get("objective")
            ),
            "wall_time": time.perf_counter() - started,
            **info,
            "log_file": str(log_path),
        }
        # Keep the compact scalar assignment count without embedding routes.
        if assignment:
            record["assignment_counts"] = {
                s: sum(1 for value in assignment.values() if value == s)
                for s in sorted(set(assignment.values()))
            }
        records.append(record)
        with jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps(record, ensure_ascii=False), flush=True)

    scalar_fields = [
        "variant", "dataset", "warm_start", "warm_objective", "status",
        "proven_optimal", "objective", "best_bound", "mip_gap", "runtime",
        "wall_time", "node_count", "work", "model_rows", "model_columns",
        "model_binary", "model_nonzeros", "log_file",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=scalar_fields,
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    print(f"Wrote {jsonl_path}")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
