"""Run the audited M4 arc-incumbent → two-state fixed-grid B&P pipeline.

This command is a diagnostic/research entry point.  Its branch-price result
is explicitly limited to the state grid supplied by imported and optional
direct-singleton seed programs; it never prints or stores that result as a
global M4 optimum.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.run_mainline_small_exact import _fleet, _problem
from models.model_4.exact import solve_mip
from models.model_4.two_state_fixed_grid_branch_price import (
    solve_m4_two_state_fixed_grid_branch_price,
)
from models.model_4.two_state_program_import import import_m4_routes_as_two_state_programs
from models.model_4.two_state_seed_generation import generate_m4_direct_e1_singleton_programs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", default="10_customers")
    parser.add_argument("--instance", default="C101")
    parser.add_argument("--arc-time-limit", type=float, default=120.0)
    parser.add_argument("--max-nodes", type=int, default=128)
    parser.add_argument("--max-cg-iterations", type=int, default=30)
    parser.add_argument("--pricing-time-limit", type=float, default=10.0)
    parser.add_argument(
        "--add-direct-singletons",
        action="store_true",
        help="Safely enrich the seed grid; does not make it complete.",
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Validate/import the arc incumbent but do not run fixed-grid B&P.",
    )
    args = parser.parse_args()

    instance_path = ROOT / "data" / args.scale / f"{args.instance}.csv"
    if not instance_path.exists():
        raise SystemExit(f"instance not found: {instance_path}")
    problem = _problem(instance_path, _fleet())
    e1_routes, e2_routes, assignment, arc_info = solve_mip(
        problem,
        problem.fleet.e2_per_sat_count,
        time_limit=args.arc_time_limit,
        verbose=False,
        return_info=True,
    )
    result = {
        "instance": f"{args.scale}/{args.instance}",
        "arc_flow": {
            key: arc_info.get(key)
            for key in (
                "status", "proven_optimal", "solution_type", "objective",
                "best_bound", "mip_gap", "runtime", "sol_count",
            )
        },
        "proven_optimal": False,
    }
    if e1_routes is None or e2_routes is None:
        result["pipeline_status"] = "NO_ARC_INCUMBENT_TO_IMPORT"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    imported = import_m4_routes_as_two_state_programs(problem, e1_routes, e2_routes)
    programs = list(imported.programs)
    if args.add_direct_singletons:
        programs.extend(generate_m4_direct_e1_singleton_programs(problem))
    result["seed"] = {
        "imported_programs": len(imported.programs),
        "total_programs": len(programs),
        "assignment_count": len(imported.assignment),
        "e1_witness_feasible": imported.e1_witness.feasible,
        "states": [
            {
                "satellite": program.sat_id,
                "delivery_ready_time": program.ready_time,
                "pickup_ready_time": program.pickup_ready_time,
                "routes": len(program.routes),
                "customers": len(program.customers),
            }
            for program in programs
        ],
    }
    if args.seed_only:
        result["pipeline_status"] = "SEED_VALIDATED_ONLY"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    bcp = solve_m4_two_state_fixed_grid_branch_price(
        problem,
        programs,
        problem.fleet.e2_per_sat_count,
        max_nodes=args.max_nodes,
        max_cg_iterations_per_node=args.max_cg_iterations,
        pricing_time_limit=args.pricing_time_limit,
    )
    result["pipeline_status"] = bcp.status
    result["fixed_grid_branch_price"] = {
        "status": bcp.status,
        "fixed_grid_proven_optimal": bcp.fixed_grid_proven_optimal,
        "proven_optimal": bcp.proven_optimal,
        "proof_scope": bcp.proof_scope,
        "objective": bcp.objective,
        "nodes_processed": bcp.nodes_processed,
        "columns": len(bcp.columns),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
