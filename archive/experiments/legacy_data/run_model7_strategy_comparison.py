# experiments/run_model7_strategy_comparison.py
# -*- coding: utf-8 -*-
"""Compare Model 7 transshipment-selection strategies on several instances."""
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.runs.run_model567_comparison import (  # noqa: E402
    DEFAULT_FLEET,
    OUT_DIR,
    _run_one,
)
from models.routing.variants.model_3.alns_solver import solve_alns as solve_m3  # noqa: E402
from models.routing.variants.model_5.alns_solver import solve_alns as solve_m5  # noqa: E402
from models.routing.variants.model_6.alns_solver import solve_alns as solve_m6  # noqa: E402
from models.routing.variants.model_7.alns_solver import solve_alns as solve_m7  # noqa: E402


DEFAULT_DATASETS = [
    "data/10_customers/C101.csv",
    "data/10_customers/R101.csv",
    "data/10_customers/RC101.csv",
    "data/10_customers/C103.csv",
    "data/10_customers/R103.csv",
    "data/10_customers/RC103.csv",
    "data/50_customers/C101.csv",
]


MODEL5_STRATEGIES = [
    {
        "label": "m7_A_greedy_H1",
        "strategy": "greedy",
        "depth": 1,
        "pool_size": 4,
        "alpha": 0.8,
        "beam_width": 3,
        "conservative_min_saving": 1.0,
        "note": "方案A: 每轮选当前最佳中转",
    },
    {
        "label": "m7_B_range_H1",
        "strategy": "range_random",
        "depth": 1,
        "pool_size": 6,
        "alpha": 0.8,
        "beam_width": 3,
        "conservative_min_saving": 1.0,
        "note": "方案B: 收益范围内加权随机",
    },
    {
        "label": "m7_C_beam_H2",
        "strategy": "beam",
        "depth": 2,
        "pool_size": 4,
        "alpha": 0.8,
        "beam_width": 3,
        "conservative_min_saving": 1.0,
        "note": "方案C: 候选池保留",
    },
    {
        "label": "m7_D_conservative_H1",
        "strategy": "conservative",
        "depth": 1,
        "pool_size": 4,
        "alpha": 0.8,
        "beam_width": 3,
        "conservative_min_saving": 1.0,
        "note": "方案D: 强收益保守筛选",
    },
    {
        "label": "m7_A_greedy_H2",
        "strategy": "greedy",
        "depth": 2,
        "pool_size": 4,
        "alpha": 0.8,
        "beam_width": 3,
        "conservative_min_saving": 1.0,
        "note": "方案A扩展: 最佳中转连续2步",
    },
]


def _make_args(args, variant=None):
    variant = variant or {}
    return SimpleNamespace(
        iterations=args.iterations,
        time=args.time,
        seed=args.seed,
        transshipment_time_budget=args.transshipment_time_budget,
        sample_pickups=args.sample_pickups,
        sample_locations=args.sample_locations,
        sample_carriers=args.sample_carriers,
        position_top_k=args.position_top_k,
        min_net_saving=args.min_net_saving,
        max_total=args.max_total,
        max_per_satellite=args.max_per_satellite,
        stage1_time_ratio=args.stage1_time_ratio,
        model7_depth=variant.get("depth", 1),
        model7_schedule="fixed",
        model7_selection="last",
        model7_strategy=variant.get("strategy", "greedy"),
        model7_pool_size=variant.get("pool_size", 4),
        model7_alpha=variant.get("alpha", 0.8),
        model7_beam_width=variant.get("beam_width", 3),
        model7_conservative_min_saving=variant.get(
            "conservative_min_saving", 1.0),
        model7_quality_gap=variant.get("quality_gap"),
        model7_prune=variant.get("prune", False),
        model7_prune_min_improvement=0.01,
        model7_prune_max_rounds=1,
        model7_final_refine=variant.get("final_refine", False),
        model7_final_refine_depth=10,
        model7_baseline_guard=variant.get("baseline_guard", False),
        model7_baseline_iterations=variant.get("baseline_iterations"),
        model7_baseline_time=variant.get("baseline_time"),
    )


