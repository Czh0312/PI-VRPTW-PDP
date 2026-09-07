"""M7 ALNS A/B with frozen M4 seeds and separate base-search route records."""
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
sys.path.insert(0, str(ROOT))

from experiments.runs.run_model12346_alns_scaling import as_solution, load_problem, route_cost, solve_one
from experiments.runs.run_fixed_repair_ab import FIXED_CONFIG
from models.routing.alns.variants import model_7


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--instances", nargs="+", default=["Set2_100-10-1", "Set2_100-5-1", "Set2_200-10-1"])
    parser.add_argument("--seeds", nargs="+", type=int, default=[11, 23, 42])
    parser.add_argument("--iterations", type=int, default=4)
    parser.add_argument("--time", type=float, default=10)
    parser.add_argument("--modes", nargs="+", choices=["fixed", "time"], default=["fixed", "time"])
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output/results/m7_optimization_20260831")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    fleet = json.loads((ROOT / "config/routing/validation/zhou_large.json").read_text(encoding="utf-8"))
    sources = {name: hashlib.sha256((ROOT / f"models/routing/alns/{name}.py").read_bytes()).hexdigest()
               for name in ("engine", "initial_solution", "variants/model_4", "variants/model_6", "variants/model_7")}
    output = args.output_dir / f"alns_{args.label}_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
    with output.open("x", encoding="utf-8") as stream:
        for instance in args.instances:
            for seed in args.seeds:
                problem = load_problem(ROOT / f"data/zhou_later_pickup/large/standard_same_day/{instance}.csv", fleet)
                replay = args.output_dir / f"m4_alns_{instance}_{seed}.json"
                if not replay.exists():
                    e1, e2, assignment, info = solve_one(4, problem, max_time=600, iterations=3,
                        seed=seed, alns_config_overrides=FIXED_CONFIG)
                    if not info["feasible"]:
                        raise RuntimeError(f"No feasible M4 replay for {instance}")
                    replay.write_text(json.dumps({"e1": e1, "e2": e2, "assignment": assignment,
                        "objective": info["objective"], "sources": sources}, ensure_ascii=False), encoding="utf-8")
                data = json.loads(replay.read_text(encoding="utf-8"))
                for mode in args.modes:
                    stages = []
                    original = model_7.run_alns_search

                    def traced(*a, **kw):
                        started = time.perf_counter()
                        result = original(*a, **kw)
                        best = result.best_solution
                        stages.append({"objective": result.best_cost, "iterations": result.iterations,
                            "elapsed": time.perf_counter() - started,
                            "e1_routes": [r.full_path() for r in best.e1_routes] if best else None,
                            "e2_routes": {s: [r.full_path() for r in routes] for s, routes in best.e2_routes.items()} if best else None})
                        return result

                    model_7.run_alns_search = traced
                    profile = cProfile.Profile() if args.profile else None
                    started = time.perf_counter()
                    try:
                        if profile:
                            profile.enable()
                        e1, e2, assignment, info = solve_one(7, problem,
                            max_time=600 if mode == "fixed" else args.time,
                            iterations=args.iterations if mode == "fixed" else 1_000_000,
                            seed=seed, initial_solution=as_solution(data["e1"], data["e2"]),
                            alns_config_overrides=FIXED_CONFIG if mode == "fixed" else None)
                    finally:
                        if profile:
                            profile.disable()
                        model_7.run_alns_search = original
                    row = {"label": args.label, "instance": instance, "seed": seed, "mode": mode,
                        "wall_time": time.perf_counter() - started, "source_hashes": sources,
                        "python_hash_seed": os.environ.get("PYTHONHASHSEED"),
                        "replay_sha256": hashlib.sha256(replay.read_bytes()).hexdigest(),
                        "routes_e1": e1, "routes_e2": e2, "stages": stages,
                        "route_cost_delta": route_cost(problem, e1, e2) - info["objective"], **info}
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                    stream.flush()
                    print(json.dumps({key: row.get(key) for key in ("label", "instance", "seed", "mode",
                        "objective", "wall_time", "iterations", "feasible", "transshipment_count")}), flush=True)
                    if profile:
                        pstats.Stats(profile).sort_stats("cumulative").print_stats(25)
    print(f"Wrote {output}", flush=True)


if __name__ == "__main__":
    main()
