# -*- coding: utf-8 -*-
"""Run the small-scale certified-exact benchmark for Models 1--7.

The output is append-only JSONL plus a compact CSV summary.  Only results with
``proven_optimal=True`` are counted as exact; time-limit incumbents are retained
separately for diagnostics and are never merged into an exact-optimum column.
"""
import argparse
import copy
import csv
import json
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor
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
from models.routing.alns.variants.model_4 import solve_alns as solve_m4_alns
from models.routing.variants.model_5.exact_solver import solve_exact as solve_m5
from models.routing.variants.model_6.exact_solver import solve_exact as solve_m6
from models.routing.variants.model_7.exact_solver import solve_exact as solve_m7
from models.routing.common.data_loader import parse_dataset
from models.routing.common.task_builder import build_tasks_for_model
from models.routing.common.solution_audit import audit_exact_solution


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


def _run_m4_alns_warm_trial(problem, seconds, seed):
    """Run one isolated ALNS trial; safe as a spawned-process entry point."""
    started = time.perf_counter()
    try:
        e1, e2, assignment, alns_info = solve_m4_alns(
            problem,
            problem.fleet.e2_per_sat_count,
            max_iterations=1_000_000,
            max_time=seconds,
            seed=seed,
            verbose=False,
            return_info=True,
        )
        return {
            "seed": int(seed),
            "wall_time": time.perf_counter() - started,
            "routes": (e1, e2),
            "assignment": assignment,
            "alns_info": alns_info,
            "error": None,
        }
    except Exception as exc:
        return {
            "seed": int(seed),
            "wall_time": time.perf_counter() - started,
            "routes": None,
            "assignment": None,
            "alns_info": {},
            "error": repr(exc),
        }


def _build_m4_alns_warm_start(problem, seconds, seeds, workers=1):
    """Return the best independently audited M4 ALNS incumbent."""
    best = None
    trials = []
    total_started = time.perf_counter()
    workers = max(1, min(int(workers), len(seeds)))
    if workers > 1:
        # Processes, rather than threads, isolate ALNS' module-level random
        # generator.  All workers finish before Gurobi starts, so the exact
        # solve still receives the full machine and an unchanged time budget.
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = [(
                seed,
                executor.submit(
                    _run_m4_alns_warm_trial, problem, seconds, seed),
            ) for seed in seeds]
            raw_trials = []
            for seed, future in futures:
                try:
                    raw_trials.append(future.result())
                except Exception as exc:
                    raw_trials.append({
                        "seed": int(seed),
                        "wall_time": None,
                        "routes": None,
                        "assignment": None,
                        "alns_info": {},
                        "error": repr(exc),
                    })
    else:
        # The sequential path preserves the historical execution protocol.
        raw_trials = [
            _run_m4_alns_warm_trial(copy.deepcopy(problem), seconds, seed)
            for seed in seeds
        ]

    for raw_trial in raw_trials:
        seed = raw_trial["seed"]
        elapsed = raw_trial["wall_time"]
        e1, e2 = raw_trial["routes"] or (None, None)
        assignment = raw_trial["assignment"]
        alns_info = raw_trial["alns_info"]
        if raw_trial["error"] is not None:
            trials.append({
                "seed": seed,
                "wall_time": elapsed,
                "objective": None,
                "valid": False,
                "iterations": None,
                "termination_reason": "error",
                "audit_errors": [raw_trial["error"]],
            })
            continue
        audit = None
        if e1 is not None:
            audit = audit_exact_solution(
                problem, 4, e1, e2, assignment=assignment,
                objective=alns_info.get("objective"), tfp=9999.0,
            )
        valid = bool(audit and audit["valid"])
        objective = (
            float(audit["route_objective"])
            if valid and audit.get("route_objective") is not None
            else None
        )
        trials.append({
            "seed": int(seed),
            "wall_time": elapsed,
            "objective": objective,
            "valid": valid,
            "iterations": alns_info.get("iterations"),
            "termination_reason": alns_info.get("termination_reason"),
            "audit_errors": [] if audit is None else audit.get("errors", []),
        })
        if objective is not None and (
                best is None or objective < best["objective"] - 1e-9):
            best = {
                "routes": (e1, e2),
                "assignment": dict(assignment),
                "objective": objective,
                "seed": int(seed),
            }
    return {
        "best": best,
        "wall_time": time.perf_counter() - total_started,
        "workers": workers,
        "trials": trials,
    }


