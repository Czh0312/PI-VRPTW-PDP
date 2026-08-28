# experiments/run_model7_optimized_comparison.py
# -*- coding: utf-8 -*-
"""Compare Model 3/5, current Model 7, and optimized Model 7 variants."""

import argparse
import csv
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
from experiments.runs.run_model7_large_portfolio import (  # noqa: E402
    ITERATIVE_REBALANCE_EXTRA_VARIANTS,
    REBALANCE_EXTRA_VARIANTS,
    STABLE_V6_EXTRA_VARIANTS,
    STAGNATION_EXTRA_VARIANTS,
    SWAP_EXTRA_VARIANTS,
    V1_OPTIMIZED_EXTRA_VARIANTS,
    get_compact_variants,
    _make_args as _make_m7_args,
)
from models.routing.variants.model_3.alns_solver import solve_alns as solve_m3  # noqa: E402
from models.routing.variants.model_5.alns_solver import solve_alns as solve_m5  # noqa: E402
from models.routing.variants.model_7.alns_solver import solve_alns as solve_m7  # noqa: E402


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


def _best_success(results):
    feasible = [
        result for result in results
        if result["success"] and result["cost"] is not None
    ]
    if not feasible:
        return {
            "cost": None,
            "transshipments": None,
            "label": "no_feasible_m7",
        }
    return min(feasible, key=lambda item: item["cost"])


def _cost(result):
    return result["cost"] if result and result["success"] else None


def _verdict(base, cost):
    if base is None and cost is None:
        return "both_failed"
    if cost is None:
        return "failed"
    if base is None:
        return "recovered"
    gap = cost - base
    if gap < -1e-9:
        return "better"
    if abs(gap) <= 1e-9:
        return "tie"
    return "worse"


def _gap_pct(base, cost):
    if base is None or cost is None:
        return None
    return (cost - base) / base * 100.0


def _fmt(value):
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _variant_args(args, variant=None):
    v_args = _make_m7_args(args, variant)
    v_args.baseline_strong_initial = args.strong_initial_baselines
    return v_args


def _summarize(rows, model_key, base_key="m3_cost"):
    better = tie = worse = recovered = failed = both_failed = 0
    gaps = []
    for row in rows:
        verdict = _verdict(row[base_key], row[model_key])
        better += verdict == "better"
        tie += verdict == "tie"
        worse += verdict == "worse"
        recovered += verdict == "recovered"
        failed += verdict == "failed"
        both_failed += verdict == "both_failed"
        gap = _gap_pct(row[base_key], row[model_key])
        if gap is not None:
            gaps.append(gap)
    return {
        "better": better,
        "tie": tie,
        "worse": worse,
        "recovered": recovered,
        "failed": failed,
        "both_failed": both_failed,
        "avg_gap_pct": sum(gaps) / len(gaps) if gaps else None,
    }


