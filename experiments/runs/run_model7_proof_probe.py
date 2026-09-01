"""Paired M7 proof benchmark with replayed, audited M4 incumbents."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.append(r"C:\gurobi\win64\python311\lib")

from experiments.runs.run_mainline_small_exact import _fleet, _problem
from models.routing.variants.model_4.exact_solver import solve_exact as solve_m4
from models.routing.variants.model_7.exact_solver import solve_exact as solve_m7
from models.routing.common.solution_audit import audit_exact_solution


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--instances", nargs="+", default=["Set1_7-2-2b", "Set1_7-2-3"])
    parser.add_argument("--time", type=float, default=30)
    parser.add_argument("--screen", choices=["default", "on", "off"], default="default")
    parser.add_argument(
        "--global-cert", choices=["default", "on", "off"], default="default",
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output/results/m7_optimization_20260831")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"proof_{args.label}_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
    source = ROOT / "models/routing/variants/model_7/exact_solver.py"
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    with output.open("x", encoding="utf-8") as stream:
        for instance in args.instances:
            problem = _problem(ROOT / f"data/zhou_later_pickup/small/standard_same_day/{instance}.csv", _fleet())
            vps = problem.fleet.e2_per_sat_count
            replay = args.output_dir / f"m4_exact_{instance}.json"
            if not replay.exists():
                e1, e2, assignment, info = solve_m4(problem, vps, time_limit=120,
                    verbose=False, return_info=True, return_incumbent_on_time_limit=True,
                    formulation_options={"prune_e2_arcs": True})
                if e1 is None:
                    raise RuntimeError(f"No M4 incumbent for {instance}")
                replay.write_text(json.dumps({"e1": e1, "e2": e2, "assignment": assignment,
                    "objective": info["objective"]}, ensure_ascii=False), encoding="utf-8")
            data = json.loads(replay.read_text(encoding="utf-8"))
            warm_audit = audit_exact_solution(problem, 4, data["e1"], data["e2"],
                assignment=data["assignment"], objective=data["objective"])
            if not warm_audit["valid"]:
                raise RuntimeError(f"Invalid replay: {warm_audit}")
            options = {} if args.screen == "default" else {"partition_relaxation_screen": args.screen == "on"}
            if args.global_cert != "default":
                options["use_global_relaxation_certificate"] = args.global_cert == "on"
            started = time.perf_counter()
            e1, e2, assignment, info = solve_m7(problem, vehicles_per_satellite=vps,
                time_limit=args.time, verbose=False, return_info=True,
                return_incumbent_on_time_limit=True, warm_start_routes=(data["e1"], data["e2"]),
                warm_start_assignment=data["assignment"], warm_start_objective=data["objective"], **options)
            row = {"label": args.label, "instance": instance, "time_budget": args.time,
                "screen": args.screen, "global_certificate": args.global_cert,
                "source_sha256": fingerprint,
                "replay_sha256": hashlib.sha256(replay.read_bytes()).hexdigest(),
                "warm_start_audit": warm_audit,
                "wall_time": time.perf_counter() - started, "routes_e1": e1, "routes_e2": e2,
                **info}
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            print(json.dumps({key: row.get(key) for key in ("instance", "label", "status",
                "objective", "best_bound", "mip_gap", "wall_time", "assignment_partitions_evaluated",
                "assignment_partitions_resolved", "partition_relaxation_screened",
                "partition_relaxation_pruned", "assignment_partition_retries")}), flush=True)
    print(f"Wrote {output}", flush=True)


if __name__ == "__main__":
    main()
