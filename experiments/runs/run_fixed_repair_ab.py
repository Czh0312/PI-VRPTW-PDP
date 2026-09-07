"""Reproducible fixed-assignment repair A/B probe (M3 and M6).

Run the baseline before editing, then use another label with the same output
directory. M6 replays saved M3 incumbents, so changes upstream cannot confound
its comparison. Fixed-iteration runs disable all wall-time-driven adaptation.
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import os
from pathlib import Path
import pstats
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.runs.run_model12346_alns_scaling import (
    as_solution, load_problem, route_cost, solve_one,
)
from models.routing.alns.variants import model_6

FIXED_CONFIG = {
    "cooling_schedule": "iteration", "segment_time": None,
    "reward_time_exponent": 0.0, "stagnation_fraction": None,
    "stagnation_time": None, "intensification_fraction": 0.0,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--instances", nargs="+", default=[
        "Set2_100-10-1", "Set2_100-5-1", "Set2_200-10-1"])
    parser.add_argument("--seeds", nargs="+", type=int, default=[11, 23, 42])
    parser.add_argument("--models", nargs="+", type=int, choices=[3, 6], default=[3, 6])
    parser.add_argument("--modes", nargs="+", choices=["fixed", "time"], default=["fixed", "time"])
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--time", type=float, default=10.0)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output/results/m6_optimization_20260831")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    sources = [ROOT / f"models/routing/alns/{name}.py" for name in (
        "engine", "variants/model_3", "variants/model_6", "fixed_repair")]
    fingerprint = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sources if path.exists()
    }
    stamp = time.strftime("%Y%m%d_%H%M%S")
    output = args.output_dir / f"{args.label}_{stamp}.jsonl"
    fleet = json.loads((ROOT / "config/routing/validation/zhou_large.json").read_text(encoding="utf-8"))
    with output.open("x", encoding="utf-8") as stream:
        for instance in args.instances:
            for seed in args.seeds:
                problem = load_problem(ROOT / f"data/zhou_later_pickup/large/standard_same_day/{instance}.csv", fleet)
                replay = args.output_dir / f"initial_{instance}_{seed}.json"
                if 6 in args.models and not replay.exists():
                    r1, r2, _, info = solve_one(3, problem, max_time=600,
                        iterations=100, seed=seed, alns_config_overrides=FIXED_CONFIG)
                    if not info["feasible"]:
                        raise RuntimeError(f"No feasible M3 replay for {instance}")
                    replay.write_text(json.dumps({"e1": r1, "e2": r2,
                        "objective": info["objective"], "source_hashes": fingerprint,
                        "seed": seed, "iterations": 100}, ensure_ascii=False), encoding="utf-8")
                for mode in args.modes:
                    for model in args.models:
                        initial = None
                        replay_hash = None
                        if model == 6:
                            data = json.loads(replay.read_text(encoding="utf-8"))
                            initial = as_solution(data["e1"], data["e2"])
                            replay_hash = hashlib.sha256(replay.read_bytes()).hexdigest()
                        stages = []
                        original_search = model_6.run_alns_search

                        def traced_search(*a, **kw):
                            started = time.perf_counter()
                            result = original_search(*a, **kw)
                            best = result.best_solution
                            stages.append({"objective": result.best_cost,
                                "elapsed": time.perf_counter() - started,
                                "iterations": result.iterations,
                                "e1_routes": [r.full_path() for r in best.e1_routes] if best else None,
                                "e2_routes": {sat: [r.full_path() for r in routes]
                                    for sat, routes in best.e2_routes.items()} if best else None,
                                "diagnostics": result.diagnostics})
                            return result

                        model_6.run_alns_search = traced_search
                        profiler = cProfile.Profile() if args.profile else None
                        started = time.perf_counter()
                        try:
                            if profiler:
                                profiler.enable()
                            r1, r2, _, info = solve_one(model, problem,
                                max_time=600 if mode == "fixed" else args.time,
                                iterations=args.iterations if mode == "fixed" else 1_000_000,
                                seed=seed, initial_solution=initial,
                                alns_config_overrides=FIXED_CONFIG if mode == "fixed" else None)
                        finally:
                            if profiler:
                                profiler.disable()
                            model_6.run_alns_search = original_search
                        wall = time.perf_counter() - started
                        audited = route_cost(problem, r1, r2)
                        row = {"label": args.label, "mode": mode, "instance": instance,
                            "seed": seed, "model": model, "wall_time": wall,
                            "python_hash_seed": os.environ.get("PYTHONHASHSEED"),
                            "time_budget": args.time if mode == "time" else 600,
                            "fixed_config": FIXED_CONFIG if mode == "fixed" else None,
                            "source_hashes": fingerprint, "replay_sha256": replay_hash,
                            "route_cost_audit": audited,
                            "route_cost_delta": None if audited is None else audited - info["objective"],
                            "e1_routes": r1, "e2_routes": r2, "engine_stages": stages,
                            **info}
                        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                        stream.flush()
                        print(json.dumps({k: row.get(k) for k in (
                            "label", "mode", "instance", "seed", "model", "objective",
                            "wall_time", "iterations", "feasible", "refill_iterations",
                            "transshipment_count", "transshipment_search_elapsed_seconds")}), flush=True)
                        if profiler:
                            pstats.Stats(profiler).sort_stats("cumulative").print_stats(20)
    print(f"Wrote {output}", flush=True)


if __name__ == "__main__":
    main()
