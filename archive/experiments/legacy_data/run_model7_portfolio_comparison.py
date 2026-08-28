# experiments/run_model7_portfolio_comparison.py
# -*- coding: utf-8 -*-
"""Run a broader Model 3 vs Model 7 portfolio comparison."""
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
from models.routing.variants.model_7.alns_solver import solve_alns as solve_m7  # noqa: E402


PORTFOLIO_VARIANTS = [
    {
        "label": "m7_greedy_H1",
        "strategy": "greedy",
        "depth": 1,
        "pool_size": 4,
        "beam_width": 3,
        "prune": False,
        "quality_gap": None,
        "final_refine": False,
        "baseline_guard": False,
    },
    {
        "label": "m7_beam_H2",
        "strategy": "beam",
        "depth": 2,
        "pool_size": 4,
        "beam_width": 3,
        "prune": False,
        "quality_gap": None,
        "final_refine": False,
        "baseline_guard": False,
    },
    {
        "label": "m7_cons_prune_refine",
        "strategy": "conservative",
        "depth": 1,
        "pool_size": 4,
        "beam_width": 3,
        "prune": True,
        "quality_gap": None,
        "final_refine": True,
        "baseline_guard": False,
    },
    {
        "label": "m7_guard_greedy",
        "strategy": "greedy",
        "depth": 1,
        "pool_size": 4,
        "beam_width": 3,
        "prune": False,
        "quality_gap": None,
        "final_refine": False,
        "baseline_guard": True,
    },
    {
        "label": "m7_guard_route_refine",
        "strategy": "greedy",
        "depth": 1,
        "pool_size": 4,
        "beam_width": 3,
        "prune": False,
        "quality_gap": None,
        "final_refine": False,
        "baseline_guard": True,
        "route_local_refine": True,
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
        stage1_time_ratio=0.95,
        model7_depth=variant.get("depth", 1),
        model7_schedule="fixed",
        model7_selection="last",
        model7_strategy=variant.get("strategy", "greedy"),
        model7_pool_size=variant.get("pool_size", 4),
        model7_alpha=0.8,
        model7_beam_width=variant.get("beam_width", 3),
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


def _dataset_label(path):
    return f"{path.parent.name}_{path.stem}"


def _resolve_datasets(items):
    if not items:
        return sorted((ROOT / "data" / "10_customers").glob("*.csv"))
    paths = []
    for item in items:
        path = Path(item)
        if not path.is_absolute():
            path = ROOT / path
        if path.is_dir():
            paths.extend(sorted(path.glob("*.csv")))
        else:
            paths.append(path)
    return paths


def main():
    parser = argparse.ArgumentParser(
        description="Run broad Model 3 vs Model 7 portfolio comparison.")
    parser.add_argument("--datasets", nargs="*", default=None)
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
    parser.add_argument("--conservative-min-saving", type=float, default=1.0)
    parser.add_argument("--prune-min-improvement", type=float, default=0.01)
    parser.add_argument("--prune-max-rounds", type=int, default=1)
    parser.add_argument("--final-refine-depth", type=int, default=10)
    parser.add_argument("--route-local-refine-rounds", type=int, default=2)
    parser.add_argument("--route-local-refine-max-moves", type=int,
                        default=20)
    args = parser.parse_args()

    datasets = _resolve_datasets(args.datasets)
    with open(DEFAULT_FLEET, "r", encoding="utf-8") as f:
        fleet_cfg = json.load(f)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = []
    for index, data_path in enumerate(datasets, start=1):
        label = _dataset_label(data_path)
        print(f"\n[{index}/{len(datasets)}] {label}")

        base_args = _make_args(args)
        model3 = _run_one(3, solve_m3, data_path, fleet_cfg, base_args)
        model3["label"] = "model3"
        print(f"  Model 3: {model3['cost']}")

        variants = []
        for variant in PORTFOLIO_VARIANTS:
            v_args = _make_args(args, variant)
            result = _run_one(7, solve_m7, data_path, fleet_cfg, v_args)
            result["label"] = variant["label"]
            variants.append(result)
            print(f"  {variant['label']}: {result['cost']}")

        feasible_variants = [
            result for result in variants
            if result["success"] and result["cost"] is not None
        ]
        best_m7 = min(feasible_variants, key=lambda item: item["cost"])
        gap = best_m7["cost"] - model3["cost"]
        gap_pct = gap / model3["cost"] * 100.0
        if gap < -1e-9:
            verdict = "better"
        elif abs(gap) < 1e-9:
            verdict = "tie"
        else:
            verdict = "worse"

        row = {
            "dataset": label,
            "model3_cost": model3["cost"],
            "model3_trans": model3["transshipments"],
            "best_m7_cost": best_m7["cost"],
            "best_m7_trans": best_m7["transshipments"],
            "best_m7_label": best_m7["label"],
            "gap": gap,
            "gap_pct": gap_pct,
            "verdict": verdict,
        }
        for variant in variants:
            row[f"{variant['label']}_cost"] = variant["cost"]
            row[f"{variant['label']}_trans"] = variant["transshipments"]
        rows.append(row)

    summary_label = (
        "all10" if args.datasets is None else f"{len(datasets)}datasets"
    )
    csv_path = OUT_DIR / (
        f"model7_portfolio_{summary_label}_iter{args.iterations}_"
        f"seed{args.seed}_summary.csv"
    )
    fields = [
        "dataset", "model3_cost", "model3_trans",
        "best_m7_cost", "best_m7_trans", "best_m7_label",
        "gap", "gap_pct", "verdict",
    ]
    for variant in PORTFOLIO_VARIANTS:
        fields.extend([f"{variant['label']}_cost",
                       f"{variant['label']}_trans"])
    _write_csv(csv_path, fields, rows)

    md_path = OUT_DIR / (
        f"model7_portfolio_{summary_label}_iter{args.iterations}_"
        f"seed{args.seed}_summary.md"
    )
    _write_markdown(md_path, rows)
    detail_md_path = OUT_DIR / (
        f"model7_portfolio_{summary_label}_iter{args.iterations}_"
        f"seed{args.seed}_detail.md"
    )
    _write_detail_markdown(detail_md_path, rows)

    better = sum(1 for row in rows if row["verdict"] == "better")
    tie = sum(1 for row in rows if row["verdict"] == "tie")
    worse = sum(1 for row in rows if row["verdict"] == "worse")
    avg_gap_pct = sum(row["gap_pct"] for row in rows) / max(len(rows), 1)

    print("\nPortfolio summary")
    print(f"better={better}, tie={tie}, worse={worse}, "
          f"avg_gap_pct={avg_gap_pct:.2f}%")
    print(f"Markdown: {md_path}")
    print(f"Detail Markdown: {detail_md_path}")
    print(f"CSV: {csv_path}")


def _write_csv(path, fields, rows):
    import csv
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(path, rows):
    lines = [
        "# Model 3 vs Model 7 Portfolio",
        "",
        "gap = Best M7 - Model 3；负数表示 Model 7 更好。",
        "",
        "| 算例 | Model 3 | Best M7 | M7策略 | gap | gap% | 结论 |",
        "| --- | ---: | ---: | --- | ---: | ---: | --- |",
    ]
    label_map = {"better": "更好", "tie": "持平", "worse": "更差"}
    for row in rows:
        lines.append(
            f"| {row['dataset']} "
            f"| {row['model3_cost']:.2f} / {row['model3_trans']} "
            f"| {row['best_m7_cost']:.2f} / {row['best_m7_trans']} "
            f"| {row['best_m7_label']} "
            f"| {row['gap']:.2f} "
            f"| {row['gap_pct']:.2f}% "
            f"| {label_map[row['verdict']]} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fmt_cost(row, label):
    cost = row.get(f"{label}_cost")
    trans = row.get(f"{label}_trans")
    if cost in (None, ""):
        return "-"
    return f"{float(cost):.2f} / {trans}"


def _write_detail_markdown(path, rows):
    variant_labels = [variant["label"] for variant in PORTFOLIO_VARIANTS]
    header = [
        "算例", "Model 3", *variant_labels, "Best M7", "gap%", "结论"
    ]
    lines = [
        "# Model 3 vs Model 7 Portfolio Detail",
        "",
        "单元格格式为 `成本 / 中转数`；gap% = Best M7 - Model 3。",
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] + ["---:"] * (len(header) - 1)) + " |",
    ]
    label_map = {"better": "更好", "tie": "持平", "worse": "更差"}
    for row in rows:
        values = [
            row["dataset"],
            f"{float(row['model3_cost']):.2f} / {row['model3_trans']}",
        ]
        values.extend(_fmt_cost(row, label) for label in variant_labels)
        values.extend([
            f"{float(row['best_m7_cost']):.2f} / {row['best_m7_trans']}"
            f" ({row['best_m7_label']})",
            f"{float(row['gap_pct']):.2f}%",
            label_map[row["verdict"]],
        ])
        lines.append("| " + " | ".join(values) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