def _use_better_m4_alns_incumbent(info, routes, candidate):
    """Keep an audited ALNS incumbent if the limited MIP returns worse."""
    best = candidate.get("best") if candidate else None
    if best is None or info.get("proven_optimal"):
        return info, routes
    exact_objective = info.get("objective")
    if exact_objective is not None and exact_objective <= best["objective"] + 1e-6:
        return info, routes

    info["exact_incumbent_before_alns_fallback"] = exact_objective
    info["objective"] = best["objective"]
    info["assignment"] = dict(best["assignment"])
    info["solution_type"] = "alns_warm_incumbent"
    info["incumbent_source"] = "audited_m4_alns"
    bound = info.get("best_bound")
    if bound is not None and math.isfinite(float(bound)):
        denominator = max(abs(best["objective"]), 1e-12)
        info["mip_gap"] = max(0.0, (best["objective"] - float(bound)) / denominator)
    return info, best["routes"]


def _record(model, scale_group, scenario, instance, info, elapsed, problem):
    return {
        "model": model,
        "scale_group": scale_group,
        "scenario": scenario,
        "instance": instance,
        "customers": len(problem.customers),
        "satellites": len(problem.satellites),
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
        "assignment": info.get("assignment"),
        "assignment_partitions_total": info.get("assignment_partitions_total"),
        "assignment_partitions_evaluated": info.get(
            "assignment_partitions_evaluated"),
        "assignment_partitions_resolved": info.get(
            "assignment_partitions_resolved"),
        "assignment_partitions_proven_feasible": info.get(
            "assignment_partitions_proven_feasible"),
        "assignment_partitions_proven_infeasible": info.get(
            "assignment_partitions_proven_infeasible"),
        "assignment_partition_retries": info.get(
            "assignment_partition_retries"),
        "partition_relaxation_screened": info.get("partition_relaxation_screened"),
        "partition_relaxation_pruned": info.get("partition_relaxation_pruned"),
        "assignment_partitions_unresolved": info.get(
            "assignment_partitions_unresolved"),
        "weakest_unresolved_bound": info.get("weakest_unresolved_bound"),
        "weakest_unresolved_assignment": info.get(
            "weakest_unresolved_assignment"),
        "weakest_unresolved_status": info.get("weakest_unresolved_status"),
        "certificate_type": (info.get("certificate") or {}).get("type"),
        "external_m4_incumbent": info.get("external_m4_incumbent"),
        "incumbent_source": info.get("incumbent_source"),
        "route_audit_valid": info.get("route_audit_valid"),
        "route_objective": info.get("route_objective"),
        "route_objective_delta": info.get("route_objective_delta"),
        "route_audit_errors": info.get("route_audit_errors"),
        "model_rows": info.get("model_rows"),
        "model_columns": info.get("model_columns"),
        "model_binary": info.get("model_binary"),
        "model_nonzeros": info.get("model_nonzeros"),
        "formulation_stats": info.get("formulation_stats"),
        "node_count": info.get("node_count"),
        "iteration_count": info.get("iteration_count"),
        "work": info.get("work"),
        "time_limit": info.get("time_limit"),
        "work_limit": info.get("work_limit"),
        "m4_alns_warm_attempted": info.get("m4_alns_warm_attempted"),
        "m4_alns_warm_objective": info.get("m4_alns_warm_objective"),
        "m4_alns_warm_seed": info.get("m4_alns_warm_seed"),
        "m4_alns_warm_wall_time": info.get("m4_alns_warm_wall_time"),
        "m4_alns_warm_workers": info.get("m4_alns_warm_workers"),
        "m4_alns_warm_trials": info.get("m4_alns_warm_trials"),
        "exact_incumbent_before_alns_fallback": info.get(
            "exact_incumbent_before_alns_fallback"),
        **_fleet_fields(problem),
    }


