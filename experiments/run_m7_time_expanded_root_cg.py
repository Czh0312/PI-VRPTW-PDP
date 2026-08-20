#!/usr/bin/env python3
"""Run the certified *root-LP* column-generation attempt for Model 7.

This script is deliberately conservative about wording.  It can certify that
the global M7 LP relaxation is priced complete at the root, but it does not
build a branch tree and therefore never certifies an integer optimum.  It is
the intended 50-customer diagnostic/engineering entry point for the new
time-expanded route architecture.

Example
-------
PYTHONPYCACHEPREFIX=/tmp/2e_vrptw_pycache .venv/bin/python \\
    experiments/run_m7_time_expanded_root_cg.py \\
    --scale 50_customers --instance C101 --max-iterations 10 \\
    --pricing-time-limit 10
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.instance import ProblemInstance
from models.model_7.time_expanded_column_generation import (
    singleton_seed_columns,
    solve_m7_time_expanded_root_cg,
)
from models.model_7.time_expanded_route_master import solve_m7_time_expanded_pool
from models.model_7.certified_time_expansion import (
    enumerate_canonical_ready_states,
    enumerate_ready_states,
    validate_m7_time_expansion_witness,
)
from utils.data_loader import parse_dataset


def _load_problem(scale: str, instance: str) -> ProblemInstance:
    fleet = json.loads((ROOT / "config" / "fleet_config.json").read_text())
    path = ROOT / "data" / scale / f"{instance}.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    nodes, _ = parse_dataset(str(path))
    problem = ProblemInstance(nodes, fleet, speed=float(fleet.get("speed", 1.0)))
    problem.aggregate_demands_from_pre_assignment()
    return problem


def _json_safe(value: Any) -> Any:
    """Keep audit files standards-compliant even if a pricing bound is inf."""

    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="M7 time-expanded globally coupled root-LP column generation"
    )
    parser.add_argument("--scale", default="50_customers")
    parser.add_argument("--instance", default="C101")
    parser.add_argument("--max-iterations", type=int, default=10)
    parser.add_argument("--pricing-time-limit", type=float, default=10.0)
    parser.add_argument("--pricing-pool-solutions", type=int, default=1)
    parser.add_argument(
        "--lp-method", type=int,
        help=("Optional Gurobi LP Method for the RMP (for example 2 for "
              "barrier).  This alters the dual point used for pricing, not "
              "the certificate criterion."),
    )
    parser.add_argument(
        "--lp-crossover", type=int,
        help="Optional Gurobi Crossover parameter for the RMP.",
    )
    parser.add_argument(
        "--alns-seed-time", type=float, default=0.0,
        help="Run M7 ALNS first and inject its E2 routes as valid seed columns.",
    )
    parser.add_argument("--alns-seed-iterations", type=int, default=1000)
    parser.add_argument(
        "--m1-exact-seed-time", type=float, default=0.0,
        help=("Run the certified M1 arc-flow solver first and inject its E2 "
              "paths as M7 route-column seeds.  This is an incumbent seed, "
              "not an M7 optimality certificate."),
    )
    parser.add_argument(
        "--restricted-ip-time", type=float, default=0.0,
        help=("After root CG, solve the current finite-column integer master "
              "for this many seconds to obtain a non-certified feasible upper bound."),
    )
    parser.add_argument(
        "--singleton-seed", action="store_true",
        help="Seed every physical singleton path (larger initial master).",
    )
    parser.add_argument(
        "--resume-pool",
        help="JSON audit from an earlier root-CG run; reuse its raw route paths.",
    )
    parser.add_argument(
        "--canonical-ready-states",
        action="store_true",
        help=(
            "Fresh run only: merge per-satellite ready states with exactly "
            "equal h values.  A resulting checkpoint records this mode and "
            "will resume it automatically."
        ),
    )
    parser.add_argument("--output-dir", default="results/m7_time_expanded_root_cg")
    args = parser.parse_args()

    problem = _load_problem(args.scale, args.instance)
    if args.canonical_ready_states and args.resume_pool:
        parser.error(
            "--canonical-ready-states starts a new state-indexed family; "
            "omit --resume-pool (canonical checkpoints resume their recorded mode automatically)"
        )
    ready_state_mode = "canonical_exact_time" if args.canonical_ready_states else "full_prefix"
    selected_ready_states = (
        enumerate_canonical_ready_states(problem)
        if args.canonical_ready_states
        else enumerate_ready_states(problem)
    )
    seed_metadata: dict[str, Any] | None = None
    if args.resume_pool:
        resume_data = json.loads(Path(args.resume_pool).read_text(encoding="utf-8"))
        resume_mode = str(resume_data.get("ready_state_mode", "full_prefix"))
        if resume_mode not in {"full_prefix", "canonical_exact_time"}:
            raise ValueError(
                f"resume audit has unknown ready_state_mode {resume_mode!r}"
            )
        ready_state_mode = resume_mode
        selected_ready_states = (
            enumerate_canonical_ready_states(problem)
            if resume_mode == "canonical_exact_time"
            else enumerate_ready_states(problem)
        )
        initial = list(resume_data.get("raw_route_columns", ()))
        if not initial:
            raise ValueError(
                "resume audit does not contain raw_route_columns; rerun with "
                "the current root-CG driver to create a resumable checkpoint"
            )
    else:
        initial = list(singleton_seed_columns(problem)) if args.singleton_seed else []

    if args.alns_seed_time > 0:
        from models.model_7.route_pool import solve_alns as solve_m7_alns

        alns_e1, alns_e2, alns_assignment = solve_m7_alns(
            problem,
            problem.fleet.e2_per_sat_count,
            max_iterations=args.alns_seed_iterations,
            max_time=args.alns_seed_time,
            seed=42,
            verbose=False,
        )
        if alns_e2:
            alns_columns = [
                {"sat_id": sat_id, "path": tuple(route), "source": "m7_alns_seed"}
                for sat_id, routes in alns_e2.items() for route in routes
            ]
            initial.extend(alns_columns)
            seed_metadata = {
                "status": "available",
                "e1_routes": len(alns_e1 or ()),
                "e2_routes": len(alns_columns),
                "assigned_customers": len(alns_assignment or {}),
            }
        else:
            seed_metadata = {"status": "no_alns_solution"}

    m1_seed_metadata: dict[str, Any] | None = None
    m1_imported: dict[str, Any] | None = None
    if args.m1_exact_seed_time > 0:
        from models.model_1.solver import solve_mip as solve_m1_mip
        from models.model_7.m1_incumbent_import import (
            import_m1_solution_as_m7_time_expanded_incumbent,
        )
        from utils.task_builder import build_tasks_for_model

        e1_tasks, e2_tasks = build_tasks_for_model(problem, 1)
        m1_e1, m1_e2, m1_info = solve_m1_mip(
            problem,
            e1_tasks,
            e2_tasks,
            problem.fleet.e2_per_sat_count,
            verbose=False,
            time_limit=args.m1_exact_seed_time,
            return_info=True,
        )
        if m1_e1 and m1_e2:
            try:
                # This checked conversion verifies the whole M1 solution,
                # maps each path to its one induced ready state, and proves
                # only that it is a feasible M7 incumbent (never an M7
                # optimum, even if M1 itself was solved to optimality).
                m1_imported = import_m1_solution_as_m7_time_expanded_incumbent(
                    problem,
                    m1_e1,
                    m1_e2,
                    ready_states=selected_ready_states,
                    source_info=m1_info,
                )
                initial.extend(m1_imported["route_columns"])
                m1_seed_metadata = {
                    "status": "validated_m7_incumbent",
                    "solver_info": m1_info,
                    "e2_routes_added": len(m1_imported["route_columns"]),
                    "m7_feasible_upper_bound": m1_imported["m7_feasible_upper_bound"],
                    "proof_scope": m1_imported["proof_scope"],
                    "validation": m1_imported["validation"],
                }
            except ValueError as exc:
                m1_seed_metadata = {
                    "status": "rejected_by_m7_import_validation",
                    "solver_info": m1_info,
                    "error": str(exc),
                }
        else:
            m1_seed_metadata = {
                "status": "no_m1_incumbent",
                "solver_info": m1_info,
            }
    result = solve_m7_time_expanded_root_cg(
        problem,
        problem.fleet.e2_per_sat_count,
        initial_route_columns=initial,
        ready_states=(m1_imported["ready_states"] if m1_imported is not None else selected_ready_states),
        max_iterations=args.max_iterations,
        pricing_time_limit=args.pricing_time_limit,
        pricing_pool_solutions=args.pricing_pool_solutions,
        lp_method=args.lp_method,
        lp_crossover=args.lp_crossover,
    )
    result.update({
        "instance": f"{args.scale}/{args.instance}",
        "customers": len(problem.customers),
        "satellites": len(problem.satellites),
        "run_timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ready_state_mode": ready_state_mode,
        "ready_state_counts": {
            sat_id: len(items)
            for sat_id, items in (
                m1_imported["ready_states"]
                if m1_imported is not None
                else selected_ready_states
            ).items()
        },
    })
    if seed_metadata is not None:
        result["alns_seed"] = seed_metadata
    if m1_seed_metadata is not None:
        result["m1_arc_seed"] = m1_seed_metadata
    if m1_imported is not None:
        # The importer independently validates the full E1/E2 witness, so
        # this is a safe global M7 feasible upper bound.  It remains distinct
        # from a finite route-pool MIP's (non-certifying) optimum.
        result["global_feasible_upper_bound"] = m1_imported["m7_feasible_upper_bound"]
        result["global_feasible_upper_bound_source"] = "validated_m1_import"

    if args.restricted_ip_time > 0 and result.get("raw_route_columns"):
        restricted = solve_m7_time_expanded_pool(
            problem,
            result["raw_route_columns"],
            problem.fleet.e2_per_sat_count,
            ready_states=(m1_imported["ready_states"] if m1_imported is not None else selected_ready_states),
            time_limit=args.restricted_ip_time,
            verbose=False,
        )
        restricted_info = restricted["info"]
        result["restricted_ip"] = restricted_info
        if restricted_info.get("objective") is not None:
            # Every selected column is forward-validated and the master keeps
            # full E1 feasibility.  Only promote it to a global-feasible
            # upper bound after the independently reconstructed witness also
            # validates; a finite pool optimum is never an optimality proof.
            try:
                validate_m7_time_expansion_witness(
                    problem,
                    restricted["e1_routes"],
                    restricted["e2_routes"],
                )
                restricted_info["time_expansion_witness_valid"] = True
                existing_upper = result.get("global_feasible_upper_bound")
                if existing_upper is None or restricted_info["objective"] < existing_upper:
                    result["global_feasible_upper_bound"] = restricted_info["objective"]
                    result["global_feasible_upper_bound_source"] = "validated_restricted_pool"
            except Exception as exc:
                restricted_info["time_expansion_witness_valid"] = False
                restricted_info["time_expansion_witness_error"] = repr(exc)

    out_dir = ROOT / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"m7_root_cg_{args.scale}_{args.instance}_{stamp}.json"
    out_path.write_text(
        json.dumps(_json_safe(result), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"status: {result['status']}")
    print(f"root LP priced complete: {result['root_lp_priced_complete']}")
    print(f"global integer optimum proven: {result['proven_optimal']}")
    if result.get("root_lp_lower_bound") is not None:
        print(f"root LP lower bound: {result['root_lp_lower_bound']:.6f}")
    if result.get("global_feasible_upper_bound") is not None:
        print(f"restricted-pool feasible upper bound: "
              f"{result['global_feasible_upper_bound']:.6f}")
    print(f"raw route paths: {result.get('raw_columns', 0)}")
    print(f"audit: {out_path}")


if __name__ == "__main__":
    main()