def _write_csv(path, rows):
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_md(path, rows, args):
    summaries = [
        ("Model 5 vs Model 3", _summarize(rows, "m5_cost")),
        ("Current M7 vs Model 3", _summarize(rows, "m7_base_cost")),
        ("Optimized V1 M7 vs Model 3", _summarize(rows, "m7_opt_v1_cost")),
        ("Optimized V2 M7 vs Model 3", _summarize(rows, "m7_opt_v2_cost")),
        ("Optimized V3 M7 vs Model 3", _summarize(rows, "m7_opt_v3_cost")),
        ("Optimized V4 M7 vs Model 3", _summarize(rows, "m7_opt_v4_cost")),
        ("Stable V6 M7 vs Model 3", _summarize(rows, "m7_opt_cost")),
        ("Optimized V1 M7 vs Current M7",
         _summarize(rows, "m7_opt_v1_cost", base_key="m7_base_cost")),
        ("Optimized V2 M7 vs V1 M7",
         _summarize(rows, "m7_opt_v2_cost", base_key="m7_opt_v1_cost")),
        ("Optimized V3 M7 vs V2 M7",
         _summarize(rows, "m7_opt_v3_cost", base_key="m7_opt_v2_cost")),
        ("Optimized V4 M7 vs V3 M7",
         _summarize(rows, "m7_opt_v4_cost", base_key="m7_opt_v3_cost")),
        ("Stable V6 M7 vs V4 M7",
         _summarize(rows, "m7_opt_cost", base_key="m7_opt_v4_cost")),
        ("Stable V6 M7 vs Current M7",
         _summarize(rows, "m7_opt_cost", base_key="m7_base_cost")),
    ]

    lines = [
        "# Model 7 Optimized Comparison",
        "",
        f"- datasets: {len(rows)}",
        f"- iterations: {args.iterations}",
        f"- time limit/model or variant: {args.time}s",
        f"- seed: {args.seed}",
        f"- Model 3/5 strong initial: {args.strong_initial_baselines}",
        f"- legacy safety: {not args.no_legacy_safety}",
        "- Stable V6 extra: full",
        "",
        "## Summary",
        "",
        "| 对比 | 更好 | 持平 | 更差 | 恢复可行 | 本模型失败 | 均失败 | 平均 gap% |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, summary in summaries:
        lines.append(
            f"| {label} | {summary['better']} | {summary['tie']} "
            f"| {summary['worse']} | {summary['recovered']} "
            f"| {summary['failed']} | {summary['both_failed']} "
            f"| {_fmt(summary['avg_gap_pct'])}% |"
        )

    lines.extend([
        "",
        "## Details",
        "",
        "| 算例 | M3 | M5 | Current M7 | Opt V1 | Opt V2 | Opt V3 | Opt V4 | Stable V6 | V6策略 | V6 vs M3 | V6 vs V4 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
    ])
    for row in rows:
        lines.append(
            f"| {row['dataset']} | {_fmt(row['m3_cost'])} "
            f"| {_fmt(row['m5_cost'])} | {_fmt(row['m7_base_cost'])} "
            f"| {_fmt(row['m7_opt_v1_cost'])} "
            f"| {_fmt(row['m7_opt_v2_cost'])} "
            f"| {_fmt(row['m7_opt_v3_cost'])} "
            f"| {_fmt(row['m7_opt_v4_cost'])} "
            f"| {_fmt(row['m7_opt_cost'])} "
            f"| {row['m7_opt_label']} "
            f"| {_fmt(row['m7_opt_gap_pct'])}% "
            f"| {_fmt(row['m7_opt_vs_v4_gap_pct'])}% |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Compare Model 3/5, current M7, and optimized M7.")
    parser.add_argument("--datasets", nargs="+", required=True)
    parser.add_argument("--iterations", type=int, default=80)
    parser.add_argument("--time", type=float, default=10.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--strong-initial-baselines", action="store_true")
    parser.add_argument("--transshipment-time-budget", type=float, default=0.03)
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
    parser.add_argument("--route-local-refine-max-moves", type=int,
                        default=20)
    parser.add_argument("--model7-legacy-initial", action="store_true")
    parser.add_argument("--no-legacy-safety", action="store_true")
    parser.add_argument("--tag", default=None)
    args = parser.parse_args()

    datasets = _resolve_datasets(args.datasets)
    with open(DEFAULT_FLEET, "r", encoding="utf-8") as f:
        fleet_cfg = json.load(f)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    base_variants = get_compact_variants(
        include_legacy_safety=not args.no_legacy_safety)
    v1_variants = list(V1_OPTIMIZED_EXTRA_VARIANTS)
    v2_variants = list(STAGNATION_EXTRA_VARIANTS)
    v3_variants = list(REBALANCE_EXTRA_VARIANTS)
    v4_variants = list(SWAP_EXTRA_VARIANTS)
    stable_v6_variants = (
        list(ITERATIVE_REBALANCE_EXTRA_VARIANTS)
        + list(STABLE_V6_EXTRA_VARIANTS)
    )
    rows = []

    for index, data_path in enumerate(datasets, start=1):
        dataset = _dataset_label(data_path)
        print(f"\n[{index}/{len(datasets)}] {dataset}")

        base_args = _variant_args(args)
        model3 = _run_one(3, solve_m3, data_path, fleet_cfg, base_args)
        model5 = _run_one(5, solve_m5, data_path, fleet_cfg, base_args)
        print(f"  Model 3: {model3['cost']}")
        print(f"  Model 5: {model5['cost']}")

        base_runs = []
        for variant in base_variants:
            v_args = _variant_args(args, variant)
            result = _run_one(7, solve_m7, data_path, fleet_cfg, v_args)
            result["label"] = variant["label"]
            base_runs.append(result)
            print(f"  M7 base {variant['label']}: {result['cost']}")

        v1_runs = []
        for variant in v1_variants:
            v_args = _variant_args(args, variant)
            result = _run_one(7, solve_m7, data_path, fleet_cfg, v_args)
            result["label"] = variant["label"]
            v1_runs.append(result)
            print(f"  M7 opt-v1 {variant['label']}: {result['cost']}")

        v2_runs = []
        for variant in v2_variants:
            v_args = _variant_args(args, variant)
            result = _run_one(7, solve_m7, data_path, fleet_cfg, v_args)
            result["label"] = variant["label"]
            v2_runs.append(result)
            print(f"  M7 opt-v2 {variant['label']}: {result['cost']}")

        v3_runs = []
        for variant in v3_variants:
            v_args = _variant_args(args, variant)
            result = _run_one(7, solve_m7, data_path, fleet_cfg, v_args)
            result["label"] = variant["label"]
            v3_runs.append(result)
            print(f"  M7 opt-v3 {variant['label']}: {result['cost']}")

        v4_runs = []
        for variant in v4_variants:
            v_args = _variant_args(args, variant)
            result = _run_one(7, solve_m7, data_path, fleet_cfg, v_args)
            result["label"] = variant["label"]
            v4_runs.append(result)
            print(f"  M7 opt-v4 {variant['label']}: {result['cost']}")

        stable_v6_runs = []
        for variant in stable_v6_variants:
            v_args = _variant_args(args, variant)
            result = _run_one(7, solve_m7, data_path, fleet_cfg, v_args)
            result["label"] = variant["label"]
            stable_v6_runs.append(result)
            print(f"  M7 stable-v6 {variant['label']}: {result['cost']}")

        best_base = _best_success(base_runs)
        best_v1 = _best_success(base_runs + v1_runs)
        best_v2 = _best_success(base_runs + v1_runs + v2_runs)
        best_v3 = _best_success(base_runs + v1_runs + v2_runs + v3_runs)
        best_v4 = _best_success(
            base_runs + v1_runs + v2_runs + v3_runs + v4_runs)
        best_opt = _best_success(
            base_runs + v1_runs + v2_runs + v3_runs + v4_runs
            + stable_v6_runs)
        m3_cost = _cost(model3)
        row = {
            "dataset": dataset,
            "m3_cost": m3_cost,
            "m5_cost": _cost(model5),
            "m7_base_cost": best_base["cost"],
            "m7_base_label": best_base["label"],
            "m7_opt_v1_cost": best_v1["cost"],
            "m7_opt_v1_label": best_v1["label"],
            "m7_opt_v2_cost": best_v2["cost"],
            "m7_opt_v2_label": best_v2["label"],
            "m7_opt_v3_cost": best_v3["cost"],
            "m7_opt_v3_label": best_v3["label"],
            "m7_opt_v4_cost": best_v4["cost"],
            "m7_opt_v4_label": best_v4["label"],
            "m7_opt_cost": best_opt["cost"],
            "m7_opt_label": best_opt["label"],
            "m7_opt_gap_pct": _gap_pct(m3_cost, best_opt["cost"]),
            "m7_opt_vs_base_gap_pct": _gap_pct(
                best_base["cost"], best_opt["cost"]),
            "m7_opt_vs_v1_gap_pct": _gap_pct(
                best_v1["cost"], best_opt["cost"]),
            "m7_opt_vs_v2_gap_pct": _gap_pct(
                best_v2["cost"], best_opt["cost"]),
            "m7_opt_v3_vs_v2_gap_pct": _gap_pct(
                best_v2["cost"], best_v3["cost"]),
            "m7_opt_v4_vs_v3_gap_pct": _gap_pct(
                best_v3["cost"], best_v4["cost"]),
            "m7_opt_vs_v4_gap_pct": _gap_pct(
                best_v4["cost"], best_opt["cost"]),
        }
        all_runs = (
            base_runs + v1_runs + v2_runs + v3_runs + v4_runs
            + stable_v6_runs
        )
        for run in all_runs:
            row[f"{run['label']}_cost"] = run["cost"]
        rows.append(row)

    label = f"{len(datasets)}datasets_iter{args.iterations}_seed{args.seed}"
    if args.strong_initial_baselines:
        label += "_strong_baselines"
    if args.tag:
        label += f"_{args.tag}"
    csv_path = OUT_DIR / f"model7_optimized_comparison_{label}.csv"
    _write_csv(csv_path, rows)
    md_path = csv_path.with_suffix(".md")
    _write_md(md_path, rows, args)

    print("\nOptimized comparison completed")
    for key, title in [
            ("m5_cost", "Model 5 vs Model 3"),
            ("m7_base_cost", "Current M7 vs Model 3"),
            ("m7_opt_v1_cost", "Optimized V1 M7 vs Model 3"),
            ("m7_opt_v2_cost", "Optimized V2 M7 vs Model 3"),
            ("m7_opt_v3_cost", "Optimized V3 M7 vs Model 3"),
            ("m7_opt_v4_cost", "Optimized V4 M7 vs Model 3"),
            ("m7_opt_cost", "Stable V6 M7 vs Model 3")]:
        summary = _summarize(rows, key)
        print(
            f"{title}: better={summary['better']}, tie={summary['tie']}, "
            f"worse={summary['worse']}, recovered={summary['recovered']}, "
            f"failed={summary['failed']}, both_failed={summary['both_failed']}, "
            f"avg_gap_pct={_fmt(summary['avg_gap_pct'])}%"
        )
    v1_vs_base = _summarize(rows, "m7_opt_v1_cost", base_key="m7_base_cost")
    print(
        "Optimized V1 M7 vs Current M7: "
        f"better={v1_vs_base['better']}, tie={v1_vs_base['tie']}, "
        f"worse={v1_vs_base['worse']}, "
        f"avg_gap_pct={_fmt(v1_vs_base['avg_gap_pct'])}%"
    )
    v2_vs_v1 = _summarize(rows, "m7_opt_v2_cost", base_key="m7_opt_v1_cost")
    print(
        "Optimized V2 M7 vs V1 M7: "
        f"better={v2_vs_v1['better']}, tie={v2_vs_v1['tie']}, "
        f"worse={v2_vs_v1['worse']}, "
        f"avg_gap_pct={_fmt(v2_vs_v1['avg_gap_pct'])}%"
    )
    v3_vs_v2 = _summarize(
        rows, "m7_opt_v3_cost", base_key="m7_opt_v2_cost")
    print(
        "Optimized V3 M7 vs V2 M7: "
        f"better={v3_vs_v2['better']}, tie={v3_vs_v2['tie']}, "
        f"worse={v3_vs_v2['worse']}, "
        f"avg_gap_pct={_fmt(v3_vs_v2['avg_gap_pct'])}%"
    )
    v4_vs_v3 = _summarize(rows, "m7_opt_v4_cost", base_key="m7_opt_v3_cost")
    print(
        "Optimized V4 M7 vs V3 M7: "
        f"better={v4_vs_v3['better']}, tie={v4_vs_v3['tie']}, "
        f"worse={v4_vs_v3['worse']}, "
        f"avg_gap_pct={_fmt(v4_vs_v3['avg_gap_pct'])}%"
    )
    v5_vs_v4 = _summarize(rows, "m7_opt_cost", base_key="m7_opt_v4_cost")
    print(
        "Stable V6 M7 vs V4 M7: "
        f"better={v5_vs_v4['better']}, tie={v5_vs_v4['tie']}, "
        f"worse={v5_vs_v4['worse']}, "
        f"avg_gap_pct={_fmt(v5_vs_v4['avg_gap_pct'])}%"
    )
    opt_vs_base = _summarize(rows, "m7_opt_cost", base_key="m7_base_cost")
    print(
        "Stable V6 M7 vs Current M7: "
        f"better={opt_vs_base['better']}, tie={opt_vs_base['tie']}, "
        f"worse={opt_vs_base['worse']}, "
        f"avg_gap_pct={_fmt(opt_vs_base['avg_gap_pct'])}%"
    )
    print(f"Markdown: {md_path}")
    print(f"CSV: {csv_path}")


if __name__ == "__main__":
    main()
