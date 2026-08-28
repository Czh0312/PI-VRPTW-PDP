# -*- coding: utf-8 -*-
"""Measure when ALNS first reaches each certified small-instance optimum."""
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

from experiments.runs.run_model12346_alns_scaling import (
    MODELS,
    load_problem,
    route_cost,
    solve_one,
)


def load_exact_rows():
    paths = [
        ROOT / "output" / "results" / "mainline_small_exact"
        / "small_exact_20260825_123124.jsonl",
        ROOT / "output" / "results" / "model6_standard_small_exact_unlimited"
        / "small_exact_20260826_171404.jsonl",
    ]
    result = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if (row.get("scenario") == "standard_same_day"
                    and row.get("proven_optimal")):
                result[(row["instance"], int(row["model"]))] = row
    return result


def finite(value):
    return value if value is None or not isinstance(value, float) \
        or math.isfinite(value) else None


def solve_multistart(model, problem, *, max_time, iterations, seed, starts,
                     target, tolerance, stagnation_fraction):
    portfolio_start = time.perf_counter()
    best = None
    total_iterations = 0
    total_restarts = 0
    total_local_improvements = 0
    used = 0
    target_hit_time = None

    for start_idx in range(starts):
        elapsed = time.perf_counter() - portfolio_start
        remaining = max_time - elapsed
        if remaining <= 1e-3:
            break
        starts_left = starts - start_idx
        run_budget = remaining / starts_left
        overrides = {
            "cooling_schedule": "time",
            "stagnation_time": max(0.05, run_budget * stagnation_fraction),
            "reheat_fraction": 0.5,
            "max_restarts": 2,
            "intensification_time": max(0.02, run_budget * 0.10),
        }
        offset = time.perf_counter() - portfolio_start
        e1, e2, assignment, info = solve_one(
            model, problem, max_time=run_budget, iterations=iterations,
            seed=seed + start_idx, target_objective=target,
            target_tolerance=tolerance,
            alns_config_overrides=overrides,
        )
        used += 1
        total_iterations += int(info.get("iterations") or 0)
        total_restarts += int(info.get("restarts") or 0)
        total_local_improvements += int(
            info.get("local_search_improvements") or 0)
        objective = info.get("objective")
        if (objective is not None
                and (best is None or objective < best[3]["objective"])):
            best = (e1, e2, assignment, dict(info))
        if info.get("target_hit_time") is not None:
            target_hit_time = offset + float(info["target_hit_time"])
            break

    if best is None:
        raise RuntimeError("all ALNS starts failed to return a feasible solution")
    e1, e2, assignment, info = best
    elapsed = time.perf_counter() - portfolio_start
    info["runtime"] = elapsed
    info["iterations"] = total_iterations
    info["target_hit_time"] = target_hit_time
    info["termination_reason"] = "target" if target_hit_time is not None else "time"
    info["restarts"] = total_restarts
    info["local_search_improvements"] = total_local_improvements
    info["multi_start_count"] = used
    return e1, e2, assignment, info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-time", type=float, default=20.0)
    parser.add_argument("--iterations", type=int, default=1_000_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--starts", type=int, default=1)
    parser.add_argument("--stagnation-fraction", type=float, default=0.30)
    parser.add_argument("--tolerance", type=float, default=1e-5)
    parser.add_argument("--models", nargs="*", type=int, choices=MODELS)
    parser.add_argument("--instances", nargs="*")
    parser.add_argument(
        "--output-dir", default="output/results/small_alns_time_to_exact")
    args = parser.parse_args()
    if (args.max_time <= 0 or args.iterations <= 0 or args.tolerance < 0
            or args.starts <= 0 or not 0 < args.stagnation_fraction < 1):
        parser.error("time/iterations must be positive and tolerance nonnegative")

    fleet = json.loads((ROOT / "config" / "routing" / "validation" / "zhou_small.json").read_text(
        encoding="utf-8"))
    paths = sorted((ROOT / "data" / "zhou_later_pickup" / "small"
                    / "standard_same_day").glob("*.csv"))
    if args.instances:
        wanted = {x if x.endswith(".csv") else f"{x}.csv"
                  for x in args.instances}
        paths = [p for p in paths if p.name in wanted]
    models = tuple(args.models) if args.models else MODELS
    exact = load_exact_rows()

    output_dir = ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    stem = (f"small_time_to_exact_t{args.max_time:g}_starts{args.starts}"
            f"_seed{args.seed}_{stamp}")
    jsonl_path = output_dir / f"{stem}.jsonl"
    csv_path = output_dir / f"{stem}.csv"
    rows = []

    for path in paths:
        problem = load_problem(path, fleet)
        for model in models:
            exact_row = exact.get((path.stem, model))
            if exact_row is None:
                raise RuntimeError(f"missing exact target: {path.stem} M{model}")
            target = float(exact_row["objective"])
            exact_wall = float(exact_row.get("wall_time")
                               or exact_row["runtime"])
            started = time.perf_counter()
            try:
                e1, e2, _assignment, info = solve_multistart(
                    model, problem, max_time=args.max_time,
                    iterations=args.iterations, seed=args.seed,
                    starts=args.starts, target=target,
                    tolerance=args.tolerance,
                    stagnation_fraction=args.stagnation_fraction,
                )
                wall = time.perf_counter() - started
                objective = info.get("objective")
                hit_time = info.get("target_hit_time")
                hit = (hit_time is not None and objective is not None
                       and objective <= target + args.tolerance)
                audit = route_cost(problem, e1, e2)
                saved = exact_wall - hit_time if hit else None
                row = {
                    "instance": path.stem,
                    "customers": len(problem.customers),
                    "model": model,
                    "seed": args.seed,
                    "exact_objective": target,
                    "exact_runtime": exact_row.get("runtime"),
                    "exact_wall_time": exact_wall,
                    "alns_hit": hit,
                    "alns_target_hit_time": hit_time,
                    "alns_runtime": info.get("runtime"),
                    "alns_wall_time": wall,
                    "alns_objective": objective,
                    "final_gap_pct": ((objective - target) / target * 100.0
                                      if objective is not None else None),
                    "time_saved_sec": saved,
                    "time_saved_pct": (saved / exact_wall * 100.0
                                       if hit and exact_wall > 0 else None),
                    "speedup_exact_over_alns": (exact_wall / hit_time
                                                if hit and hit_time > 0
                                                else None),
                    "iterations": info.get("iterations"),
                    "restarts": info.get("restarts", 0),
                    "local_search_improvements": info.get(
                        "local_search_improvements", 0),
                    "multi_start_count": info.get("multi_start_count", 1),
                    "termination_reason": info.get("termination_reason"),
                    "route_cost_audit": audit,
                    "route_cost_delta": (audit - objective
                                         if audit is not None
                                         and objective is not None else None),
                    "max_time": args.max_time,
                    "planned_starts": args.starts,
                    "stagnation_fraction": args.stagnation_fraction,
                    "target_tolerance": args.tolerance,
                    "fleet_config": "config/routing/validation/zhou_small.json",
                }
            except Exception as exc:
                row = {
                    "instance": path.stem, "customers": len(problem.customers),
                    "model": model, "seed": args.seed,
                    "exact_objective": target,
                    "exact_runtime": exact_row.get("runtime"),
                    "exact_wall_time": exact_wall, "alns_hit": False,
                    "alns_target_hit_time": None, "alns_runtime": None,
                    "alns_wall_time": time.perf_counter() - started,
                    "alns_objective": None, "final_gap_pct": None,
                    "time_saved_sec": None, "time_saved_pct": None,
                    "speedup_exact_over_alns": None, "iterations": None,
                    "restarts": None, "local_search_improvements": None,
                    "multi_start_count": None,
                    "termination_reason": "error", "route_cost_audit": None,
                    "route_cost_delta": None, "max_time": args.max_time,
                    "planned_starts": args.starts,
                    "stagnation_fraction": args.stagnation_fraction,
                    "target_tolerance": args.tolerance,
                    "fleet_config": "config/routing/validation/zhou_small.json",
                    "error": repr(exc),
                }
            row = {key: finite(value) for key, value in row.items()}
            rows.append(row)
            with jsonl_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(json.dumps(row, ensure_ascii=False), flush=True)

    fields = []
    for row in rows:
        fields.extend(key for key in row if key not in fields)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {jsonl_path}")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
