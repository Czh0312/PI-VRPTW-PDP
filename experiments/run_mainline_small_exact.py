# -*- coding: utf-8 -*-
"""Run the small-scale certified-exact benchmark for Models 1/2/6/7.

The output is append-only JSONL plus a compact CSV summary.  Only Gurobi's
``OPTIMAL`` status is counted as exact; time-limit incumbents are retained
separately for diagnostics and are never merged into an exact-optimum column.
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.instance import ProblemInstance
from models.model_1.solver import solve_exact as solve_m1
from models.model_2.solver import solve_exact as solve_m2
from models.model_6.exact import solve_exact as solve_m6
from models.model_7.exact import solve_exact as solve_m7
from utils.data_loader import parse_dataset
from utils.task_builder import build_tasks_for_model


def _fleet():
    return json.loads(
        (ROOT / "config" / "fleet_config.json").read_text(encoding="utf-8")
    )


def _problem(path, fleet):
    nodes, _ = parse_dataset(str(path))
    problem = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    return problem


def _record(model, scale, instance, info, elapsed):
    return {
        "model": model,
        "scale": scale,
        "instance": instance,
        "status": str(info.get("status")),
        "proven_optimal": bool(info.get("proven_optimal")),
        "solution_type": info.get("solution_type"),
        "objective": info.get("objective"),
        "best_bound": info.get("best_bound"),
        "mip_gap": info.get("mip_gap"),
        "runtime": info.get("runtime"),
        "wall_time": elapsed,
        "sol_count": info.get("sol_count"),
    }


def solve_one(model, problem, time_limit, warm_start_routes=None):
    """Solve one model instance. Returns (info, routes_or_None)."""
    vps = problem.fleet.e2_per_sat_count
    routes = None
    if model == 1:
        e1, e2 = build_tasks_for_model(problem, 1)
        r1, r2, info = solve_m1(
            problem, e1, e2, vps, time_limit=time_limit,
            verbose=False, return_info=True,
        )
        routes = (r1, r2)
    elif model == 2:
        e1, e2 = build_tasks_for_model(problem, 2)
        r1, r2, info = solve_m2(
            problem, e1, e2, vps, TFp=9999.0, time_limit=time_limit,
            verbose=False, return_info=True,
        )
        routes = (r1, r2)
    elif model == 6:
        r1, r2, _, info = solve_m6(
            problem, vps, time_limit=time_limit,
            verbose=False, return_info=True,
            warm_start_routes=warm_start_routes,
        )
        routes = (r1, r2)
    elif model == 7:
        r1, r2, _, info = solve_m7(
            problem, vps, time_limit=time_limit,
            verbose=False, return_info=True,
        )
        routes = (r1, r2)
    else:
        raise ValueError(model)
    return info, routes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scales", nargs="+", default=["5_customers", "10_customers"])
    parser.add_argument("--instances", nargs="*", default=None,
                        help="Instance stems, e.g. C101 C201. Default: all.")
    parser.add_argument("--models", nargs="+", type=int, default=[1, 2, 6, 7])
    parser.add_argument("--time-limit", type=float, default=120.0)
    parser.add_argument("--output-dir", default="results/mainline_small_exact")
    args = parser.parse_args()

    out_dir = ROOT / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    jsonl_path = out_dir / f"small_exact_{stamp}.jsonl"
    csv_path = out_dir / f"small_exact_{stamp}.csv"
    fleet = _fleet()

    records = []
    for scale in args.scales:
        paths = sorted((ROOT / "data" / scale).glob("*.csv"))
        if args.instances:
            wanted = {f"{x}.csv" for x in args.instances}
            paths = [p for p in paths if p.name in wanted]
        for path in paths:
            problem = _problem(path, fleet)
            # Cache M2 routes for M6 warm start, M1 routes for M7 warm start
            m2_routes = None
            m1_routes = None
            for model in args.models:
                started = time.perf_counter()
                ws = None
                if model == 6 and m2_routes is not None:
                    ws = m2_routes
                try:
                    info, routes = solve_one(model, problem, args.time_limit,
                                              warm_start_routes=ws)
                    # Cache routes for downstream warm starts
                    if model == 2 and routes[0] is not None:
                        m2_routes = routes
                    if model == 1 and routes[0] is not None:
                        m1_routes = routes
                    rec = _record(model, scale, path.stem, info,
                                  time.perf_counter() - started)
                except Exception as exc:  # keep the batch auditable
                    rec = {
                        "model": model, "scale": scale, "instance": path.stem,
                        "status": "SCRIPT_ERROR", "proven_optimal": False,
                        "solution_type": "script_error", "objective": None,
                        "best_bound": None, "mip_gap": None, "runtime": None,
                        "wall_time": time.perf_counter() - started,
                        "sol_count": 0, "error": repr(exc),
                    }
                    routes = None
                records.append(rec)
                print(json.dumps(rec, ensure_ascii=False), flush=True)
                with jsonl_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # Records were appended as they completed, so JSONL survives interruption.
    fields = ["model", "scale", "instance", "status", "proven_optimal",
              "solution_type", "objective", "best_bound", "mip_gap",
              "runtime", "wall_time", "sol_count"]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)

    print(f"\nWrote {jsonl_path}")
    print(f"Wrote {csv_path}")
    by_key = {}
    for rec in records:
        key = (rec["scale"], rec["instance"])
        by_key.setdefault(key, {})[rec["model"]] = rec
    checks = {"M7<=M1": [], "M6<=M2": []}
    for (scale, instance), vals in sorted(by_key.items()):
        if 1 in vals and 7 in vals and vals[1]["objective"] is not None and vals[7]["objective"] is not None:
            checks["M7<=M1"].append(
                (scale, instance, vals[7]["objective"] <= vals[1]["objective"] + 1e-5)
            )
        if 2 in vals and 6 in vals and vals[2]["objective"] is not None and vals[6]["objective"] is not None:
            checks["M6<=M2"].append(
                (scale, instance, vals[6]["objective"] <= vals[2]["objective"] + 1e-5)
            )
    for name, rows in checks.items():
        bad = [row[:2] for row in rows if not row[2]]
        print(f"{name}: {len(rows)-len(bad)}/{len(rows)} passed; violations={bad}")

    # Any relationship violation is an implementation warning, not silent data.
    if any(not row[2] for rows in checks.values() for row in rows):
        sys.exit(2)


if __name__ == "__main__":
    main()
