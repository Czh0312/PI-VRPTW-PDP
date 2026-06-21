# experiments/run_model5_v6_stable_comparison.py
# -*- coding: utf-8 -*-
"""Compare current Model 5, V5 portfolio, and V6 stable variants.

The default dataset list matches the 24 large-sample cases used in the
Model 5 optimization notes. V6 is evaluated as a safety portfolio:
best(current compact variants + V1..V5 variants + V6 variants), so V6 cannot
be worse than the previous V5 portfolio under the same run results.
"""
import argparse
import csv
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.run_model345_comparison import (  # noqa: E402
    DEFAULT_FLEET,
    OUT_DIR,
    _run_one,
)
from experiments.run_model5_large_portfolio import (  # noqa: E402
    ITERATIVE_REBALANCE_EXTRA_VARIANTS,
    REBALANCE_EXTRA_VARIANTS,
    STABLE_V6_EXTRA_VARIANTS,
    STAGNATION_EXTRA_VARIANTS,
    SWAP_EXTRA_VARIANTS,
    V1_OPTIMIZED_EXTRA_VARIANTS,
    get_compact_variants,
    _make_args as _make_m5_args,
)
from models.model_2.alns_solver import solve_alns as solve_m2  # noqa: E402
from models.model_3.alns_solver import solve_alns as solve_m3  # noqa: E402
from models.model_5.alns_solver import solve_alns as solve_m5  # noqa: E402


DEFAULT_DATASETS = [
    "data/50_customers/C101.csv",
    "data/50_customers/C201.csv",
    "data/50_customers/R101.csv",
    "data/50_customers/R201.csv",
    "data/50_customers/RC101.csv",
    "data/50_customers/RC201.csv",
    "data/100_customers/C101.csv",
    "data/100_customers/C201.csv",
    "data/100_customers/R101.csv",
    "data/100_customers/R201.csv",
    "data/100_customers/RC101.csv",
    "data/100_customers/RC201.csv",
    "data/50_customers/C109.csv",
    "data/50_customers/C209.csv",
    "data/50_customers/R107.csv",
    "data/50_customers/R207.csv",
    "data/50_customers/RC108.csv",
    "data/50_customers/RC208.csv",
    "data/100_customers/C109.csv",
    "data/100_customers/C209.csv",
    "data/100_customers/R107.csv",
    "data/100_customers/R207.csv",
    "data/100_customers/RC108.csv",
    "data/100_customers/RC208.csv",
]


def _resolve_datasets(items):
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


def _dataset_label(path):
    return f"{path.parent.name}_{path.stem}"


def _variant_args(args, variant=None):
    v_args = _make_m5_args(args, variant)
    v_args.baseline_strong_initial = args.strong_initial_baselines
    return v_args


def _best_success(results):
    feasible = [
        result for result in results
        if result["success"] and result["cost"] is not None
    ]
    if not feasible:
        return {"cost": None, "transshipments": None, "label": "no_feasible"}
    return min(feasible, key=lambda item: item["cost"])


def _cost(result):
    return result["cost"] if result and result["success"] else None


def _gap_pct(base, cost):
    if base is None or cost is None:
        return None
    return (cost - base) / base * 100.0


def _verdict(base, cost):
    if base is None and cost is None:
        return "both_failed"
    if cost is None:
        return "worse"
    if base is None:
        return "better"
    gap = cost - base
    if gap < -1e-9:
        return "better"
    if abs(gap) <= 1e-9:
        return "tie"
    return "worse"


def _summarize(rows, model_key, base_key="m2_cost"):
    summary = {"better": 0, "tie": 0, "worse": 0, "both_failed": 0}
    gaps = []
    for row in rows:
        verdict = _verdict(row[base_key], row[model_key])
        summary[verdict] += 1
        gap = _gap_pct(row[base_key], row[model_key])
        if gap is not None:
            gaps.append(gap)
    summary["avg_gap_pct"] = sum(gaps) / len(gaps) if gaps else None
    return summary


