#!/usr/bin/env python3
"""Bounded strict branch-price attempt for the globally coupled M7 model.

This is the 50-customer experimental entry point for the certified M7
architecture.  It deliberately distinguishes a closed branch-price tree from
a bounded diagnostic run: an ``INCOMPLETE`` result is useful progress, not an
optimality claim.

Example
-------
PYTHONPYCACHEPREFIX=/tmp/2e_vrptw_pycache .venv/bin/python \\
    experiments/run_m7_time_expanded_branch_price.py \\
    --scale 50_customers --instance C101 --m1-seed-time 60 \\
    --max-nodes 128 --max-cg-iterations 30 --pricing-time-limit 10
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
from models.model_7.certified_time_expansion import (
    enumerate_canonical_ready_states,
    enumerate_ready_states,
)
from models.model_7.m1_incumbent_import import (
    import_m1_solution_as_m7_time_expanded_incumbent,
)
from models.model_7.time_expanded_branch_price import (
    solve_m7_time_expanded_branch_price,
)
from models.model_7.time_expanded_column_generation import singleton_seed_columns
from models.model_7.time_expanded_route_master import solve_m7_time_expanded_pool
from models.model_7.certified_time_expansion import validate_m7_time_expansion_witness
from utils.data_loader import parse_dataset
from utils.task_builder import build_tasks_for_model


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
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="M7 globally coupled external branch-price proof attempt"
    )
    parser.add_argument("--scale", default="50_customers")
    parser.add_argument("--instance", default="C101")
    parser.add_argument("--max-nodes", type=int, default=64)
    parser.add_argument("--max-cg-iterations", type=int, default=30)
    parser.add_argument("--pricing-time-limit", type=float, default=15.0)
    parser.add_argument("--pricing-pool-solutions", type=int, default=1)
    parser.add_argument(
        "--lp-method", type=int,
        help=("Optional Gurobi LP Method for every B&P node RMP (for example "
              "2 for barrier).  It changes the pricing dual point only."),
    )
    parser.add_argument(
        "--lp-crossover", type=int,
        help="Optional Gurobi Crossover parameter for every B&P node RMP.",
    )
    parser.add_argument(
        "--wall-time-limit", type=float,
        help="Maximum total seconds for this B&P attempt; returns INCOMPLETE safely.",
    )
    parser.add_argument(
        "--node-time-limit", type=float,
        help="Maximum seconds for one B&P node, including its pricing solves.",
    )
    parser.add_argument("--singleton-seed", action="store_true")
    parser.add_argument(
        "--resume-pool",
        help=("A root-CG or branch-price audit JSON whose raw route columns "
              "are used solely as an initial restricted pool."),
    )
    parser.add_argument(
        "--resume-tree",
        help=("A prior branch-price checkpoint.  It restores its pending "
              "branch nodes as well as the state-indexed route pool."),
    )
    parser.add_argument(
        "--canonical-ready-states",
        action="store_true",
        help=(
            "Fresh run only: merge ready states with exactly equal h values. "
            "Checkpoints record their state mode and resume it automatically."
        ),
    )
    parser.add_argument(
        "--m1-seed-time", type=float, default=0.0,
        help=("Run M1 first, validate/import its routes as an M7 incumbent, "
              "and pass only that validated cost as the B&P upper bound."),
    )
    parser.add_argument(
        "--seed-pool-ip-time", type=float, default=0.0,
        help=("Solve the current finite route pool before B&P and validate its "
              "E1/E2 witness as an additional incumbent upper bound.  This "
              "never makes the finite pool an optimality certificate."),
    )
    parser.add_argument("--output-dir", default="results/m7_time_expanded_branch_price")
    args = parser.parse_args()

    problem = _load_problem(args.scale, args.instance)
    if args.resume_pool and args.resume_tree:
        parser.error("--resume-pool and --resume-tree are mutually exclusive")
    resume_path = args.resume_tree or args.resume_pool
    if args.canonical_ready_states and resume_path:
        parser.error(
            "--canonical-ready-states starts a new state-indexed family; "
            "omit resume options (a canonical checkpoint resumes its mode automatically)"
        )
    ready_state_mode = "canonical_exact_time" if args.canonical_ready_states else "full_prefix"
    selected_ready_states = (
        enumerate_canonical_ready_states(problem)
        if args.canonical_ready_states
        else enumerate_ready_states(problem)
    )
    resume_state = None
    if resume_path:
        resume = json.loads(Path(resume_path).read_text(encoding="utf-8"))
        resume_mode = str(resume.get("ready_state_mode", "full_prefix"))
        if resume_mode not in {"full_prefix", "canonical_exact_time"}:
            raise ValueError(f"resume audit has unknown ready_state_mode {resume_mode!r}")
        ready_state_mode = resume_mode
        selected_ready_states = (
            enumerate_canonical_ready_states(problem)
            if resume_mode == "canonical_exact_time"
            else enumerate_ready_states(problem)
        )
        initial_columns = list(resume.get("raw_route_columns", ()))
        if not initial_columns:
            raise ValueError("resume audit contains no raw_route_columns")
        if args.resume_tree:
            if resume.get("method") != "time_expanded_external_branch_price":
                raise ValueError("--resume-tree requires a time-expanded branch-price checkpoint")
            if not resume.get("pending_nodes"):
                raise ValueError("branch-price checkpoint contains no pending_nodes")
            resume_state = resume
    elif args.singleton_seed:
        initial_columns = list(singleton_seed_columns(problem))
    else:
        initial_columns = []

    m1_metadata: dict[str, Any] | None = None
    imported = None
    if args.m1_seed_time > 0:
        from models.model_1.solver import solve_mip as solve_m1_mip

        e1_tasks, e2_tasks = build_tasks_for_model(problem, 1)
        m1_e1, m1_e2, m1_info = solve_m1_mip(
            problem,
            e1_tasks,
            e2_tasks,
            problem.fleet.e2_per_sat_count,
            verbose=False,
            time_limit=args.m1_seed_time,
            return_info=True,
        )
        if m1_e1 and m1_e2:
            try:
                imported = import_m1_solution_as_m7_time_expanded_incumbent(
                    problem,
                    m1_e1,
                    m1_e2,
                    ready_states=selected_ready_states,
                    source_info=m1_info,
                )
                initial_columns.extend(imported["route_columns"])
                m1_metadata = {
                    "status": "validated_m7_incumbent",
                    "solver_info": m1_info,
                    "m7_feasible_upper_bound": imported["m7_feasible_upper_bound"],
                    "e2_routes_added": len(imported["route_columns"]),
                    "proof_scope": imported["proof_scope"],
                }
            except ValueError as exc:
                m1_metadata = {
                    "status": "rejected_by_m7_import_validation",
                    "solver_info": m1_info,
                    "error": str(exc),
                }
        else:
            m1_metadata = {"status": "no_m1_incumbent", "solver_info": m1_info}

    effective_ready_states = (
        imported["ready_states"] if imported is not None else selected_ready_states
    )
    validated_incumbent = (
        float(imported["m7_feasible_upper_bound"])
        if imported is not None else None
    )
    validated_incumbent_source = "validated_m1_import" if imported is not None else None
    pool_seed_metadata: dict[str, Any] | None = None
    if args.seed_pool_ip_time > 0 and initial_columns:
        # A restricted-pool MIP may improve the incumbent substantially.  Its
        # status is never used as a global proof: only a separately rebuilt
        # route witness allows its objective to be supplied as a B&P upper
        # bound for fathoming nodes.
        pool = solve_m7_time_expanded_pool(
            problem,
            initial_columns,
            problem.fleet.e2_per_sat_count,
            ready_states=effective_ready_states,
            time_limit=args.seed_pool_ip_time,
            verbose=False,
        )
        pool_info = pool["info"]
        pool_seed_metadata = {"solver_info": pool_info}
        if pool_info.get("objective") is not None:
            try:
                validate_m7_time_expansion_witness(
                    problem, pool["e1_routes"], pool["e2_routes"],
                )
                pool_seed_metadata["time_expansion_witness_valid"] = True
                pool_cost = float(pool_info["objective"])
                if validated_incumbent is None or pool_cost < validated_incumbent:
                    validated_incumbent = pool_cost
                    validated_incumbent_source = "validated_restricted_pool"
            except Exception as exc:
                pool_seed_metadata["time_expansion_witness_valid"] = False
                pool_seed_metadata["time_expansion_witness_error"] = repr(exc)

    result = solve_m7_time_expanded_branch_price(
        problem,
        problem.fleet.e2_per_sat_count,
        initial_route_columns=initial_columns,
        ready_states=effective_ready_states,
        resume_state=resume_state,
        validated_incumbent_cost=validated_incumbent,
        max_nodes=args.max_nodes,
        max_cg_iterations_per_node=args.max_cg_iterations,
        pricing_time_limit=args.pricing_time_limit,
        pricing_pool_solutions=args.pricing_pool_solutions,
        lp_method=args.lp_method,
        lp_crossover=args.lp_crossover,
        wall_time_limit=args.wall_time_limit,
        node_time_limit=args.node_time_limit,
    )
    result.update({
        "instance": f"{args.scale}/{args.instance}",
        "customers": len(problem.customers),
        "satellites": len(problem.satellites),
        "run_timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ready_state_mode": ready_state_mode,
        "ready_state_counts": {
            sat_id: len(items)
            for sat_id, items in effective_ready_states.items()
        },
    })
    if m1_metadata is not None:
        result["m1_arc_seed"] = m1_metadata
    if pool_seed_metadata is not None:
        result["restricted_pool_seed"] = pool_seed_metadata
    if validated_incumbent_source is not None:
        result["validated_incumbent_seed_source"] = validated_incumbent_source

    out_dir = ROOT / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"m7_branch_price_{args.scale}_{args.instance}_{stamp}.json"
    out_path.write_text(
        json.dumps(_json_safe(result), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"status: {result['status']}")
    print(f"global integer optimum proven: {result['proven_optimal']}")
    print(f"processed nodes: {result['processed_nodes']}")
    if result.get("incumbent") is not None:
        print(f"validated incumbent/upper bound: {result['incumbent']:.6f}")
    print(f"audit: {out_path}")


if __name__ == "__main__":
    main()
