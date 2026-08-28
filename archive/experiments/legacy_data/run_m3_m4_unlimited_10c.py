# -*- coding: utf-8 -*-
"""Resumable unlimited exact campaign for the hard 10-customer M3/M4 cases.

Every shipped 10-customer instance has exactly one satellite.  Consequently
M4's free-assignment variables are fixed and M3 and M4 have the same feasible
set, E1 cost, E2 programs, and optimum.  We exploit that exact equivalence and
solve the complete one-satellite program MIP after partitioning its feasible
set by the number of used E2 vehicles.  A partition is persisted immediately
after it closes, so a restarted campaign skips completed work.

Omitting ``--time-limit`` leaves Gurobi's TimeLimit at infinity.  Supplying a
limit is intended only for smoke tests and bound diagnostics; an incomplete
partition stops the campaign and is never promoted to a global certificate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
GUROBI_PY = Path(r"C:\gurobi\win64\python311\lib")
if GUROBI_PY.exists() and str(GUROBI_PY) not in sys.path:
    sys.path.insert(0, str(GUROBI_PY))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.analysis.combine_m4_fixed_vehicle_count_results import (  # noqa: E402
    combine_results,
    load_problem,
    possible_vehicle_counts,
)
from models.routing.variants.model_4.alns_solver import solve_alns as solve_m4_alns  # noqa: E402
from models.routing.variants.model_4.single_satellite_exact_bp import (  # noqa: E402
    solve_single_satellite_direct_program_mip,
)


# C109 was already closed by a dedicated unlimited M3 run (908.8 s).  These
# are the remaining non-OPTIMAL cases in the complete finite 10c baseline.
DEFAULT_PENDING_INSTANCES = (
    "C209",
    "R103",
    "R201",
    "R203",
    "R205",
    "R207",
    "R209",
    "RC203",
    "RC207",
    "RC208",
)
DEFAULT_GUROBI_WORK_DIR = Path(r"C:\Windows\Temp\m3_m4_unlimited_10c")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically replace a campaign JSON artifact."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def _parse_gurobi_params(items: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {
        "MIPFocus": 3,
        "Seed": 42,
        "OutputFlag": 1,
        # Long unlimited runs must spill the B&B tree before memory pressure
        # can terminate a proof attempt.
        "NodefileStart": 0.5,
    }
    for item in items:
        if "=" not in item:
            raise ValueError(f"--gurobi-param must be NAME=VALUE, got {item!r}")
        name, raw_value = item.split("=", 1)
        try:
            params[name] = json.loads(raw_value)
        except json.JSONDecodeError:
            params[name] = raw_value
    return params


def _partition_closed(payload: Mapping[str, Any]) -> bool:
    info = payload.get("info")
    return bool(
        isinstance(info, Mapping)
        and (
            info.get("subproblem_proven_optimal") is True
            or info.get("subproblem_proven_infeasible") is True
        )
    )


def _load_or_build_warm_start(
    problem,
    instance_dir: Path,
    *,
    scale: str,
    instance: str,
    alns_time: float,
    seed: int,
) -> tuple[Mapping[str, list[list[str]]] | None, Mapping[str, Any]]:
    path = instance_dir / "alns_warm_start.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("instance") != f"{scale}/{instance}":
            raise ValueError(f"warm start belongs to another instance: {path}")
        routes = payload.get("e2_routes_by_sat")
        return (routes if isinstance(routes, Mapping) else None), payload

    started = time.perf_counter()
    e1_routes, e2_routes, assignment = solve_m4_alns(
        problem,
        problem.fleet.e2_per_sat_count,
        max_time=alns_time,
        seed=seed,
        verbose=False,
    )
    available = bool(e1_routes and e2_routes and assignment)
    route_count = (
        sum(1 for routes in e2_routes.values() for route in routes if route)
        if available else None
    )
    payload = {
        "schema": "m3_m4_single_satellite_alns_warm_start_v1",
        "instance": f"{scale}/{instance}",
        "seed": seed,
        "time_limit": alns_time,
        "wall_time": time.perf_counter() - started,
        "available": available,
        "route_count": route_count,
        "e1_routes": e1_routes if available else None,
        "e2_routes_by_sat": e2_routes if available else None,
        "assignment": assignment if available else None,
    }
    _write_json(path, payload)
    return (e2_routes if available else None), payload


def _component_payload(
    *,
    scale: str,
    instance: str,
    vehicle_count: int,
    time_limit: float | None,
    params: Mapping[str, Any],
    subtour_strengthening_size: int,
    warm_info: Mapping[str, Any],
    warm_compatible: bool,
    result,
) -> dict[str, Any]:
    return {
        "instance": f"{scale}/{instance}",
        "info": result.info,
        "e1_routes": result.e1_routes,
        "e2_routes_by_sat": result.e2_routes_by_sat,
        "pricing_gurobi_params": dict(params),
        "direct_program_mip": True,
        "direct_time_limit": time_limit,
        "direct_fixed_vehicle_count": vehicle_count,
        "direct_warm_start": {
            "source": "campaign_alns_warm_start",
            "available": bool(warm_info.get("available")),
            "route_count": warm_info.get("route_count"),
            "compatible_with_fixed_vehicle_count": warm_compatible,
        },
        "pricing_subtour_strengthening_size": subtour_strengthening_size,
        "campaign": "m3_m4_unlimited_10c_v1",
    }


def _model_certificate(model: int, portfolio: Mapping[str, Any]) -> dict[str, Any]:
    proven = portfolio.get("proven_optimal") is True
    return {
        "schema": "single_satellite_m3_m4_equivalence_certificate_v1",
        "model": model,
        "instance": portfolio.get("instance"),
        "status": "OPTIMAL" if proven else portfolio.get("status"),
        "proven_optimal": proven,
        "solution_type": "proven_optimal" if proven else "not_proven",
        "proof_scope": "complete_single_satellite_fixed_vehicle_count_partition",
        "equivalent_models": [2, 6],
        "equivalence_reason": (
            "With exactly one satellite, every M4 assignment variable is fixed "
            "to that satellite, so the complete M3 and M4 feasible sets and "
            "objectives coincide."
        ),
        "objective": portfolio.get("objective"),
        "best_bound": portfolio.get("best_bound"),
        "mip_gap": portfolio.get("mip_gap"),
        "expected_vehicle_counts": portfolio.get("expected_vehicle_counts"),
        "e1_routes": portfolio.get("e1_routes"),
        "e2_routes_by_sat": portfolio.get("e2_routes_by_sat"),
        "components": portfolio.get("components"),
    }


def _resolve_output_dir(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", default="10_customers")
    parser.add_argument("--instances", nargs="*", default=list(DEFAULT_PENDING_INSTANCES))
    parser.add_argument("--output-dir", default="output/results/m3_m4_unlimited_10c")
    parser.add_argument(
        "--gurobi-work-dir", type=Path, default=DEFAULT_GUROBI_WORK_DIR,
        help=(
            "ASCII-only directory for Gurobi logs and node files.  The default "
            "avoids Gurobi 11 path failures when the project path contains CJK text."
        ),
    )
    parser.add_argument(
        "--time-limit", type=float, default=None,
        help="Diagnostic per-partition limit; omit for the production unlimited run.",
    )
    parser.add_argument("--warm-start-alns-time", type=float, default=20.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--subtour-strengthening-size", type=int, choices=(0, 2, 3), default=2,
    )
    parser.add_argument(
        "--gurobi-param", action="append", default=[], metavar="NAME=VALUE",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Rerun even partitions that already carry an optimal/infeasible proof.",
    )
    args = parser.parse_args()
    if args.scale != "10_customers":
        raise SystemExit("this campaign is proof-scoped to 10_customers")
    if args.time_limit is not None and args.time_limit <= 0.0:
        raise SystemExit("--time-limit must be positive")
    if args.warm_start_alns_time < 0.0:
        raise SystemExit("--warm-start-alns-time must be nonnegative")

    output_dir = _resolve_output_dir(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    gurobi_work_dir = args.gurobi_work_dir
    if not gurobi_work_dir.is_absolute():
        gurobi_work_dir = ROOT / gurobi_work_dir
    gurobi_work_dir.mkdir(parents=True, exist_ok=True)
    params = _parse_gurobi_params(args.gurobi_param)
    params.setdefault("NodefileDir", str(gurobi_work_dir.resolve()))
    status_path = output_dir / "campaign_status.json"
    summary: list[dict[str, Any]] = []

    manifest = {
        "schema": "m3_m4_unlimited_10c_campaign_v1",
        "scale": args.scale,
        "instances": args.instances,
        "time_limit": args.time_limit,
        "unlimited": args.time_limit is None,
        "gurobi_params": params,
        "subtour_strengthening_size": args.subtour_strengthening_size,
        "certifies_models": [3, 4],
        "partition_checkpointing": True,
        "gurobi_work_dir": str(gurobi_work_dir.resolve()),
        "process_id": os.getpid(),
    }
    _write_json(output_dir / "campaign_manifest.json", manifest)

    for instance in args.instances:
        data_path = ROOT / "data" / args.scale / f"{instance}.csv"
        if not data_path.exists():
            raise SystemExit(f"instance not found: {data_path}")
        problem = load_problem(data_path)
        if len(problem.satellites) != 1:
            raise SystemExit(
                f"{args.scale}/{instance}: M3/M4 equivalence requires exactly one satellite"
            )
        instance_dir = output_dir / instance
        instance_dir.mkdir(parents=True, exist_ok=True)
        counts = possible_vehicle_counts(problem)
        warm_routes, warm_info = _load_or_build_warm_start(
            problem,
            instance_dir,
            scale=args.scale,
            instance=instance,
            alns_time=args.warm_start_alns_time,
            seed=args.seed,
        )

        for vehicle_count in counts:
            result_path = instance_dir / f"k{vehicle_count}.json"
            if result_path.exists() and not args.force:
                existing = json.loads(result_path.read_text(encoding="utf-8"))
                if _partition_closed(existing):
                    print(f"[skip] {instance} k={vehicle_count} already closed", flush=True)
                    continue

            warm_compatible = bool(
                warm_routes is not None
                and warm_info.get("route_count") == vehicle_count
            )
            run_params = dict(params)
            run_params["LogFile"] = str(
                (gurobi_work_dir / f"{instance}_k{vehicle_count}.log").resolve()
            )
            _write_json(
                status_path,
                {
                    **manifest,
                    "state": "running",
                    "current_instance": instance,
                    "current_vehicle_count": vehicle_count,
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                },
            )
            print(
                f"[run] {instance} k={vehicle_count} "
                f"TimeLimit={'infinity' if args.time_limit is None else args.time_limit}",
                flush=True,
            )
            result = solve_single_satellite_direct_program_mip(
                problem,
                problem.fleet.e2_per_sat_count,
                time_limit=args.time_limit,
                gurobi_params=run_params,
                subtour_strengthening_size=args.subtour_strengthening_size,
                warm_start_e2_routes_by_sat=warm_routes if warm_compatible else None,
                required_vehicle_count=vehicle_count,
            )
            payload = _component_payload(
                scale=args.scale,
                instance=instance,
                vehicle_count=vehicle_count,
                time_limit=args.time_limit,
                params=run_params,
                subtour_strengthening_size=args.subtour_strengthening_size,
                warm_info=warm_info,
                warm_compatible=warm_compatible,
                result=result,
            )
            _write_json(result_path, payload)
            if not _partition_closed(payload):
                _write_json(
                    status_path,
                    {
                        **manifest,
                        "state": "partition_incomplete",
                        "current_instance": instance,
                        "current_vehicle_count": vehicle_count,
                        "result": str(result_path.resolve()),
                        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    },
                )
                print(
                    f"[stop] {instance} k={vehicle_count} returned "
                    f"{result.info.get('status')}; no global certificate emitted",
                    flush=True,
                )
                return 2

        records = []
        for vehicle_count in counts:
            path = instance_dir / f"k{vehicle_count}.json"
            records.append((path, json.loads(path.read_text(encoding="utf-8"))))
        portfolio = combine_results(
            records,
            instance_key=f"{args.scale}/{instance}",
            expected_counts=counts,
        )
        portfolio["certifies_models"] = [3, 4]
        portfolio["single_satellite_equivalence"] = True
        _write_json(instance_dir / "portfolio.json", portfolio)
        for model in (3, 4):
            _write_json(
                instance_dir / f"model{model}_certificate.json",
                _model_certificate(model, portfolio),
            )
        summary.append(
            {
                "instance": instance,
                "proven_optimal": portfolio.get("proven_optimal"),
                "objective": portfolio.get("objective"),
                "best_bound": portfolio.get("best_bound"),
            }
        )
        _write_json(output_dir / "summary.json", {"records": summary})

    _write_json(
        status_path,
        {
            **manifest,
            "state": "complete",
            "completed_instances": [item["instance"] for item in summary],
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        },
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
