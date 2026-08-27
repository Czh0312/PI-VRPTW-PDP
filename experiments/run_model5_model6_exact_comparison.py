# -*- coding: utf-8 -*-
"""Compare Model 5 and compact Model 6 on the same exact-solve instance."""
import argparse
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.instance import ProblemInstance
from models.model_5.solver import solve_exact as solve_m5
from models.model_6.solver import solve_exact as solve_m6
from utils.data_loader import parse_dataset
from utils.task_builder import build_tasks_for_model


def _load_problem(dataset):
    fleet = json.loads(
        (ROOT / "config" / "fleet_config.json").read_text(encoding="utf-8")
    )
    nodes, _ = parse_dataset(str(dataset))
    problem = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    return problem


def _run(label, solver, problem, e1_tasks, e2_tasks, limit, seed):
    started = time.perf_counter()
    _e1, _e2, info = solver(
        problem, e1_tasks, e2_tasks,
        problem.fleet.e2_per_sat_count,
        verbose=False,
        time_limit=limit,
        mip_gap=0.0,
        gurobi_params={"Seed": seed},
        return_incumbent_on_time_limit=True,
        return_info=True,
    )
    return {
        "model": label,
        "time_limit": limit,
        "wall_time": time.perf_counter() - started,
        **info,
    }


def _json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset", type=Path,
        default=(ROOT / "data" / "zhou_later_pickup" / "small"
                 / "next_day" / "Set1_10-2-3.csv"),
    )
    parser.add_argument("--time-limit", type=float, default=60.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--output", type=Path,
        default=(ROOT / "results" / "model5_model6_exact_comparison"
                 / "Set1_10-2-3_next_day.json"),
    )
    args = parser.parse_args()

    problem = _load_problem(args.dataset)
    e1_tasks, e2_tasks = build_tasks_for_model(problem, 6)
    rows = [
        _run(
            "Model 5", solve_m5, problem, e1_tasks, e2_tasks,
            args.time_limit, args.seed,
        ),
        _run(
            "Model 6", solve_m6, problem, e1_tasks, e2_tasks,
            args.time_limit, args.seed,
        ),
    ]
    payload = _json_safe({
        "dataset": str(args.dataset.resolve()),
        "customer_count": len(problem.customers),
        "satellite_count": len(problem.satellites),
        "gurobi_seed": args.seed,
        "comparison_scope_note": (
            "Model 5 uses arbitrary explicit TP/TD/RP handoffs; Model 6 uses "
            "homogeneous integer asynchronous handoffs at existing service "
            "locations. Their "
            "feasible regions differ, so the timing comparison is meaningful "
            "but the Model 6 certificate does not certify Model 5."
        ),
        "runs": rows,
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
