"""Compare the partition solver and Benders with equal wall-time budgets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from configuration.loader import PROJECT_ROOT, load_json, resolve_project_path
from models.routing.common.data_loader import parse_dataset
from models.routing.common.domain.instance import ProblemInstance
from models.routing.variants.model_7.exact_solver import solve_exact


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", action="append", required=True)
    parser.add_argument("--time", type=float, default=30.0)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--subproblem-time", type=float, default=5.0)
    parser.add_argument("--fleet", default="config/routing/validation/zhou_small.json")
    parser.add_argument("--output-dir", default="output/experiments/model7_benders_comparison")
    args = parser.parse_args(argv)
    out = resolve_project_path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    fleet = load_json(args.fleet)
    summaries = []
    for dataset in args.dataset:
        path = resolve_project_path(dataset)
        for algorithm in ("partition", "benders"):
            nodes, _ = parse_dataset(str(path))
            problem = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
            problem.aggregate_demands_from_pre_assignment()
            kwargs = {"benders_options": {"subproblem_time_limit": args.subproblem_time}} if algorithm == "benders" else {}
            r1, r2, assignment, info = solve_exact(
                problem, algorithm=algorithm, time_limit=args.time,
                verbose=False, return_info=True, return_incumbent_on_time_limit=True,
                gurobi_params={"Threads": args.threads, "Seed": 42}, **kwargs)
            payload = {"dataset": str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else str(path),
                       "algorithm": algorithm, "time_limit": args.time,
                       "threads": args.threads, "fleet": fleet,
                       "routes_e1": r1, "routes_e2": r2, "assignment": assignment, "info": info}
            target = out / f"{path.stem}_{algorithm}.json"
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            summary = {"dataset": path.name, "algorithm": algorithm, **{
                field: info.get(field) for field in (
                    "status", "objective", "best_bound", "mip_gap", "proven_optimal", "runtime",
                    "assignment_partitions_evaluated", "iterations", "optimality_cuts", "feasibility_cuts")}}
            summaries.append(summary)
            (out / "summary.json").write_text(
                json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
