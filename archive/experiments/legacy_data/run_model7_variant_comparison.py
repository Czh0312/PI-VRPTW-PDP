# experiments/run_model7_variant_comparison.py
# -*- coding: utf-8 -*-
"""Compare Model 7 transshipment probing variants against Model 5/6."""
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.runs.run_model567_comparison import (  # noqa: E402
    DEFAULT_DATA,
    DEFAULT_FLEET,
    OUT_DIR,
    _run_one,
)
from models.routing.variants.model_3.alns_solver import solve_alns as solve_m3  # noqa: E402
from models.routing.variants.model_5.alns_solver import solve_alns as solve_m5  # noqa: E402
from models.routing.variants.model_6.alns_solver import solve_alns as solve_m6  # noqa: E402
from models.routing.variants.model_7.alns_solver import solve_alns as solve_m7  # noqa: E402


MODEL5_VARIANTS = [
    {
        "label": "m7_k1_single",
        "depth": 1,
        "schedule": "fixed",
        "selection": "last",
        "note": "每轮最多1次中转试探",
    },
    {
        "label": "m7_k2_chain",
        "depth": 2,
        "schedule": "fixed",
        "selection": "last",
        "note": "每轮从少到多，最多连续2次",
    },
    {
        "label": "m7_k3_chain",
        "depth": 3,
        "schedule": "fixed",
        "selection": "last",
        "note": "每轮从少到多，最多连续3次",
    },
    {
        "label": "m7_k3_best_prefix",
        "depth": 3,
        "schedule": "fixed",
        "selection": "best_prefix",
        "note": "0到3次中转前缀中保留最优",
    },
    {
        "label": "m7_k3_decreasing",
        "depth": 3,
        "schedule": "decreasing",
        "selection": "last",
        "note": "前期最多3次，中期2次，后期1次",
    },
]


def _clone_args(args, depth=1, schedule="fixed", selection="last"):
    return SimpleNamespace(
        data=args.data,
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
        model7_depth=depth,
        model7_schedule=schedule,
        model7_selection=selection,
        model7_strategy="greedy",
        model7_pool_size=4,
        model7_alpha=0.8,
        model7_beam_width=3,
        model7_conservative_min_saving=1.0,
        model7_quality_gap=None,
        model7_prune=False,
        model7_prune_min_improvement=0.01,
        model7_prune_max_rounds=1,
        model7_final_refine=False,
        model7_final_refine_depth=10,
        model7_baseline_guard=False,
        model7_baseline_iterations=None,
        model7_baseline_time=None,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Compare Model 7 probing-depth variants.")
    parser.add_argument("--data", default=str(DEFAULT_DATA))
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--time", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--transshipment-time-budget", type=float, default=0.05)
    parser.add_argument("--sample-pickups", type=int, default=5)
    parser.add_argument("--sample-locations", type=int, default=3)
    parser.add_argument("--sample-carriers", type=int, default=3)
    parser.add_argument("--position-top-k", type=int, default=3)
    parser.add_argument("--min-net-saving", type=float, default=0.01)
    parser.add_argument("--max-total", type=int, default=None)
    parser.add_argument("--max-per-satellite", type=int, default=None)
    parser.add_argument("--stage1-time-ratio", type=float, default=0.95)
    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.is_absolute():
        data_path = ROOT / data_path

    with open(DEFAULT_FLEET, "r", encoding="utf-8") as f:
        fleet_cfg = json.load(f)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data_label = f"{data_path.parent.name}_{data_path.stem}"

    rows = []
    baseline_args = _clone_args(args)
    for label, model_id, solver_fn in [
            ("m3_baseline", 3, solve_m3),
            ("m5_baseline", 5, solve_m5),
            ("m6_baseline", 6, solve_m6)]:
        result = _run_one(model_id, solver_fn, data_path, fleet_cfg,
                          baseline_args)
        result["label"] = label
        result["note"] = "baseline"
        rows.append(result)
        log_path = OUT_DIR / (
            f"{label}_{data_label}_iter{args.iterations}_seed{args.seed}.log"
        )
        log_path.write_text(result["log"], encoding="utf-8")

    for variant in MODEL5_VARIANTS:
        v_args = _clone_args(
            args,
            depth=variant["depth"],
            schedule=variant["schedule"],
            selection=variant["selection"],
        )
        result = _run_one(7, solve_m7, data_path, fleet_cfg, v_args)
        result["label"] = variant["label"]
        result["note"] = variant["note"]
        rows.append(result)
        log_path = OUT_DIR / (
            f"{variant['label']}_{data_label}_iter{args.iterations}_"
            f"seed{args.seed}.log"
        )
        log_path.write_text(result["log"], encoding="utf-8")

    summary_path = OUT_DIR / (
        f"model7_variants_{data_label}_iter{args.iterations}_"
        f"seed{args.seed}_summary.csv"
    )
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("label,model,success,cost,stage1_cost,transshipments,"
                "trials,steps,trial_applied,elapsed,note\n")
        for r in rows:
            f.write(
                f"{r['label']},{r['model']},{r['success']},{r['cost']},"
                f"{r['stage1_cost']},{r['transshipments']},"
                f"{r['trials']},{r['steps']},{r['trial_applied']},"
                f"{r['elapsed']:.2f},{r['note']}\n"
            )

    print("Model 7 probing variant comparison")
    print(f"Data: {data_path}")
    print(f"Iterations: {args.iterations}, time limit/run: {args.time}s, "
          f"seed: {args.seed}")
    print("label                 cost      trans  trials   steps  applied  time")
    for r in rows:
        cost = "-" if r["cost"] is None else f"{r['cost']:.2f}"
        trials = "-" if r["trials"] is None else str(r["trials"])
        steps = "-" if r["steps"] is None else str(r["steps"])
        applied = "-" if r["trial_applied"] is None else str(r["trial_applied"])
        print(f"{r['label']:<21} {cost:>8}  "
              f"{r['transshipments']:>5}  {trials:>6}  "
              f"{steps:>6}  {applied:>7}  {r['elapsed']:>5.2f}s")
    print(f"Summary saved to: {summary_path}")


if __name__ == "__main__":
    main()
