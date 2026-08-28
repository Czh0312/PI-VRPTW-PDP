# experiments/run_beijing_alns_comparison.py
# -*- coding: utf-8 -*-
"""
Quick ALNS comparison for the Beijing fresh datasets.

Runs Model 3/5/6 ALNS on selected CSV files and writes:
- output/beijing_alns_comparison/summary.csv
- output/beijing_alns_comparison/summary.json
- output/beijing_alns_comparison/logs/*.txt
"""
import argparse
import csv
import io
import json
import re
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.routing.common.domain.instance import ProblemInstance
from models.routing.variants.model_3.alns_solver import solve_alns as solve_m3_alns
from models.routing.variants.model_5.alns_solver import solve_alns as solve_m5_alns
from models.routing.variants.model_6.alns_solver import solve_alns as solve_m6_alns
from models.routing.common.data_loader import parse_dataset
from models.routing.common.task_builder import build_tasks_for_model


SOLVERS = {
    3: solve_m3_alns,
    5: solve_m5_alns,
    6: solve_m6_alns,
}


def load_fleet_config():
    with open(PROJECT_ROOT / "config" / "routing" / "validation" / "zhou_small.json", "r", encoding="utf-8") as f:
        return json.load(f)


def to_phys(node_id):
    if node_id.startswith("TRANS_PICK_") or node_id.startswith("TRANS_DROP_"):
        payload = node_id.split("_", 2)[-1]
        return payload.rsplit("@", 1)[-1]
    if node_id.endswith(("d", "p")) and len(node_id) > 1 and not node_id.startswith("TRANS"):
        return node_id[:-1]
    return node_id


def route_distance(problem, route):
    return sum(
        problem.distance(to_phys(route[i]), to_phys(route[i + 1]))
        for i in range(len(route) - 1)
    )


def total_cost(problem, e1_routes, e2_routes_by_sat):
    cost = sum(route_distance(problem, route) for route in e1_routes)
    for routes in e2_routes_by_sat.values():
        cost += sum(route_distance(problem, route) for route in routes)
    return cost


def count_e2_routes(e2_routes_by_sat):
    return sum(len(routes) for routes in e2_routes_by_sat.values())


def count_transshipments_from_routes(e2_routes_by_sat):
    return sum(
        1
        for routes in e2_routes_by_sat.values()
        for route in routes
        for node_id in route
        if node_id.startswith("TRANS_DROP_")
    )


def parse_log_metrics(log_text):
    metrics = {}
    patterns = {
        "reported_best_cost": r"最优目标值:\s*([0-9.]+)",
        "transshipments": r"中转操作数:\s*(\d+)",
        "stage1_cost": r"阶段一\(无中转\):\s*([0-9.]+)",
        "stage2_cost": r"阶段二\(中转后\):\s*([0-9.]+)",
    }
    for key, pattern in patterns.items():
        matches = re.findall(pattern, log_text)
        if not matches:
            continue
        value = matches[-1]
        metrics[key] = int(value) if key == "transshipments" else float(value)
    return metrics


def run_one(data_path, model, seed, max_time, max_iterations, fleet_cfg, args):
    nodes_dict, _ = parse_dataset(str(data_path))
    problem = ProblemInstance(nodes_dict, fleet_cfg, speed=fleet_cfg.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, model)

    kwargs = {
        "problem": problem,
        "e1_tasks": e1_tasks,
        "e2_tasks_by_sat": e2_tasks_by_sat,
        "vehicles_per_satellite": problem.fleet.e2_per_sat_count,
        "max_iterations": max_iterations,
        "max_time": max_time,
        "seed": seed,
        "verbose": True,
    }
    if model in (3, 6):
        kwargs["TFp"] = args.tfp
    if model == 5:
        kwargs.update({
            "transshipment_candidate_mode": args.trans_mode,
            "transshipment_time_budget": args.trans_budget,
            "transshipment_interval": args.trans_interval,
            "transshipment_min_net_saving": args.min_net,
            "transshipment_max_total": args.max_trans,
            "transshipment_max_per_satellite": args.max_trans_per_sat,
        })
    if model == 6:
        kwargs.update({
            "stage1_time_ratio": args.stage1_ratio,
            "transshipment_time_budget": args.trans_budget,
            "transshipment_min_net_saving": args.min_net,
            "transshipment_max_total": args.max_trans,
            "transshipment_max_per_satellite": args.max_trans_per_sat,
        })

    log_buf = io.StringIO()
    start = time.time()
    status = "OK"
    try:
        with redirect_stdout(log_buf):
            e1_routes, e2_routes_by_sat_res = SOLVERS[model](**kwargs)
        elapsed = time.time() - start
        if not e1_routes or not e2_routes_by_sat_res:
            status = "INFEASIBLE"
            cost = None
            e1_count = 0
            e2_count = 0
            trans_count = 0
        else:
            cost = total_cost(problem, e1_routes, e2_routes_by_sat_res)
            e1_count = len(e1_routes)
            e2_count = count_e2_routes(e2_routes_by_sat_res)
            trans_count = count_transshipments_from_routes(e2_routes_by_sat_res)
    except Exception as exc:
        elapsed = time.time() - start
        status = f"ERROR: {exc}"
        cost = None
        e1_count = 0
        e2_count = 0
        trans_count = 0

    log_text = log_buf.getvalue()
    log_metrics = parse_log_metrics(log_text)
    if "transshipments" in log_metrics:
        trans_count = log_metrics["transshipments"]

    result = {
        "dataset": data_path.stem,
        "model": model,
        "seed": seed,
        "max_time": max_time,
        "max_iterations": max_iterations,
        "status": status,
        "cost": round(cost, 4) if cost is not None else None,
        "elapsed": round(elapsed, 4),
        "e1_routes": e1_count,
        "e2_routes": e2_count,
        "transshipments": trans_count,
    }
    result.update(log_metrics)
    return result, log_text


