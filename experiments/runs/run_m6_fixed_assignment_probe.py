# -*- coding: utf-8 -*-
"""A/B benchmark for one fixed-assignment compact Model 6 subproblem."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GUROBI_PY = Path(r"C:\gurobi\win64\python311\lib")
if GUROBI_PY.exists() and str(GUROBI_PY) not in sys.path:
    sys.path.append(str(GUROBI_PY))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.routing.common.data_loader import parse_dataset
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.task_builder import build_tasks_for_model
from models.routing.variants.model_6.exact_solver import solve_exact as solve_m6
from models.routing.variants.model_7.exact_solver import _partition_relaxation_screen


def _load_problem(dataset: Path, satellite: str) -> ProblemInstance:
    fleet = json.loads(
        (ROOT / "config" / "routing" / "validation" / "zhou_small.json")
        .read_text(encoding="utf-8")
    )
    nodes, _ = parse_dataset(str(dataset))
    problem = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    valid = {item.id for item in problem.satellites}
    if satellite not in valid:
        raise ValueError(
            f"unknown satellite {satellite!r}; valid={sorted(valid)}")
    for customer in problem.customers:
        customer.assigned_sat = satellite
    problem.aggregate_demands_from_pre_assignment()
    return problem


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--satellite", required=True)
    parser.add_argument("--time-limit", type=float, default=30.0)
    parser.add_argument("--cutoff", type=float, default=None)
    parser.add_argument(
        "--relaxation-only", action="store_true",
        help="Run only the zero-pickup-load M3 proof relaxation.",
    )
    parser.add_argument(
        "--warm-start-json", type=Path, default=None,
        help="JSON containing e1/e2 routes, such as an audited M4 result.",
    )
    parser.add_argument(
        "--variants", nargs="+",
        choices=["no_strengthen", "strengthen", "proof_tuned"],
        default=["no_strengthen", "strengthen"],
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("output/results/m6_fixed_assignment_probe"),
    )
    args = parser.parse_args()
    dataset = args.dataset if args.dataset.is_absolute() else ROOT / args.dataset
    output_dir = (
        args.output_dir if args.output_dir.is_absolute()
        else ROOT / args.output_dir
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    warm_start = None
    if args.warm_start_json is not None:
        warm_path = (
            args.warm_start_json if args.warm_start_json.is_absolute()
            else ROOT / args.warm_start_json
        )
        payload = json.loads(warm_path.read_text(encoding="utf-8"))
        warm_start = (payload["e1"], payload["e2"])

    records = []
    if args.relaxation_only:
        if args.cutoff is None:
            parser.error("--relaxation-only requires --cutoff")
        problem = _load_problem(dataset, args.satellite)
        e1_tasks, e2_tasks = build_tasks_for_model(problem, 6)
        record = {
            "dataset": dataset.stem,
            "satellite": args.satellite,
            "variant": "zero_pickup_load_relaxation",
            **_partition_relaxation_screen(
                problem, e1_tasks, e2_tasks,
                problem.fleet.e2_per_sat_count,
                TFp=9999.0,
                cutoff=float(args.cutoff),
                time_limit=float(args.time_limit),
            ),
        }
        records.append(record)
        print(json.dumps(record, ensure_ascii=False, default=str))

    settings = {
        "no_strengthen": (False, {}),
        "strengthen": (True, {}),
        "proof_tuned": (
            True,
            {"MIPFocus": 3, "Heuristics": 0.0, "Cuts": 2},
        ),
    }
    for variant in ([] if args.relaxation_only else args.variants):
        strengthen, variant_params = settings[variant]
        problem = _load_problem(dataset, args.satellite)
        e1_tasks, e2_tasks = build_tasks_for_model(problem, 6)
        params = dict(variant_params)
        if args.cutoff is not None:
            params["Cutoff"] = float(args.cutoff)
        started = time.perf_counter()
        _e1, _e2, info = solve_m6(
            problem,
            e1_tasks,
            e2_tasks,
            problem.fleet.e2_per_sat_count,
            time_limit=args.time_limit,
            verbose=False,
            return_info=True,
            return_incumbent_on_time_limit=True,
            gurobi_params=params,
            warm_start_routes=warm_start,
            strengthen=strengthen,
            use_relaxation_certificate=False,
            compute_iis_on_infeasible=False,
        )
        record = {
            "dataset": dataset.stem,
            "satellite": args.satellite,
            "variant": variant,
            "strengthen": strengthen,
            "warm_start": warm_start is not None,
            "wall_time": time.perf_counter() - started,
            **info,
        }
        records.append(record)
        print(json.dumps(record, ensure_ascii=False, default=str))

    stamp = time.strftime("%Y%m%d_%H%M%S")
    output = output_dir / f"{dataset.stem}_{args.satellite}_{stamp}.json"
    output.write_text(
        json.dumps(records, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