def _fmt(value):
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _write_outputs(csv_path, rows, args):
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    summaries = [
        ("Model 3 vs Model 2", _summarize(rows, "m3_cost")),
        ("Current M5 vs Model 2", _summarize(rows, "m5_current_cost")),
        ("Optimized V5 vs Model 2", _summarize(rows, "m5_v5_cost")),
        ("Stable V6 vs Model 2", _summarize(rows, "m5_v6_cost")),
        ("Stable V6 vs Current M5",
         _summarize(rows, "m5_v6_cost", base_key="m5_current_cost")),
        ("Stable V6 vs Optimized V5",
         _summarize(rows, "m5_v6_cost", base_key="m5_v5_cost")),
    ]

    md_path = csv_path.with_suffix(".md")
    lines = [
        "# Model 5 V6 Stable Comparison",
        "",
        f"- datasets: {len(rows)}",
        f"- iterations: {args.iterations}",
        f"- time limit/model or variant: {args.time}s",
        f"- seed: {args.seed}",
        f"- Model 2/3 strong initial: {args.strong_initial_baselines}",
        f"- legacy safety: {not args.no_legacy_safety}",
        "",
        "## Summary",
        "",
        "| 对比 | 更好 | 持平 | 更差 | 均失败 | 平均 gap% |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, summary in summaries:
        lines.append(
            f"| {label} | {summary['better']} | {summary['tie']} "
            f"| {summary['worse']} | {summary['both_failed']} "
            f"| {_fmt(summary['avg_gap_pct'])}% |"
        )

    lines.extend([
        "",
        "## Details",
        "",
        "| 算例 | Model 2 | Model 3 | Current M5 | V5 | V6 | V6胜出分支 | V6 vs M2 | V6 vs Current | V6 vs V5 |",
        "|---|---:|---:|---:|---:|---:|---|---:|---:|---:|",
    ])
    for row in rows:
        lines.append(
            f"| {row['dataset']} | {_fmt(row['m2_cost'])} "
            f"| {_fmt(row['m3_cost'])} | {_fmt(row['m5_current_cost'])} "
            f"| {_fmt(row['m5_v5_cost'])} | {_fmt(row['m5_v6_cost'])} "
            f"| {row['m5_v6_label']} | {_fmt(row['m5_v6_gap_pct'])}% "
            f"| {_fmt(row['m5_v6_vs_current_gap_pct'])}% "
            f"| {_fmt(row['m5_v6_vs_v5_gap_pct'])}% |"
        )

    improved = [
        row for row in rows
        if row["m5_v6_vs_v5_gap_pct"] is not None
        and row["m5_v6_vs_v5_gap_pct"] < -1e-9
    ]
    if improved:
        lines.extend([
            "",
            "## V6 New Wins",
            "",
            "| 算例 | V5 | V6 | V6胜出分支 | V6 vs V5 |",
            "|---|---:|---:|---|---:|",
        ])
        for row in improved:
            lines.append(
                f"| {row['dataset']} | {_fmt(row['m5_v5_cost'])} "
                f"| {_fmt(row['m5_v6_cost'])} | {row['m5_v6_label']} "
                f"| {_fmt(row['m5_v6_vs_v5_gap_pct'])}% |"
            )

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summaries, md_path


def _print_summary(summaries):
    print("\nSummary")
    print("对比                         更好  持平  更差  均失败  平均gap%")
    for label, summary in summaries:
        print(
            f"{label:<28} {summary['better']:>4} {summary['tie']:>5} "
            f"{summary['worse']:>5} {summary['both_failed']:>6} "
            f"{_fmt(summary['avg_gap_pct']):>8}%"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Run V6 stable Model 5 comparison on large samples.")
    parser.add_argument("--datasets", nargs="+", default=DEFAULT_DATASETS)
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--time", type=float, default=5.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--strong-initial-baselines", action="store_true")
    parser.add_argument("--transshipment-time-budget", type=float, default=0.015)
    parser.add_argument("--transshipment-candidate-mode",
                        default="all_sampled",
                        choices=["nearest_k", "all_sampled",
                                 "all_exhaustive"])
    parser.add_argument("--max-relocation-candidates", type=int, default=None)
    parser.add_argument("--sample-pickups", type=int, default=5)
    parser.add_argument("--sample-locations", type=int, default=3)
    parser.add_argument("--sample-carriers", type=int, default=3)
    parser.add_argument("--position-top-k", type=int, default=3)
    parser.add_argument("--min-net-saving", type=float, default=0.01)
    parser.add_argument("--conservative-min-saving", type=float, default=1.0)
    parser.add_argument("--prune-min-improvement", type=float, default=0.01)
    parser.add_argument("--prune-max-rounds", type=int, default=1)
    parser.add_argument("--final-refine-depth", type=int, default=10)
    parser.add_argument("--route-local-refine-rounds", type=int, default=2)
    parser.add_argument("--route-local-refine-max-moves", type=int, default=20)
    parser.add_argument("--model5-legacy-initial", action="store_true")
    parser.add_argument("--no-legacy-safety", action="store_true")
    parser.add_argument("--tag", default="large_sample_50_100_v6")
    args = parser.parse_args()

    datasets = _resolve_datasets(args.datasets)
    with open(DEFAULT_FLEET, "r", encoding="utf-8") as f:
        fleet_cfg = json.load(f)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    current_variants = get_compact_variants(
        include_legacy_safety=not args.no_legacy_safety)
    v5_extra_variants = (
        list(V1_OPTIMIZED_EXTRA_VARIANTS)
        + list(STAGNATION_EXTRA_VARIANTS)
        + list(REBALANCE_EXTRA_VARIANTS)
        + list(SWAP_EXTRA_VARIANTS)
        + list(ITERATIVE_REBALANCE_EXTRA_VARIANTS)
    )

    rows = []
    for index, data_path in enumerate(datasets, start=1):
        dataset = _dataset_label(data_path)
        print(f"\n[{index}/{len(datasets)}] {dataset}")
        base_args = _variant_args(args)
        model2 = _run_one(2, solve_m2, data_path, fleet_cfg, base_args)
        model3 = _run_one(3, solve_m3, data_path, fleet_cfg, base_args)
        print(f"  Model 2: {model2['cost']}")
        print(f"  Model 3: {model3['cost']}")

        current_runs = []
        for variant in current_variants:
            result = _run_one(
                5, solve_m5, data_path, fleet_cfg,
                _variant_args(args, variant))
            result["label"] = variant["label"]
            current_runs.append(result)
            print(f"  Current {variant['label']}: {result['cost']}")

        v5_runs = []
        for variant in v5_extra_variants:
            result = _run_one(
                5, solve_m5, data_path, fleet_cfg,
                _variant_args(args, variant))
            result["label"] = variant["label"]
            v5_runs.append(result)
            print(f"  V5 {variant['label']}: {result['cost']}")

        v6_runs = []
        for variant in STABLE_V6_EXTRA_VARIANTS:
            result = _run_one(
                5, solve_m5, data_path, fleet_cfg,
                _variant_args(args, variant))
            result["label"] = variant["label"]
            v6_runs.append(result)
            print(f"  V6 {variant['label']}: {result['cost']}")

        best_current = _best_success(current_runs)
        best_v5 = _best_success(current_runs + v5_runs)
        best_v6 = _best_success(current_runs + v5_runs + v6_runs)
        m2_cost = _cost(model2)
        row = {
            "dataset": dataset,
            "m2_cost": m2_cost,
            "m3_cost": _cost(model3),
            "m5_current_cost": best_current["cost"],
            "m5_current_label": best_current["label"],
            "m5_v5_cost": best_v5["cost"],
            "m5_v5_label": best_v5["label"],
            "m5_v6_cost": best_v6["cost"],
            "m5_v6_label": best_v6["label"],
            "m5_current_gap_pct": _gap_pct(m2_cost, best_current["cost"]),
            "m5_v5_gap_pct": _gap_pct(m2_cost, best_v5["cost"]),
            "m5_v6_gap_pct": _gap_pct(m2_cost, best_v6["cost"]),
            "m5_v6_vs_current_gap_pct": _gap_pct(
                best_current["cost"], best_v6["cost"]),
            "m5_v6_vs_v5_gap_pct": _gap_pct(
                best_v5["cost"], best_v6["cost"]),
        }
        for result in v6_runs:
            row[f"{result['label']}_cost"] = result["cost"]
        rows.append(row)

    label = f"{len(datasets)}datasets_iter{args.iterations}_seed{args.seed}"
    if args.strong_initial_baselines:
        label += "_strong_baselines"
    if args.tag:
        label += f"_{args.tag}"
    csv_path = OUT_DIR / f"model5_v6_stable_comparison_{label}.csv"
    summaries, md_path = _write_outputs(csv_path, rows, args)
    _print_summary(summaries)
    print(f"Markdown: {md_path}")
    print(f"CSV: {csv_path}")


if __name__ == "__main__":
    main()
