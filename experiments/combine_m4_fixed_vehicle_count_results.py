"""Combine fixed-E2-vehicle-count M4 runs into one global certificate.

For a one-satellite instance, the feasible set is the disjoint union over the
number of used E2 vehicles.  Each component MIP therefore supplies a lower
bound for one partition; after every capacity-possible count is represented,
the minimum component bound is a valid lower bound for the full instance.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.instance import ProblemInstance
from utils.data_loader import parse_dataset


def load_problem(path: Path):
    fleet = json.loads((ROOT / "config" / "fleet_config.json").read_text(encoding="utf-8"))
    nodes, _ = parse_dataset(str(path))
    problem = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    return problem


def possible_vehicle_counts(problem) -> tuple[int, ...]:
    """Return every E2 route count not excluded by aggregate capacity."""

    customers = tuple(problem.customers)
    cw = float(problem.fleet.container_weight)
    cv = float(problem.fleet.container_volume)
    delivery_weight = sum(float(customer.weight_del) + cw for customer in customers)
    delivery_volume = len(customers) * cv
    pickup_weight = len(customers) * cw
    pickup_volume = len(customers) * cv

    def vehicles_needed(total: float, capacity: float) -> int:
        if capacity <= 0.0:
            raise ValueError("E2 capacities must be positive")
        return max(1, int(math.ceil(total / capacity - 1e-12)))

    minimum = max(
        vehicles_needed(delivery_weight, float(problem.e2_weight_cap)),
        vehicles_needed(delivery_volume, float(problem.e2_volume_cap)),
        vehicles_needed(pickup_weight, float(problem.e2_weight_cap)),
        vehicles_needed(pickup_volume, float(problem.e2_volume_cap)),
    )
    # Every used vehicle must leave the satellite on at least one task arc.
    maximum = min(int(problem.fleet.e2_per_sat_count), 2 * len(customers))
    return tuple(range(minimum, maximum + 1))


def combine_results(
    records: Iterable[tuple[Path, Mapping[str, Any]]],
    *,
    instance_key: str,
    expected_counts: Iterable[int],
    tolerance: float = 1e-6,
) -> dict[str, Any]:
    """Validate component provenance and construct a proof-safe portfolio."""

    expected = tuple(sorted(set(int(count) for count in expected_counts)))
    if not expected:
        raise ValueError("expected vehicle-count set must not be empty")
    by_count: dict[int, tuple[Path, Mapping[str, Any]]] = {}
    for source, payload in records:
        if payload.get("instance") != instance_key:
            raise ValueError(f"{source}: result belongs to {payload.get('instance')!r}")
        if payload.get("direct_program_mip") is not True:
            raise ValueError(f"{source}: not a direct-program MIP result")
        info = payload.get("info")
        if not isinstance(info, Mapping):
            raise ValueError(f"{source}: missing result info")
        if info.get("proof_scope") != "single_satellite_direct_program_mip_fixed_vehicle_count":
            raise ValueError(f"{source}: not a fixed-vehicle-count proof result")
        count = info.get("required_vehicle_count")
        if not isinstance(count, int) or isinstance(count, bool):
            raise ValueError(f"{source}: invalid required_vehicle_count")
        if payload.get("direct_fixed_vehicle_count") != count:
            raise ValueError(f"{source}: top-level vehicle count disagrees with result info")
        if count in by_count:
            raise ValueError(f"duplicate result for {count} E2 vehicles")
        by_count[count] = (source, payload)

    missing = sorted(set(expected) - set(by_count))
    extra = sorted(set(by_count) - set(expected))
    if missing:
        raise ValueError(f"missing fixed-count results: {missing}")
    if extra:
        raise ValueError(f"unexpected fixed-count results: {extra}")

    components = []
    finite_bounds: list[float] = []
    incumbents: list[tuple[float, Path, Mapping[str, Any]]] = []
    every_component_infeasible = True
    for count in expected:
        source, payload = by_count[count]
        info = payload["info"]
        proven_infeasible = info.get("subproblem_proven_infeasible") is True
        proven_optimal = info.get("subproblem_proven_optimal") is True
        objective = _finite_number(info.get("objective"), f"{source}: objective", allow_none=True)
        raw_bound = _finite_number(
            info.get("subproblem_best_bound"),
            f"{source}: subproblem_best_bound",
            allow_none=True,
        )
        if proven_infeasible:
            if objective is not None:
                raise ValueError(f"{source}: infeasible partition contains an incumbent")
            effective_bound = None
        else:
            every_component_infeasible = False
            if raw_bound is None:
                raise ValueError(f"{source}: partition has neither a bound nor infeasibility proof")
            # Numerical solver tolerances can put ObjBound microscopically above
            # ObjVal.  The minimum keeps the exported certificate conservative.
            effective_bound = min(raw_bound, objective) if objective is not None else raw_bound
            finite_bounds.append(effective_bound)
            if objective is not None:
                incumbents.append((objective, source, payload))
        components.append(
            {
                "vehicle_count": count,
                "source": str(source.resolve()),
                "status": info.get("status"),
                "proven_optimal": proven_optimal,
                "proven_infeasible": proven_infeasible,
                "objective": objective,
                "raw_best_bound": raw_bound,
                "certificate_best_bound": effective_bound,
                "runtime": info.get("e2_pricing_runtime"),
            }
        )

    if every_component_infeasible:
        return {
            "schema": "m4_fixed_vehicle_count_portfolio_v1",
            "instance": instance_key,
            "status": "INFEASIBLE",
            "proof_scope": "all_capacity_possible_e2_vehicle_counts",
            "proven_optimal": False,
            "proven_infeasible": True,
            "objective": None,
            "best_bound": None,
            "mip_gap": None,
            "expected_vehicle_counts": list(expected),
            "components": components,
        }

    if not finite_bounds:
        raise ValueError("no finite component lower bound is available")
    global_bound = min(finite_bounds)
    global_objective = min((item[0] for item in incumbents), default=None)
    winner = min(incumbents, default=None, key=lambda item: item[0])
    scale = max(1.0, abs(global_objective or 0.0), abs(global_bound))
    proven_optimal = bool(
        global_objective is not None
        and global_objective - global_bound <= tolerance * scale
    )
    gap = (
        max(0.0, (global_objective - global_bound) / abs(global_objective))
        if global_objective is not None and abs(global_objective) > tolerance
        else None
    )
    return {
        "schema": "m4_fixed_vehicle_count_portfolio_v1",
        "instance": instance_key,
        "status": "OPTIMAL" if proven_optimal else "PORTFOLIO_BOUND",
        "proof_scope": "all_capacity_possible_e2_vehicle_counts",
        "proven_optimal": proven_optimal,
        "proven_infeasible": False,
        "objective": global_objective,
        "best_bound": global_objective if proven_optimal else global_bound,
        "mip_gap": 0.0 if proven_optimal else gap,
        "expected_vehicle_counts": list(expected),
        "incumbent_source": str(winner[1].resolve()) if winner is not None else None,
        "e1_routes": winner[2].get("e1_routes") if winner is not None else None,
        "e2_routes_by_sat": winner[2].get("e2_routes_by_sat") if winner is not None else None,
        "components": components,
    }


def _finite_number(value: Any, label: str, *, allow_none: bool) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", default="10_customers")
    parser.add_argument("--instance", default="R203")
    parser.add_argument("--result", action="append", type=Path, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    parser.add_argument("--tolerance", type=float, default=1e-6)
    args = parser.parse_args()
    if args.tolerance < 0.0:
        raise SystemExit("--tolerance must be nonnegative")

    instance_path = ROOT / "data" / args.scale / f"{args.instance}.csv"
    if not instance_path.exists():
        raise SystemExit(f"instance not found: {instance_path}")
    problem = load_problem(instance_path)
    expected = possible_vehicle_counts(problem)
    records = []
    for path in args.result:
        resolved = path if path.is_absolute() else ROOT / path
        records.append((resolved, json.loads(resolved.read_text(encoding="utf-8"))))
    output = combine_results(
        records,
        instance_key=f"{args.scale}/{args.instance}",
        expected_counts=expected,
        tolerance=args.tolerance,
    )
    output["capacity_derived_min_vehicle_count"] = expected[0]
    output["fleet_max_vehicle_count"] = expected[-1]

    output_path = args.output_file if args.output_file.is_absolute() else ROOT / args.output_file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    output_path.write_text(rendered, encoding="utf-8")
    print(f"Wrote {output_path}")
    print(rendered)


if __name__ == "__main__":
    main()
