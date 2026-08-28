# -*- coding: utf-8 -*-
"""Search deterministic tiny instances where physical E2 relay saves distance."""
import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Customer, Depot, Satellite
from models.routing.variants.model_3.solver import solve_exact as solve_m3_exact
from models.routing.variants.model_5.solver import solve_exact as solve_m5_exact
from models.routing.common.task_builder import build_tasks_for_model


def make_problem(seed, customer_count=5):
    rng = random.Random(seed)
    nodes = {
        "D0": Depot("D0", -6.0, 0.0),
        "S0": Satellite("S0", 0.0, 0.0, service_time=0.5),
    }
    # Mix a radial cluster with one customer on the opposite side.  Pickup
    # releases are deliberately staggered, making the cheapest delivery and
    # pickup route partitions different while keeping every instance feasible.
    angles = [
        rng.uniform(-0.35, 0.35) if i < customer_count - 1
        else rng.uniform(math.pi - 0.35, math.pi + 0.35)
        for i in range(customer_count)
    ]
    radii = [rng.uniform(7.0, 16.0) for _ in range(customer_count)]
    releases = [60.0 + 30.0 * i for i in range(customer_count)]
    rng.shuffle(releases)
    for i, (angle, radius, release) in enumerate(
            zip(angles, radii, releases)):
        nodes[f"C{i}"] = Customer(
            f"C{i}", radius * math.cos(angle), radius * math.sin(angle),
            demand=rng.choice([1.0, 2.0]), service_time=0.5,
            tw_del=(8.0, rng.choice([45.0, 55.0, 65.0])),
            tw_pick=(release, release + rng.choice([8.0, 12.0, 16.0])),
            assigned_sat="S0",
        )
    fleet = {
        "E1_vehicle_count": 1,
        "E1_Qk": 200.0,
        "E1_Ck": 20.0,
        "vehicles_per_satellite": 2,
        "E2_Qv": 100.0,
        # Two vehicles have one aggregate spare slot.  A shortest pickup route
        # that collects more than one vehicle's share must use a handoff.
        "E2_Cv": float((customer_count + 1) // 2),
        "container_weight": 2.0,
        "container_volume": 1.0,
        "speed": 1.0,
    }
    problem = ProblemInstance(nodes, fleet, speed=1.0)
    problem.aggregate_demands_from_pre_assignment()
    return problem


def solve_seed(seed, customer_count, time_limit, branch_time_limit=30.0):
    problem = make_problem(seed, customer_count)
    e1, e2 = build_tasks_for_model(problem, 3)
    m3_e1, m3_e2, m3 = solve_m3_exact(
        problem, e1, e2, problem.fleet.e2_per_sat_count,
        TFp=9999.0, verbose=False, time_limit=time_limit,
        return_info=True,
    )
    if not m3.get("proven_optimal"):
        return {"seed": seed, "status": "M3_NOT_PROVEN", "m3": m3}

    m5_e1, m5_e2, m5 = solve_m5_exact(
        problem, e1, e2, problem.fleet.e2_per_sat_count,
        verbose=False, time_limit=time_limit, return_info=True,
        return_incumbent_on_time_limit=True,
        warm_start_routes=(m3_e1, m3_e2),
        mode_partition_branch_time_limit=branch_time_limit,
    )
    saving = None
    if m5.get("objective") is not None:
        saving = float(m3["objective"]) - float(m5["objective"])
    return {
        "seed": seed,
        "status": m5.get("status"),
        "m3_objective": m3.get("objective"),
        "m5_objective": m5.get("objective"),
        "m5_bound": m5.get("best_bound"),
        "m5_proven": m5.get("proven_optimal"),
        "saving": saving,
        "relay_count": m5.get("transshipment_count"),
        "transshipments": m5.get("transshipments", []),
        "formulation": m5.get("formulation"),
        "runtime": m5.get("runtime"),
        "certificate": m5.get("certificate"),
        "m3_routes": {"e1": m3_e1, "e2": m3_e2},
        "m5_routes": {"e1": m5_e1, "e2": m5_e2},
        "customers": {
            node.id: {
                "x": node.x, "y": node.y,
                "tw_del": list(node.tw_del),
                "tw_pick": list(node.tw_pick),
            }
            for node in problem.customers
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=100)
    parser.add_argument("--start-seed", type=int, default=0)
    parser.add_argument("--customers", type=int, default=5)
    parser.add_argument("--time-limit", type=float, default=20.0)
    parser.add_argument("--branch-time-limit", type=float, default=30.0)
    parser.add_argument(
        "--output", default="output/results/model5_relay_benefit_search.jsonl"
    )
    args = parser.parse_args()
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    found = None
    with output.open("w", encoding="utf-8") as handle:
        for seed in range(args.start_seed, args.start_seed + args.trials):
            try:
                row = solve_seed(
                    seed, args.customers, args.time_limit,
                    args.branch_time_limit,
                )
            except Exception as exc:
                row = {"seed": seed, "status": "ERROR", "error": repr(exc)}
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(json.dumps({
                key: row.get(key) for key in (
                    "seed", "status", "m3_objective", "m5_objective",
                    "saving", "relay_count", "m5_proven",
                )
            }, ensure_ascii=False), flush=True)
            if (row.get("m5_proven") and row.get("relay_count", 0) > 0
                    and (row.get("saving") or 0.0) > 1e-6):
                found = row
                break
    print(json.dumps({
        "found": found,
        "elapsed": time.perf_counter() - started,
        "output": str(output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
