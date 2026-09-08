"""Compare M6 stage-one implementations under the same deterministic workload."""
import argparse
import json
import time
from pathlib import Path

from experiments.runs.run_model12346_alns_scaling import ROOT, load_problem
from models.routing.alns.variants.model_6 import solve_alns
from models.routing.common.task_builder import build_tasks_for_model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=500)
    args = parser.parse_args()
    fleet = json.loads((ROOT / "config/routing/validation/zhou_large.json").read_text())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        for index, name in enumerate(("Set2_100-5-1", "Set2_100-10-1", "Set2_200-10-1")):
            results = []
            # Alternate order to reduce systematic first-run bias.
            for fast in ((False, True) if index % 2 == 0 else (True, False)):
                problem = load_problem(ROOT / "data/zhou_later_pickup/large/standard_same_day"
                                       / f"{name}.csv", fleet)
                e1, e2 = build_tasks_for_model(problem, 6)
                started = time.perf_counter()
                r1, r2, info = solve_alns(
                    problem, e1, e2, problem.fleet.e2_per_sat_count,
                    max_time=120, max_iterations=args.iterations, seed=42,
                    verbose=False, return_info=True, fast_stage1=fast,
                    transshipment_time_budget=0, refill_unused_time=False,
                    alns_config_overrides=dict(cooling_schedule="iteration",
                        stagnation_fraction=None, segment_time=None,
                        reward_time_exponent=0, intensification_fraction=0))
                elapsed = time.perf_counter() - started
                assert info["feasible"] and info["iterations"] == args.iterations
                results.append((r1, r2, info["objective"]))
                row = dict(instance=name, fast_stage1=fast, wall_time=elapsed,
                           seed=42, info=info, e1_routes=r1, e2_routes=r2)
                handle.write(json.dumps(row) + "\n")
                handle.flush()
                print(name, fast, round(elapsed, 3), info["objective"], flush=True)
            assert results[0] == results[1], "fixed-workload route parity failed"


if __name__ == "__main__":
    main()