def add_gaps(rows):
    by_case = {}
    for row in rows:
        key = (row["dataset"], row["seed"])
        by_case.setdefault(key, {})[row["model"]] = row

    for models in by_case.values():
        base = models.get(3, {}).get("cost")
        for model, row in models.items():
            if base and row.get("cost") is not None:
                row["gap_vs_m3_pct"] = round((row["cost"] - base) / base * 100.0, 4)
                row["saving_vs_m3"] = round(base - row["cost"], 4)
            else:
                row["gap_vs_m3_pct"] = None
                row["saving_vs_m3"] = None


def write_outputs(rows, logs, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    log_dir = out_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    for name, text in logs.items():
        (log_dir / f"{name}.txt").write_text(text, encoding="utf-8")

    if rows:
        fieldnames = [
            "dataset", "model", "seed", "max_time", "max_iterations", "status",
            "cost", "gap_vs_m3_pct", "saving_vs_m3", "elapsed",
            "e1_routes", "e2_routes", "transshipments",
            "stage1_cost", "stage2_cost", "reported_best_cost",
        ]
        with open(out_dir / "summary.csv", "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    (out_dir / "summary.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def print_summary(rows):
    print("\nALNS comparison summary")
    print("-" * 100)
    print(f"{'Dataset':<24} {'M':<3} {'Seed':<5} {'Status':<12} {'Cost':>10} {'Gap%':>9} {'Save':>10} {'Trans':>6} {'Time':>8}")
    for row in rows:
        cost = "-" if row["cost"] is None else f"{row['cost']:.2f}"
        gap = "-" if row["gap_vs_m3_pct"] is None else f"{row['gap_vs_m3_pct']:.2f}"
        saving = "-" if row["saving_vs_m3"] is None else f"{row['saving_vs_m3']:.2f}"
        print(
            f"{row['dataset']:<24} M{row['model']:<2} {row['seed']:<5} "
            f"{row['status']:<12} {cost:>10} {gap:>9} {saving:>10} "
            f"{row['transshipments']:>6} {row['elapsed']:>8.2f}"
        )


def main():
    parser = argparse.ArgumentParser(description="Run Beijing fresh ALNS comparison for M3/M5/M6.")
    parser.add_argument(
        "--data",
        nargs="+",
        default=[
            "data/beijing_fresh/beijing_fresh_30.csv",
            "data/beijing_fresh/beijing_fresh_60.csv",
            "data/beijing_fresh/beijing_fresh_100.csv",
        ],
        help="Dataset CSV paths.",
    )
    parser.add_argument("--models", nargs="+", type=int, default=[3, 5, 6], choices=[3, 5, 6])
    parser.add_argument("--seeds", nargs="+", type=int, default=[42])
    parser.add_argument("--time", type=float, default=30.0, help="ALNS time limit per run.")
    parser.add_argument("--iterations", type=int, default=5000)
    parser.add_argument("--tfp", type=float, default=9999.0)
    parser.add_argument("--stage1-ratio", type=float, default=0.95)
    parser.add_argument("--trans-mode", default="all_sampled")
    parser.add_argument("--trans-budget", type=float, default=2.0)
    parser.add_argument("--trans-interval", type=int, default=20)
    parser.add_argument("--min-net", type=float, default=0.01)
    parser.add_argument("--max-trans", type=int, default=None)
    parser.add_argument("--max-trans-per-sat", type=int, default=None)
    parser.add_argument(
        "--out-dir",
        default="output/beijing_alns_comparison",
        help="Output directory for summaries and logs.",
    )
    args = parser.parse_args()

    fleet_cfg = load_fleet_config()
    rows = []
    logs = {}

    for data in args.data:
        data_path = PROJECT_ROOT / data
        if not data_path.exists():
            raise FileNotFoundError(data_path)
        for seed in args.seeds:
            for model in args.models:
                print(f"Running {data_path.stem} M{model} seed={seed} time={args.time}s ...", flush=True)
                row, log_text = run_one(
                    data_path=data_path,
                    model=model,
                    seed=seed,
                    max_time=args.time,
                    max_iterations=args.iterations,
                    fleet_cfg=fleet_cfg,
                    args=args,
                )
                rows.append(row)
                logs[f"{data_path.stem}_M{model}_seed{seed}"] = log_text
                print(
                    f"  -> {row['status']} cost={row['cost']} "
                    f"trans={row['transshipments']} elapsed={row['elapsed']:.2f}s",
                    flush=True,
                )

    add_gaps(rows)
    out_dir = PROJECT_ROOT / args.out_dir
    write_outputs(rows, logs, out_dir)
    print_summary(rows)
    print(f"\nWrote outputs to: {out_dir}")


if __name__ == "__main__":
    main()
