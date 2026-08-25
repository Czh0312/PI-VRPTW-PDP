"""Run the certified single-satellite M4 branch-price solver.

All shipped 5- and 10-customer benchmark instances currently have one
satellite.  This script invokes the corresponding full-exact entry point;
unlike generic two-state pool diagnostics, ``proven_optimal=True`` here means
the branch-price tree and universal single-satellite state proof both closed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.run_mainline_small_exact import _fleet, _problem
from models.model_4.alns_solver import solve_alns as solve_m4_alns
from models.model_4.certified_time_expansion import make_m4_satellite_program
from models.model_4.single_satellite_exact_bp import (
    solve_single_satellite_direct_program_mip,
    solve_single_satellite_exact_branch_price,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", default="10_customers")
    parser.add_argument("--instance", default="C101")
    parser.add_argument("--seed-time-limit", type=float, default=120.0)
    parser.add_argument(
        "--direct-program-mip", action="store_true",
        help="Solve the eligible one-satellite case directly as one complete E2 program MIP.",
    )
    parser.add_argument(
        "--direct-time-limit", type=float, default=120.0,
        help="Gurobi limit for --direct-program-mip.",
    )
    parser.add_argument(
        "--direct-no-time-limit", action="store_true",
        help="Leave Gurobi TimeLimit unlimited for --direct-program-mip.",
    )
    parser.add_argument(
        "--direct-fixed-vehicle-count", type=int,
        help=(
            "Restrict the direct program MIP to exactly this many used E2 vehicles. "
            "The resulting bound is for that partition only."
        ),
    )
    parser.add_argument(
        "--direct-warm-start-alns-time", type=float, default=None,
        help="For --direct-program-mip, generate a complete ALNS incumbent for this many seconds.",
    )
    parser.add_argument("--direct-warm-start-alns-seed", type=int, default=42)
    parser.add_argument(
        "--direct-warm-start-result", type=Path,
        help="Load E2 routes from a saved result JSON and revalidate them as a MIP start.",
    )
    parser.add_argument("--pricing-time-limit", type=float, default=60.0)
    parser.add_argument("--pricing-mip-focus", type=int, choices=range(4))
    parser.add_argument("--pricing-cuts", type=int, choices=range(-1, 4))
    parser.add_argument("--pricing-heuristics", type=float)
    parser.add_argument("--pricing-presolve", type=int, choices=range(-1, 3))
    parser.add_argument(
        "--pricing-gurobi-param", action="append", default=[], metavar="NAME=VALUE",
        help="Repeatable additional Gurobi parameter; VALUE is parsed as JSON when possible.",
    )
    parser.add_argument("--pricing-solution-pool-size", type=int, default=1)
    parser.add_argument("--pricing-mode", choices=("minimize", "negative_existence"), default="minimize")
    parser.add_argument("--pricing-subtour-strengthening-size", type=int, choices=(0, 2, 3), default=0)
    parser.add_argument("--exclude-existing-pricing-programs", action="store_true")
    parser.add_argument("--max-duplicate-reprices", type=int, default=8)
    parser.add_argument("--max-nodes", type=int, default=512)
    parser.add_argument("--max-cg-iterations", type=int, default=120)
    parser.add_argument("--resume-program-pool", type=Path)
    parser.add_argument("--save-program-pool", type=Path)
    parser.add_argument(
        "--output-file", type=Path,
        help="Optional JSON result path. Useful for long branch-price probes.",
    )
    args = parser.parse_args()

    path = ROOT / "data" / args.scale / f"{args.instance}.csv"
    if not path.exists():
        raise SystemExit(f"instance not found: {path}")
    problem = _problem(path, _fleet())
    if args.direct_fixed_vehicle_count is not None:
        if not args.direct_program_mip:
            raise SystemExit("--direct-fixed-vehicle-count requires --direct-program-mip")
        if not 1 <= args.direct_fixed_vehicle_count <= problem.fleet.e2_per_sat_count:
            raise SystemExit(
                "--direct-fixed-vehicle-count must be between 1 and "
                f"{problem.fleet.e2_per_sat_count}"
            )
    if args.direct_no_time_limit and not args.direct_program_mip:
        raise SystemExit("--direct-no-time-limit requires --direct-program-mip")
    direct_time_limit = None if args.direct_no_time_limit else args.direct_time_limit
    if (args.direct_warm_start_alns_time is not None
            and args.direct_warm_start_result is not None):
        raise SystemExit(
            "use only one of --direct-warm-start-alns-time and --direct-warm-start-result"
        )
    if args.direct_warm_start_alns_time is not None and not args.direct_program_mip:
        raise SystemExit("--direct-warm-start-alns-time requires --direct-program-mip")
    if args.direct_warm_start_result is not None and not args.direct_program_mip:
        raise SystemExit("--direct-warm-start-result requires --direct-program-mip")
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
    for item in args.pricing_gurobi_param:
        if "=" not in item:
            raise SystemExit(f"--pricing-gurobi-param must be NAME=VALUE, got {item!r}")
        name, raw_value = item.split("=", 1)
        try:
            pricing_gurobi_params[name] = json.loads(raw_value)
        except json.JSONDecodeError:
            pricing_gurobi_params[name] = raw_value
    initial_programs = ()
    if args.resume_program_pool is not None:
        initial_programs = _load_program_pool(args.resume_program_pool, problem, args.scale, args.instance)
    direct_warm_routes = None
    direct_warm_start_info = None
    if args.direct_warm_start_result is not None:
        warm_path = (
            args.direct_warm_start_result
            if args.direct_warm_start_result.is_absolute()
            else ROOT / args.direct_warm_start_result
        )
        warm_payload = json.loads(warm_path.read_text(encoding="utf-8"))
        expected_instance = f"{args.scale}/{args.instance}"
        if warm_payload.get("instance") != expected_instance:
            raise SystemExit(
                f"warm-start result belongs to {warm_payload.get('instance')!r}, "
                f"not {expected_instance!r}"
            )
        candidate_routes = warm_payload.get("e2_routes_by_sat")
        if not isinstance(candidate_routes, dict) or not any(candidate_routes.values()):
            raise SystemExit(f"warm-start result has no E2 route witness: {warm_path}")
        direct_warm_routes = candidate_routes
        direct_warm_start_info = {
            "source": "saved_result",
            "path": str(warm_path.resolve()),
            "available": direct_warm_routes is not None,
        }
    elif args.direct_program_mip and args.direct_warm_start_alns_time is not None:
        warm_started = time.perf_counter()
        e1_routes, e2_routes, assignment = solve_m4_alns(
            problem,
            problem.fleet.e2_per_sat_count,
            max_time=args.direct_warm_start_alns_time,
            seed=args.direct_warm_start_alns_seed,
            verbose=False,
        )
        direct_warm_routes = e2_routes if e1_routes and e2_routes and assignment else None

        direct_warm_start_info = {
            "source": "m4_alns",
            "time_limit": args.direct_warm_start_alns_time,
            "seed": args.direct_warm_start_alns_seed,
            "wall_time": time.perf_counter() - warm_started,
            "available": direct_warm_routes is not None,
        }

    if direct_warm_start_info is not None:
        warm_route_count = None
        warm_start_compatible = direct_warm_routes is not None
        if direct_warm_routes is not None:
            warm_route_count = sum(
                1 for routes in direct_warm_routes.values() for route in routes if route
            )
            if (args.direct_fixed_vehicle_count is not None
                    and warm_route_count != args.direct_fixed_vehicle_count):
                warm_start_compatible = False
        direct_warm_start_info["route_count"] = warm_route_count
        direct_warm_start_info["compatible_with_fixed_vehicle_count"] = warm_start_compatible
        if not warm_start_compatible:
            direct_warm_routes = None

    if args.direct_program_mip:
        if initial_programs:
            raise SystemExit("--resume-program-pool is not applicable to --direct-program-mip")
        result = solve_single_satellite_direct_program_mip(
            problem,
            problem.fleet.e2_per_sat_count,
            time_limit=direct_time_limit,
            gurobi_params=pricing_gurobi_params or None,
            subtour_strengthening_size=args.pricing_subtour_strengthening_size,
            warm_start_e2_routes_by_sat=direct_warm_routes,
            required_vehicle_count=args.direct_fixed_vehicle_count,
        )
    else:
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
        "direct_program_mip": args.direct_program_mip,
        "direct_time_limit": direct_time_limit if args.direct_program_mip else None,
        "direct_fixed_vehicle_count": (
            args.direct_fixed_vehicle_count if args.direct_program_mip else None
        ),
        "direct_warm_start": direct_warm_start_info,
        "pricing_solution_pool_size": args.pricing_solution_pool_size,
        "pricing_mode": args.pricing_mode,
        "pricing_subtour_strengthening_size": args.pricing_subtour_strengthening_size,
        "exclude_existing_pricing_programs": args.exclude_existing_pricing_programs,
        "max_duplicate_reprices": args.max_duplicate_reprices,
        "initial_program_count": len(initial_programs),
        "saved_program_count": len(result.programs),
        "saved_program_pool": str(args.save_program_pool) if args.save_program_pool else None,
    }
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output_file is not None:
        output_path = args.output_file
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
        print(f"Wrote {output_path}")
    print(rendered)


def _load_program_pool(path: Path, problem, scale: str, instance: str):
    """Load and independently revalidate a previous run's columns."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "m4_single_satellite_program_pool_v1":
        raise ValueError("not an M4 single-satellite program-pool checkpoint")
    if payload.get("scale") != scale or payload.get("instance") != instance:
        raise ValueError("program-pool checkpoint is for a different benchmark instance")
    return tuple(
        make_m4_satellite_program(
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
        "schema": "m4_single_satellite_program_pool_v1",
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
