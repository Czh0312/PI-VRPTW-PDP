"""Paired, sequential wall-time benchmark with per-run diagnostics and routes."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from experiments.runs.run_model12346_alns_scaling import (
    ROOT, load_problem, route_cost, solve_one,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--seeds", type=int, nargs="+", default=[42, 43, 44])
    parser.add_argument("--models", type=int, nargs="+", default=[3, 4, 6, 7])
    parser.add_argument("--instances", nargs="+", default=[
        "Set2_100-5-1", "Set2_100-10-1", "Set2_200-10-1"])
    args = parser.parse_args()
    fleet = json.loads((ROOT / "config/routing/validation/zhou_large.json").read_text())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        for instance in args.instances:
            for seed in args.seeds:
                for model in args.models:
                    problem = load_problem(
                        ROOT / "data/zhou_later_pickup/large/standard_same_day"
                        / f"{instance}.csv", fleet)
                    started = time.perf_counter()
                    r1, r2, assignment, info = solve_one(
                        model, problem, max_time=args.seconds,
                        iterations=1_000_000, seed=seed)
                    elapsed = time.perf_counter() - started
                    checked = route_cost(problem, r1, r2)
                    delta = (checked - info["objective"]
                             if checked is not None else None)
                    row = dict(instance=instance, seed=seed, model=model,
                               time_budget=args.seconds, wall_time=elapsed,
                               route_cost_delta=delta, info=info,
                               e1_routes=r1, e2_routes=r2, assignment=assignment)
                    if info.get("feasible"):
                        assert delta is not None and abs(delta) < 1e-6
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                    handle.flush()
                    print(instance, seed, model, info.get("objective"),
                          info.get("iterations"), round(elapsed, 3), flush=True)


if __name__ == "__main__":
    main()