def _safe_label(data_path):
    return f"{data_path.parent.name}_{data_path.stem}"


def main():
    parser = argparse.ArgumentParser(
        description="Compare Model 7 transshipment strategies.")
    parser.add_argument("--datasets", nargs="*", default=DEFAULT_DATASETS)
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--time", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--transshipment-time-budget", type=float, default=0.05)
    parser.add_argument("--sample-pickups", type=int, default=5)
    parser.add_argument("--sample-locations", type=int, default=3)
    parser.add_argument("--sample-carriers", type=int, default=3)
    parser.add_argument("--position-top-k", type=int, default=3)
    parser.add_argument("--min-net-saving", type=float, default=0.01)
    parser.add_argument("--max-total", type=int, default=3)
    parser.add_argument("--max-per-satellite", type=int, default=2)
    parser.add_argument("--stage1-time-ratio", type=float, default=0.95)
    args = parser.parse_args()

    with open(DEFAULT_FLEET, "r", encoding="utf-8") as f:
        fleet_cfg = json.load(f)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_rows = []

    for data_item in args.datasets:
        data_path = Path(data_item)
        if not data_path.is_absolute():
            data_path = ROOT / data_path
        data_label = _safe_label(data_path)
        rows = []

        baseline_args = _make_args(args)
        for label, model_id, solver_fn in [
                ("m3_baseline", 3, solve_m3),
                ("m5_baseline", 5, solve_m5),
                ("m6_baseline", 6, solve_m6)]:
            result = _run_one(model_id, solver_fn, data_path, fleet_cfg,
                              baseline_args)
            result["dataset"] = data_label
            result["label"] = label
            result["note"] = "baseline"
            rows.append(result)

        for variant in MODEL5_STRATEGIES:
            v_args = _make_args(args, variant)
            result = _run_one(7, solve_m7, data_path, fleet_cfg, v_args)
            result["dataset"] = data_label
            result["label"] = variant["label"]
            result["note"] = variant["note"]
            rows.append(result)

        all_rows.extend(rows)

        summary_path = OUT_DIR / (
            f"model7_strategies_{data_label}_iter{args.iterations}_"
            f"seed{args.seed}_summary.csv"
        )
        _write_summary(summary_path, rows)
        _print_dataset_summary(data_path, rows)

    if len(args.datasets) == 1:
        only_path = Path(args.datasets[0])
        if not only_path.is_absolute():
            only_path = ROOT / only_path
        combined_label = _safe_label(only_path)
    else:
        combined_label = f"{len(args.datasets)}datasets"
    combined_path = OUT_DIR / (
        f"model7_strategies_{combined_label}_combined_"
        f"iter{args.iterations}_seed{args.seed}_summary.csv"
    )
    _write_summary(combined_path, all_rows)
    print(f"\nCombined summary saved to: {combined_path}")


def _write_summary(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        f.write("dataset,label,model,success,cost,stage1_cost,transshipments,"
                "trials,steps,trial_applied,elapsed,note\n")
        for r in rows:
            f.write(
                f"{r.get('dataset')},{r['label']},{r['model']},"
                f"{r['success']},{r['cost']},{r['stage1_cost']},"
                f"{r['transshipments']},{r['trials']},{r['steps']},"
                f"{r['trial_applied']},{r['elapsed']:.2f},{r['note']}\n"
            )


def _print_dataset_summary(data_path, rows):
    print("\nModel 7 strategy comparison")
    print(f"Data: {data_path}")
    print("label                    cost      trans  trials   steps  applied  time")
    for r in rows:
        cost = "-" if r["cost"] is None else f"{r['cost']:.2f}"
        trials = "-" if r["trials"] is None else str(r["trials"])
        steps = "-" if r["steps"] is None else str(r["steps"])
        applied = "-" if r["trial_applied"] is None else str(r["trial_applied"])
        print(f"{r['label']:<24} {cost:>8}  "
              f"{r['transshipments']:>5}  {trials:>6}  "
              f"{steps:>6}  {applied:>7}  {r['elapsed']:>5.2f}s")


if __name__ == "__main__":
    main()
