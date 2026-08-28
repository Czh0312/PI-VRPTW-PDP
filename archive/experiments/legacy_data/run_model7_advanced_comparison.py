# experiments/run_model7_advanced_comparison.py
# -*- coding: utf-8 -*-
"""Compare additional Model 7 transshipment-control improvements."""
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
    "data/10_customers/C103.csv",
    "data/10_customers/R101.csv",
    "data/10_customers/R103.csv",
    "data/10_customers/RC101.csv",
    "data/10_customers/RC103.csv",
    "data/50_customers/C101.csv",
]


ADVANCED_VARIANTS = [
    {
        "label": "m7_base_greedy",
        "strategy": "greedy",
        "depth": 1,
        "prune": False,
        "quality_gap": None,
        "final_refine": False,
        "note": "基础贪心 H1",
    },
    {
        "label": "m7_conservative",
        "strategy": "conservative",
        "depth": 1,
        "prune": False,
        "quality_gap": None,
        "final_refine": False,
        "note": "强收益准入",
    },
    {
        "label": "m7_greedy_prune",
        "strategy": "greedy",
        "depth": 1,
        "prune": True,
        "quality_gap": None,
        "final_refine": False,
        "note": "贪心 + 中转撤销",
    },
    {
        "label": "m7_cons_prune",
        "strategy": "conservative",
        "depth": 1,
        "prune": True,
        "quality_gap": None,
        "final_refine": False,
        "note": "保守 + 中转撤销",
    },
    {
        "label": "m7_cons_prune_gate",
        "strategy": "conservative",
        "depth": 1,
        "prune": True,
        "quality_gap": 0.02,
        "final_refine": False,
        "note": "保守 + 撤销 + 2%质量门控",
    },
    {
        "label": "m7_cons_prune_refine",
        "strategy": "conservative",
        "depth": 1,
        "prune": True,
        "quality_gap": None,
        "final_refine": True,
        "note": "保守 + 撤销 + 最终精修",
    },
    {
        "label": "m7_guard_greedy",
        "strategy": "greedy",
        "depth": 1,
        "prune": False,
        "quality_gap": None,
        "final_refine": False,
        "baseline_guard": True,
        "note": "基线保护 + 贪心中转",
    },
    {
        "label": "m7_guard_cons_gate",
        "strategy": "conservative",
        "depth": 1,
        "prune": True,
        "quality_gap": 0.02,
        "final_refine": False,
        "baseline_guard": True,
        "note": "基线保护 + 保守撤销门控",
    },
    {
        "label": "m7_guard_refine",
        "strategy": "conservative",
        "depth": 1,
        "prune": False,
        "quality_gap": None,
        "final_refine": True,
        "baseline_guard": True,
        "note": "基线保护 + 最终中转精修",
    },
    {
        "label": "m7_guard_route_refine",
        "strategy": "greedy",
        "depth": 1,
        "prune": False,
        "quality_gap": None,
        "final_refine": False,
        "baseline_guard": True,
        "route_local_refine": True,
        "note": "基线保护 + 路径顺序精修",
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
        model7_pool_size=4,
        model7_alpha=0.8,
        model7_beam_width=3,
        model7_conservative_min_saving=args.conservative_min_saving,
        model7_quality_gap=variant.get("quality_gap"),
        model7_prune=variant.get("prune", False),
        model7_prune_min_improvement=args.prune_min_improvement,
        model7_prune_max_rounds=args.prune_max_rounds,
        model7_final_refine=variant.get("final_refine", False),
        model7_final_refine_depth=args.final_refine_depth,
        model7_baseline_guard=variant.get("baseline_guard", False),
        model7_baseline_iterations=variant.get("baseline_iterations"),
        model7_baseline_time=variant.get("baseline_time"),
        model7_route_local_refine=variant.get(
            "route_local_refine", False),
        model7_route_local_refine_rounds=args.route_local_refine_rounds,
        model7_route_local_refine_max_moves=args.route_local_refine_max_moves,
    )


def _safe_label(data_path):
    return f"{data_path.parent.name}_{data_path.stem}"


def main():
    parser = argparse.ArgumentParser(
        description="Compare additional Model 7 improvements.")
    parser.add_argument("--datasets", nargs="*", default=DEFAULT_DATASETS)
    parser.add_argument("--iterations", type=int, default=300)
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
    parser.add_argument("--conservative-min-saving", type=float, default=1.0)
    parser.add_argument("--prune-min-improvement", type=float, default=0.01)
    parser.add_argument("--prune-max-rounds", type=int, default=1)
    parser.add_argument("--final-refine-depth", type=int, default=10)
    parser.add_argument("--route-local-refine-rounds", type=int, default=2)
    parser.add_argument("--route-local-refine-max-moves", type=int,
                        default=20)
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

        for variant in ADVANCED_VARIANTS:
            v_args = _make_args(args, variant)
            result = _run_one(7, solve_m7, data_path, fleet_cfg, v_args)
            result["dataset"] = data_label
            result["label"] = variant["label"]
            result["note"] = variant["note"]
            rows.append(result)

        all_rows.extend(rows)
        summary_path = OUT_DIR / (
            f"model7_advanced_{data_label}_iter{args.iterations}_"
            f"seed{args.seed}_summary.csv"
        )
        _write_summary(summary_path, rows)
        _print_dataset_summary(data_path, rows)

    combined_label = (f"{len(args.datasets)}datasets" if len(args.datasets) > 1
                      else _safe_label(Path(args.datasets[0])))
    combined_path = OUT_DIR / (
        f"model7_advanced_{combined_label}_combined_"
        f"iter{args.iterations}_seed{args.seed}_summary.csv"
    )
    _write_summary(combined_path, all_rows)
    print(f"\nCombined summary saved to: {combined_path}")


def _write_summary(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        f.write("dataset,label,model,success,cost,stage1_cost,transshipments,"
                "trials,steps,trial_applied,pruned,route_refine_moves,"
                "elapsed,note\n")
        for r in rows:
            f.write(
                f"{r.get('dataset')},{r['label']},{r['model']},"
                f"{r['success']},{r['cost']},{r['stage1_cost']},"
                f"{r['transshipments']},{r['trials']},{r['steps']},"
                f"{r['trial_applied']},{r['pruned']},"
                f"{r['route_refine_moves']},"
                f"{r['elapsed']:.2f},{r['note']}\n"
            )


def _print_dataset_summary(data_path, rows):
    print("\nModel 7 advanced comparison")
    print(f"Data: {data_path}")
    print("label                    cost      trans  trials   steps  add  rm  rls  time")
    for r in rows:
        cost = "-" if r["cost"] is None else f"{r['cost']:.2f}"
        trials = "-" if r["trials"] is None else str(r["trials"])
        steps = "-" if r["steps"] is None else str(r["steps"])
        add = "-" if r["trial_applied"] is None else str(r["trial_applied"])
        rm = "-" if r["pruned"] is None else str(r["pruned"])
        rls = "-" if r["route_refine_moves"] is None else str(
            r["route_refine_moves"])
        print(f"{r['label']:<24} {cost:>8}  "
              f"{r['transshipments']:>5}  {trials:>6}  "
              f"{steps:>6}  {add:>3}  {rm:>2}  {rls:>3}  "
              f"{r['elapsed']:>5.2f}s")


if __name__ == "__main__":
    main()
