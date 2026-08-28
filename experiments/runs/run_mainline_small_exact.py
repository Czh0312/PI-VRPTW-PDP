# -*- coding: utf-8 -*-
"""Run the small-scale certified-exact benchmark for Models 1/2/3/4/5/6.

The output is append-only JSONL plus a compact CSV summary.  Only results with
``proven_optimal=True`` are counted as exact; time-limit incumbents are retained
separately for diagnostics and are never merged into an exact-optimum column.
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUROBI_PY = Path(r"C:\gurobi\win64\python311\lib")
if GUROBI_PY.exists() and str(GUROBI_PY) not in sys.path:
    # The project interpreter is Python 3.11, but the Gurobi package is kept
    # in the solver installation instead of site-packages.
    sys.path.append(str(GUROBI_PY))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.routing.common.domain.instance import ProblemInstance
from models.routing.variants.model_1.exact_solver import solve_exact as solve_m1
from models.routing.variants.model_2.exact_solver import solve_exact as solve_m2
from models.routing.variants.model_3.exact_solver import solve_exact as solve_m3
from models.routing.variants.model_4.exact_solver import solve_exact as solve_m4
from models.routing.variants.model_5.exact_solver import solve_exact as solve_m5
from models.routing.variants.model_6.exact_solver import solve_exact as solve_m6
from models.routing.common.data_loader import parse_dataset
from models.routing.common.task_builder import build_tasks_for_model


def _fleet():
    return json.loads(
        (ROOT / "config" / "routing" / "validation" / "zhou_small.json").read_text(encoding="utf-8")
    )


def _problem(path, fleet):
    nodes, _ = parse_dataset(str(path))
    problem = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    return problem


def _fleet_fields(problem):
    return {
        "e1_vehicle_count": problem.e1_vehicle_count,
        "e1_weight_capacity": problem.e1_weight_cap,
        "e1_container_capacity": problem.e1_volume_cap,
        "e2_vehicles_per_satellite": problem.fleet.e2_per_sat_count,
        "e2_weight_capacity": problem.e2_weight_cap,
        "e2_container_capacity": problem.e2_volume_cap,
        "container_weight": problem.fleet.container_weight,
        "container_volume": problem.fleet.container_volume,
        "speed": problem.speed,
    }


def _record(model, scale_group, scenario, instance, info, elapsed, problem):
    return {
        "model": model,
        "scale_group": scale_group,
        "scenario": scenario,
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
        "formulation": info.get("formulation"),
        "proof_scope": info.get("proof_scope"),
        "transshipment_count": info.get("transshipment_count"),
        **_fleet_fields(problem),
    }


def solve_one(model, problem, time_limit, warm_start_routes=None,
              gurobi_params=None, m4_formulation_options=None):
    """Solve one model instance. Returns (info, routes_or_None)."""
    vps = problem.fleet.e2_per_sat_count
    routes = None
    if model == 1:
        e1, e2 = build_tasks_for_model(problem, 1)
        r1, r2, info = solve_m1(
            problem, e1, e2, vps, time_limit=time_limit,
            verbose=False, return_info=True, gurobi_params=gurobi_params,
        )
        routes = (r1, r2)
    elif model == 2:
        r1, r2, _, info = solve_m2(
            problem, vps, time_limit=time_limit,
            verbose=False, return_info=True, gurobi_params=gurobi_params,
        )
        routes = (r1, r2)
    elif model == 3:
        e1, e2 = build_tasks_for_model(problem, 3)
        r1, r2, info = solve_m3(
            problem, e1, e2, vps, TFp=9999.0, time_limit=time_limit,
            verbose=False, return_info=True, gurobi_params=gurobi_params,
        )
        routes = (r1, r2)
    elif model == 4:
        r1, r2, _, info = solve_m4(
            problem, vps, time_limit=time_limit,
            verbose=False, return_info=True,
            warm_start_routes=warm_start_routes, gurobi_params=gurobi_params,
            formulation_options=m4_formulation_options,
        )
        routes = (r1, r2)
    elif model == 5:
        e1, e2 = build_tasks_for_model(problem, 5)
        r1, r2, info = solve_m5(
            problem, e1, e2, vps, time_limit=time_limit,
            verbose=False, return_info=True,
            return_incumbent_on_time_limit=True,
            gurobi_params=gurobi_params,
        )
        routes = (r1, r2)
    elif model == 6:
        e1, e2 = build_tasks_for_model(problem, 6)
        r1, r2, info = solve_m6(
            problem, e1, e2, vps, TFp=9999.0,
            time_limit=time_limit, verbose=False, return_info=True,
            return_incumbent_on_time_limit=True,
            warm_start_routes=warm_start_routes,
            gurobi_params=gurobi_params,
        )
        routes = (r1, r2)
    else:
        raise ValueError(model)
    return info, routes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--groups", nargs="+", default=["small"],
                        choices=["small", "large"])
    parser.add_argument(
        "--scenarios", nargs="+", default=["standard_same_day"],
        choices=["short_same_day", "standard_same_day", "next_day"],
    )
    parser.add_argument("--instances", nargs="*", default=None,
                        help=(
                            "Instance stems, e.g. Set1_7-2-1b. "
                            "Default: all instances in the selected groups."
                        ))
    parser.add_argument("--models", nargs="+", type=int, default=[1, 2, 3, 4])
    time_group = parser.add_mutually_exclusive_group()
    time_group.add_argument("--time-limit", type=float, default=120.0)
    time_group.add_argument(
        "--no-time-limit", dest="time_limit", action="store_const", const=None,
        help=(
            "Do not set Gurobi TimeLimit; each model runs until it proves "
            "OPTIMAL or INFEASIBLE."
        ),
    )
    parser.add_argument(
        "--gurobi-param", action="append", default=[], metavar="NAME=VALUE",
        help="Additional Gurobi parameter; VALUE accepts JSON numbers/strings.",
    )
    parser.add_argument(
        "--m4-formulation", choices=["baseline", "arc_pruning", "combined"],
        default="baseline",
        help=(
            "Exact M4 formulation. 'arc_pruning' is the recommended default "
            "for 7/10c; 'combined' adds safe M/precedence/symmetry/branching "
            "strengthening for difficult instances."
        ),
    )
    parser.add_argument("--output-dir", default="output/results/mainline_small_exact")
    args = parser.parse_args()
    if args.time_limit is not None and args.time_limit <= 0.0:
        parser.error("--time-limit must be positive")
    gurobi_params = {}
    for item in args.gurobi_param:
        if "=" not in item:
            parser.error(f"--gurobi-param must be NAME=VALUE, got {item!r}")
        name, raw_value = item.split("=", 1)
        try:
            gurobi_params[name] = json.loads(raw_value)
        except json.JSONDecodeError:
            gurobi_params[name] = raw_value
    m4_formulations = {
        "baseline": {},
        "arc_pruning": {"prune_e2_arcs": True},
        "combined": {
            "prune_e2_arcs": True,
            "arc_specific_time_m": True,
            "drop_implied_precedence": True,
            "route_key_symmetry": True,
            "branch_assignment_first": True,
        },
    }
    m4_formulation_options = m4_formulations[args.m4_formulation]

    out_dir = ROOT / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    jsonl_path = out_dir / f"small_exact_{stamp}.jsonl"
    csv_path = out_dir / f"small_exact_{stamp}.csv"
    fleet = _fleet()

    records = []
    dataset_root = ROOT / "data" / "zhou_later_pickup"
    for scale_group in args.groups:
        for scenario in args.scenarios:
            paths = sorted((dataset_root / scale_group / scenario).glob("*.csv"))
            if args.instances:
                wanted = {f"{x}.csv" for x in args.instances}
                paths = [p for p in paths if p.name in wanted]
            for path in paths:
                run_instance(
                    path, scale_group, scenario, args, fleet, gurobi_params,
                    m4_formulation_options, records, jsonl_path,
                )

    # Records were appended as they completed, so JSONL survives interruption.
    fields = ["model", "scale_group", "scenario", "instance", "status",
              "proven_optimal", "solution_type", "objective", "best_bound",
              "mip_gap", "runtime", "wall_time", "sol_count",
              "formulation", "proof_scope", "transshipment_count",
              "e1_vehicle_count", "e1_weight_capacity",
              "e1_container_capacity", "e2_vehicles_per_satellite",
              "e2_weight_capacity", "e2_container_capacity",
              "container_weight", "container_volume", "speed"]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)

    print(f"\nWrote {jsonl_path}")
    print(f"Wrote {csv_path}")
    by_key = {}
    for rec in records:
        key = (rec["scale_group"], rec["scenario"], rec["instance"])
        by_key.setdefault(key, {})[rec["model"]] = rec
    checks = {"M2<=M1": [], "M4<=M3": []}
    for (scale_group, scenario, instance), vals in sorted(by_key.items()):
        case = (scale_group, scenario, instance)
        if 1 in vals and 2 in vals and vals[1]["objective"] is not None and vals[2]["objective"] is not None:
            checks["M2<=M1"].append(
                (*case, vals[2]["objective"] <= vals[1]["objective"] + 1e-5)
            )
        if 3 in vals and 4 in vals and vals[3]["objective"] is not None and vals[4]["objective"] is not None:
            checks["M4<=M3"].append(
                (*case, vals[4]["objective"] <= vals[3]["objective"] + 1e-5)
            )
    for name, rows in checks.items():
        bad = [row[:3] for row in rows if not row[3]]
        print(f"{name}: {len(rows)-len(bad)}/{len(rows)} passed; violations={bad}")

    # Any relationship violation is an implementation warning, not silent data.
    if any(not row[3] for rows in checks.values() for row in rows):
        sys.exit(2)


def run_instance(path, scale_group, scenario, args, fleet, gurobi_params,
                 m4_formulation_options, records, jsonl_path):
    """Run all requested models for one organized dataset instance."""
    problem = _problem(path, fleet)
    # Cache M3 routes for the M4 warm start.
    m3_routes = None
    for model in args.models:
        started = time.perf_counter()
        warm_start = m3_routes if model in (4, 6) else None
        try:
            info, routes = solve_one(
                model, problem, args.time_limit,
                warm_start_routes=warm_start,
                gurobi_params=gurobi_params,
                m4_formulation_options=m4_formulation_options,
            )
            if model == 3 and routes[0] is not None:
                m3_routes = routes
            rec = _record(
                model, scale_group, scenario, path.stem, info,
                time.perf_counter() - started, problem,
            )
        except Exception as exc:  # keep the batch auditable
            rec = {
                "model": model, "scale_group": scale_group,
                "scenario": scenario, "instance": path.stem,
                "status": "SCRIPT_ERROR", "proven_optimal": False,
                "solution_type": "script_error", "objective": None,
                "best_bound": None, "mip_gap": None, "runtime": None,
                "wall_time": time.perf_counter() - started,
                "sol_count": 0, "error": repr(exc),
                **_fleet_fields(problem),
            }
        records.append(rec)
        print(json.dumps(rec, ensure_ascii=False), flush=True)
        with jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(rec, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