def solve_one(model, problem, time_limit, warm_start_routes=None,
              gurobi_params=None, m4_formulation_options=None,
              m7_warm_start=None):
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
            warm_start_routes=warm_start_routes,
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
            return_incumbent_on_time_limit=True,
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
    elif model == 7:
        m7_warm_routes = None
        m7_warm_assignment = None
        m7_warm_objective = None
        if m7_warm_start is not None:
            (m7_warm_routes, m7_warm_assignment,
             m7_warm_objective) = m7_warm_start
        r1, r2, assignment, info = solve_m7(
            problem,
            vehicles_per_satellite=vps,
            TFp=9999.0,
            time_limit=time_limit,
            verbose=False,
            return_info=True,
            return_incumbent_on_time_limit=True,
            gurobi_params=gurobi_params,
            m4_formulation_options=m4_formulation_options,
            warm_start_routes=m7_warm_routes,
            warm_start_assignment=m7_warm_assignment,
            warm_start_objective=m7_warm_objective,
        )
        info["assignment"] = assignment
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
    parser.add_argument(
        "--m4-alns-warm-time", type=float, default=0.0,
        help=(
            "Seconds per ALNS seed used to build an audited M4 MIP start. "
            "Disabled by default (0)."
        ),
    )
    parser.add_argument(
        "--m4-alns-warm-seeds", nargs="+", type=int, default=[41, 42, 43],
        help="Random seeds for the optional M4 ALNS warm start.",
    )
    parser.add_argument(
        "--m4-alns-warm-workers", type=int, default=3,
        help=(
            "Processes used for independent M4 ALNS seeds. Use the number "
            "of seeds to reduce warm-start wall time (default 3); use 1 "
            "to reproduce the historical sequential protocol."
        ),
    )
    parser.add_argument(
        "--m4-alns-warm-min-customers", type=int, default=12,
        help="Only run the optional M4 ALNS warm start at or above this size.",
    )
    parser.add_argument("--output-dir", default="output/results/mainline_small_exact")
    args = parser.parse_args()
    if args.time_limit is not None and args.time_limit <= 0.0:
        parser.error("--time-limit must be positive")
    if args.m4_alns_warm_time < 0.0:
        parser.error("--m4-alns-warm-time must be non-negative")
    if args.m4_alns_warm_min_customers < 0:
        parser.error("--m4-alns-warm-min-customers must be non-negative")
    if args.m4_alns_warm_workers <= 0:
        parser.error("--m4-alns-warm-workers must be positive")
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
    fields = ["model", "scale_group", "scenario", "instance", "customers",
              "satellites", "status",
              "proven_optimal", "solution_type", "objective", "best_bound",
              "mip_gap", "runtime", "wall_time", "sol_count",
              "formulation", "proof_scope", "transshipment_count",
              "assignment", "assignment_partitions_total",
              "assignment_partitions_evaluated",
              "assignment_partitions_resolved",
              "assignment_partitions_proven_feasible",
              "assignment_partitions_proven_infeasible",
              "assignment_partition_retries",
              "partition_relaxation_screened", "partition_relaxation_pruned",
              "assignment_partitions_unresolved",
              "weakest_unresolved_bound",
              "weakest_unresolved_assignment",
              "weakest_unresolved_status", "certificate_type",
              "external_m4_incumbent", "incumbent_source",
              "route_audit_valid", "route_objective",
              "route_objective_delta", "route_audit_errors",
              "model_rows", "model_columns", "model_binary",
              "model_nonzeros", "formulation_stats", "node_count",
              "iteration_count", "work", "time_limit", "work_limit",
              "m4_alns_warm_attempted", "m4_alns_warm_objective",
              "m4_alns_warm_seed", "m4_alns_warm_wall_time",
              "m4_alns_warm_workers",
              "m4_alns_warm_trials",
              "exact_incumbent_before_alns_fallback",
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
    audit_failures = [
        (rec["instance"], rec["model"], rec.get("route_audit_errors"))
        for rec in records
        if rec.get("proven_optimal") and rec.get("model") in (1, 2, 3, 4)
        and rec.get("route_audit_valid") is not True
    ]
    if audit_failures:
        print(f"Route-audit failures: {audit_failures}")
        sys.exit(3)


def run_instance(path, scale_group, scenario, args, fleet, gurobi_params,
                 m4_formulation_options, records, jsonl_path):
    """Run all requested models for one organized dataset instance."""
    problem = _problem(path, fleet)
    # Cache nested-model incumbents for exact MIP starts.
    m1_routes = None
    m3_routes = None
    # A solved M4 route is a zero-handoff feasible M7 incumbent.  Preserve the
    # exact objective and assignment as well as the paths so a later M7 run on
    # the same instance cannot return a worse time-limited solution.
    m4_incumbent = None
    for model in args.models:
        started = time.perf_counter()
        m4_alns_candidate = None
        warm_start = (
            m1_routes if model == 2
            else m3_routes if model in (4, 6)
            else None
        )
        try:
            if (model == 4
                    and getattr(args, "m4_alns_warm_time", 0.0) > 0.0
                    and len(problem.customers) >= getattr(
                        args, "m4_alns_warm_min_customers", 12)):
                m4_alns_candidate = _build_m4_alns_warm_start(
                    problem,
                    args.m4_alns_warm_time,
                    args.m4_alns_warm_seeds,
                    args.m4_alns_warm_workers,
                )
                if m4_alns_candidate["best"] is not None:
                    warm_start = m4_alns_candidate["best"]["routes"]
            info, routes = solve_one(
                model, problem, args.time_limit,
                warm_start_routes=warm_start,
                gurobi_params=gurobi_params,
                m4_formulation_options=m4_formulation_options,
                m7_warm_start=m4_incumbent if model == 7 else None,
            )
            if model == 4:
                info["m4_alns_warm_attempted"] = m4_alns_candidate is not None
                if m4_alns_candidate is not None:
                    best = m4_alns_candidate["best"]
                    info["m4_alns_warm_objective"] = (
                        None if best is None else best["objective"])
                    info["m4_alns_warm_seed"] = (
                        None if best is None else best["seed"])
                    info["m4_alns_warm_wall_time"] = m4_alns_candidate["wall_time"]
                    info["m4_alns_warm_workers"] = m4_alns_candidate["workers"]
                    info["m4_alns_warm_trials"] = m4_alns_candidate["trials"]
                    info, routes = _use_better_m4_alns_incumbent(
                        info, routes, m4_alns_candidate)
            if model == 1 and routes[0] is not None:
                m1_routes = routes
            if model == 3 and routes[0] is not None:
                m3_routes = routes
            if (model == 4 and routes[0] is not None
                    and info.get("assignment") is not None
                    and info.get("objective") is not None):
                m4_incumbent = (
                    routes, dict(info["assignment"]), float(info["objective"])
                )
            if model in (1, 2, 3, 4) and routes[0] is not None:
                audit = audit_exact_solution(
                    problem, model, routes[0], routes[1],
                    assignment=info.get("assignment"),
                    objective=info.get("objective"), tfp=9999.0,
                )
                info["route_audit_valid"] = audit["valid"]
                info["route_objective"] = audit["route_objective"]
                info["route_objective_delta"] = audit["objective_delta"]
                info["route_audit_errors"] = audit["errors"]
            rec = _record(
                model, scale_group, scenario, path.stem, info,
                time.perf_counter() - started, problem,
            )
        except Exception as exc:  # keep the batch auditable
            rec = {
                "model": model, "scale_group": scale_group,
                "scenario": scenario, "instance": path.stem,
                "customers": len(problem.customers),
                "satellites": len(problem.satellites),
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
