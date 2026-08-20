"""Run the certified single-satellite M6 branch-price solver.

All shipped 5- and 10-customer benchmark instances currently have one
satellite.  This script invokes the corresponding full-exact entry point;
unlike generic two-state pool diagnostics, ``proven_optimal=True`` here means
the branch-price tree and universal single-satellite state proof both closed.
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
from models.model_6.certified_time_expansion import make_m6_satellite_program
from models.model_6.single_satellite_exact_bp import solve_single_satellite_exact_branch_price


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", default="10_customers")
    parser.add_argument("--instance", default="C101")
    parser.add_argument("--seed-time-limit", type=float, default=120.0)
    parser.add_argument("--pricing-time-limit", type=float, default=60.0)
    parser.add_argument("--pricing-mip-focus", type=int, choices=range(4))
    parser.add_argument("--pricing-cuts", type=int, choices=range(-1, 4))
    parser.add_argument("--pricing-heuristics", type=float)
    parser.add_argument("--pricing-presolve", type=int, choices=range(-1, 3))
    parser.add_argument("--pricing-solution-pool-size", type=int, default=1)
    parser.add_argument("--pricing-mode", choices=("minimize", "negative_existence"), default="minimize")
    parser.add_argument("--pricing-subtour-strengthening-size", type=int, choices=(0, 2, 3), default=0)
    parser.add_argument("--exclude-existing-pricing-programs", action="store_true")
    parser.add_argument("--max-duplicate-reprices", type=int, default=8)
    parser.add_argument("--max-nodes", type=int, default=512)
    parser.add_argument("--max-cg-iterations", type=int, default=120)
    parser.add_argument("--resume-program-pool", type=Path)
    parser.add_argument("--save-program-pool", type=Path)
    args = parser.parse_args()

    path = ROOT / "data" / args.scale / f"{args.instance}.csv"
    if not path.exists():
        raise SystemExit(f"instance not found: {path}")
    problem = _problem(path, _fleet())
    pricing_gurobi_params = {
        name: value
        for name, value in {
            "MIPFocus": args.pricing_mip_focus,
            "Cuts": args.pricing_cuts,
            "Heuristics": args.pricing_heuristics,
            "Presolve": args.pricing_presolve,
        }.items()
        if value is not None
    }
    initial_programs = ()
    if args.resume_program_pool is not None:
        initial_programs = _load_program_pool(args.resume_program_pool, problem, args.scale, args.instance)
    result = solve_single_satellite_exact_branch_price(
        problem,
        problem.fleet.e2_per_sat_count,
        seed_time_limit=args.seed_time_limit,
        pricing_time_limit=args.pricing_time_limit,
        pricing_gurobi_params=pricing_gurobi_params or None,
        pricing_solution_pool_size=args.pricing_solution_pool_size,
        pricing_mode=args.pricing_mode,
        pricing_subtour_strengthening_size=args.pricing_subtour_strengthening_size,
        exclude_existing_pricing_programs=args.exclude_existing_pricing_programs,
        max_duplicate_reprices=args.max_duplicate_reprices,
        max_nodes=args.max_nodes,
        max_cg_iterations_per_node=args.max_cg_iterations,
        initial_programs=initial_programs,
    )
    if args.save_program_pool is not None:
        _save_program_pool(
            args.save_program_pool,
            result.programs,
            args.scale,
            args.instance,
        )
    output = {
        "instance": f"{args.scale}/{args.instance}",
        "info": result.info,
        "e1_routes": result.e1_routes,
        "e2_routes_by_sat": result.e2_routes_by_sat,
        "pricing_gurobi_params": pricing_gurobi_params,
        "pricing_solution_pool_size": args.pricing_solution_pool_size,
        "pricing_mode": args.pricing_mode,
        "pricing_subtour_strengthening_size": args.pricing_subtour_strengthening_size,
        "exclude_existing_pricing_programs": args.exclude_existing_pricing_programs,
        "max_duplicate_reprices": args.max_duplicate_reprices,
        "initial_program_count": len(initial_programs),
        "saved_program_count": len(result.programs),
        "saved_program_pool": str(args.save_program_pool) if args.save_program_pool else None,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


def _load_program_pool(path: Path, problem, scale: str, instance: str):
    """Load and independently revalidate a previous run's columns."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "m6_single_satellite_program_pool_v1":
        raise ValueError("not an M6 single-satellite program-pool checkpoint")
    if payload.get("scale") != scale or payload.get("instance") != instance:
        raise ValueError("program-pool checkpoint is for a different benchmark instance")
    return tuple(
        make_m6_satellite_program(
            problem,
            item["sat_id"],
            tuple(tuple(route) for route in item["routes"]),
            ready_time=float(item["ready_time"]),
            pickup_ready_time=float(item["pickup_ready_time"]),
        )
        for item in payload.get("programs", ())
    )


def _save_program_pool(path: Path, programs, scale: str, instance: str) -> None:
    """Persist only independently validated state-scoped program data."""

    payload = {
        "schema": "m6_single_satellite_program_pool_v1",
        "scale": scale,
        "instance": instance,
        "programs": [
            {
                "sat_id": program.sat_id,
                "ready_time": program.ready_time,
                "pickup_ready_time": program.pickup_ready_time,
                "routes": [list(route) for route in program.routes],
            }
            for program in programs
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
