# experiments/run_model3567_all_nonbeijing.py
# -*- coding: utf-8 -*-
"""Run Model 3/5/6/7 comparison on all non-Beijing benchmark instances."""

import argparse
import csv
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.run_model567_comparison import (  # noqa: E402
    DEFAULT_FLEET,
    OUT_DIR,
    _run_one,
)
from experiments.run_model7_large_portfolio import (  # noqa: E402
    _make_args as _make_m7_args,
    get_compact_variants,
    get_optimized_variants,
)
from models.model_3.alns_solver import solve_alns as solve_m3  # noqa: E402
from models.model_5.alns_solver import solve_alns as solve_m5  # noqa: E402
from models.model_6.alns_solver import solve_alns as solve_m6  # noqa: E402
from models.model_7.alns_solver import solve_alns as solve_m7  # noqa: E402


def _resolve_nonbeijing_datasets():
    paths = []
    for path in sorted((ROOT / "data").glob("*_customers/*.csv")):
        if "beijing" not in str(path).lower():
            paths.append(path)
    return paths


def _dataset_label(path):
    return f"{path.parent.name}_{path.stem}"


def _instance_class(stem):
    if stem.startswith("RC"):
        return "RC"
    if stem.startswith("R"):
        return "R"
    if stem.startswith("C"):
        return "C"
    return "OTHER"


def _make_args(args, variant=None):
    variant = variant or {}
    return SimpleNamespace(
        iterations=args.iterations,
        time=args.time,
        seed=args.seed,
        transshipment_candidate_mode=variant.get(
            "candidate_mode", args.transshipment_candidate_mode),
        max_relocation_candidates=variant.get(
            "max_relocation_candidates", args.max_relocation_candidates),
        transshipment_time_budget=args.transshipment_time_budget,
        sample_pickups=args.sample_pickups,
        sample_locations=args.sample_locations,
        sample_carriers=args.sample_carriers,
        position_top_k=args.position_top_k,
        min_net_saving=args.min_net_saving,
        max_total=variant.get("max_total", args.max_total),
        max_per_satellite=variant.get(
            "max_per_satellite", args.max_per_satellite),
        stage1_time_ratio=args.stage1_time_ratio,
        model7_depth=variant.get("depth", args.model7_depth),
        model7_schedule="fixed",
        model7_selection="last",
        model7_strategy=variant.get("strategy", args.model7_strategy),
        model7_pool_size=4,
        model7_alpha=0.8,
        model7_beam_width=3,
        model7_conservative_min_saving=args.conservative_min_saving,
        model7_risk_aware=variant.get("risk_aware", False),
        model7_slack_weight=variant.get("slack_weight", 0.0),
        model7_capacity_weight=variant.get("capacity_weight", 0.0),
        model7_min_transfer_slack=variant.get("min_transfer_slack", None),
        model7_min_return_slack=variant.get("min_return_slack", None),
        model7_min_capacity_slack=variant.get("min_capacity_slack", None),
        model7_quality_gap=None,
        model7_prune=variant.get("prune", False),
        model7_prune_min_improvement=args.prune_min_improvement,
        model7_prune_max_rounds=args.prune_max_rounds,
        model7_final_refine=variant.get("final_refine", False),
        model7_final_refine_depth=args.final_refine_depth,
        model7_baseline_guard=variant.get("baseline_guard", False),
        model7_baseline_iterations=None,
        model7_baseline_time=None,
        model7_route_local_refine=variant.get("route_local_refine", False),
        model7_route_local_refine_rounds=args.route_local_refine_rounds,
        model7_route_local_refine_max_moves=args.route_local_refine_max_moves,
        model7_strong_initial=variant.get(
            "strong_initial", not args.model7_legacy_initial),
        baseline_strong_initial=args.strong_initial_baselines,
    )


def _best_success(results):
    feasible = [
        result for result in results
        if result["success"] and result["cost"] is not None
    ]
    if not feasible:
        return None
    return min(feasible, key=lambda item: item["cost"])


def _model_verdict(model3_cost, cost):
    if cost is None and model3_cost is None:
        return "both_failed"
    if cost is None:
        return "failed"
    if model3_cost is None:
        return "recovered"
    gap = cost - model3_cost
    if gap < -1e-9:
        return "better"
    if abs(gap) <= 1e-9:
        return "tie"
    return "worse"


def _cost(result):
    return result["cost"] if result and result["success"] else None


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


def _fmt(value):
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _write_md(path, rows, group_rows, overall_rows, args):
    lines = [
        "# Model 3/5/6/7 全部非北京算例对比",
        "",
        f"- 算例数: {len(rows)}",
        f"- iterations: {args.iterations}",
        f"- time limit/model or variant: {args.time}s",
        f"- seed: {args.seed}",
        f"- Model 3/5/6 strong initial: {args.strong_initial_baselines}",
        f"- Model 7 portfolio: {args.m7_portfolio}",
        "- Model 7: portfolio 取最优",
        "",
        "## 总体汇总",
        "",
        "| 模型 | 可行数 | 失败数 | 相对 Model 3 更好 | 持平 | 更差 | M3无解但本模型可行 | 平均 gap% |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in overall_rows:
        lines.append(
            f"| {row['model']} | {row['feasible']} | {row['failed']} "
            f"| {row['better']} | {row['tie']} | {row['worse']} "
            f"| {row['recovered']} | {_fmt(row['avg_gap_pct'])}% |"
        )

    lines.extend([
        "",
        "## 分规模汇总",
        "",
        "| 规模 | 模型 | 可行数 | 失败数 | 更好 | 持平 | 更差 | 恢复可行 | 平均 gap% |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for row in group_rows:
        lines.append(
            f"| {row['group']} | {row['model']} | {row['feasible']} "
            f"| {row['failed']} | {row['better']} | {row['tie']} "
            f"| {row['worse']} | {row['recovered']} "
            f"| {_fmt(row['avg_gap_pct'])}% |"
        )

    lines.extend([
        "",
        "## 明细",
        "",
        "| 算例 | M3 | M5 | M6 | Best M7 | M7策略 | 最优模型 | M7 vs M3 |",
        "| --- | ---: | ---: | ---: | ---: | --- | --- | ---: |",
    ])
    for row in rows:
        lines.append(
            f"| {row['dataset']} | {_fmt(row['m3_cost'])} "
            f"| {_fmt(row['m5_cost'])} | {_fmt(row['m6_cost'])} "
            f"| {_fmt(row['m7_cost'])} | {row['m7_label']} "
            f"| {row['best_model']} | {_fmt(row['m7_gap_pct'])}% |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _summarize(rows, key=None):
    models = ["M3", "M5", "M6", "M7"]
    summary = []
    groups = sorted({row[key] for row in rows}) if key else [None]
    for group in groups:
        subset = [row for row in rows if key is None or row[key] == group]
        for model in models:
            col = model.lower()
            costs = [row[f"{col}_cost"] for row in subset]
            model3_costs = [row["m3_cost"] for row in subset]
            feasible = sum(cost is not None for cost in costs)
            failed = len(subset) - feasible
            better = tie = worse = recovered = 0
            gaps = []
            for model3_cost, cost in zip(model3_costs, costs):
                verdict = _model_verdict(model3_cost, cost)
                better += verdict == "better"
                tie += verdict == "tie"
                worse += verdict == "worse"
                recovered += verdict == "recovered"
                if model3_cost is not None and cost is not None:
                    gaps.append((cost - model3_cost) / model3_cost * 100.0)
            avg_gap = sum(gaps) / len(gaps) if gaps else None
            item = {
                "model": model,
                "feasible": feasible,
                "failed": failed,
                "better": better,
                "tie": tie,
                "worse": worse,
                "recovered": recovered,
                "avg_gap_pct": avg_gap,
            }
            if key:
                item["group"] = group
            summary.append(item)
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Compare Model 3/5/6/7 on all non-Beijing instances.")
    parser.add_argument("--iterations", type=int, default=80)
    parser.add_argument("--time", type=float, default=10.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--strong-initial-baselines", action="store_true",
                        help="Use the strong initial solution for Models 3/5/6.")
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
    parser.add_argument("--max-total", type=int, default=1)
    parser.add_argument("--max-per-satellite", type=int, default=1)
    parser.add_argument("--stage1-time-ratio", type=float, default=0.95)
    parser.add_argument("--model7-depth", type=int, default=1)
    parser.add_argument("--model7-strategy", default="greedy",
                        choices=["greedy", "range_random", "conservative",
                                 "beam"])
    parser.add_argument("--conservative-min-saving", type=float, default=1.0)
    parser.add_argument("--prune-min-improvement", type=float, default=0.01)
    parser.add_argument("--prune-max-rounds", type=int, default=1)
    parser.add_argument("--final-refine-depth", type=int, default=10)
    parser.add_argument("--route-local-refine-rounds", type=int, default=2)
    parser.add_argument("--route-local-refine-max-moves", type=int,
                        default=20)
    parser.add_argument("--model7-legacy-initial", action="store_true")
    parser.add_argument("--no-legacy-safety", action="store_true",
                        help="Use only the strong-initial compact variants.")
    parser.add_argument("--m7-portfolio",
                        choices=["optimized_v6", "hybrid_safety"],
                        default="optimized_v6",
                        help="Model 7 portfolio used in the comparison.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Optional first-N limit for smoke testing.")
    args = parser.parse_args()

    datasets = _resolve_nonbeijing_datasets()
    if args.limit:
        datasets = datasets[:args.limit]

    with open(DEFAULT_FLEET, "r", encoding="utf-8") as f:
        fleet_cfg = json.load(f)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = []
    base_args = _make_args(args)
    solvers = [("M3", 3, solve_m3), ("M5", 5, solve_m5),
               ("M6", 6, solve_m6)]

    for index, data_path in enumerate(datasets, start=1):
        label = _dataset_label(data_path)
        print(f"\n[{index}/{len(datasets)}] {label}")
        result_by_model = {}
        for model_label, model_id, solver in solvers:
            result = _run_one(model_id, solver, data_path, fleet_cfg,
                              base_args)
            result_by_model[model_label] = result
            print(f"  {model_label}: {result['cost']}")

        m7_runs = []
        if args.m7_portfolio == "optimized_v6":
            variants = get_optimized_variants(
                include_legacy_safety=not args.no_legacy_safety)
        else:
            variants = get_compact_variants(
                include_legacy_safety=not args.no_legacy_safety)
        for variant in variants:
            v_args = _make_m7_args(args, variant)
            result = _run_one(7, solve_m7, data_path, fleet_cfg, v_args)
            result["label"] = variant["label"]
            m7_runs.append(result)
            print(f"  M7 {variant['label']}: {result['cost']}")

        best_m7 = _best_success(m7_runs)
        if best_m7 is None:
            best_m7 = {
                "cost": None,
                "success": False,
                "transshipments": None,
                "label": "no_feasible_m7",
            }

        comparable = [
            ("M3", _cost(result_by_model["M3"])),
            ("M5", _cost(result_by_model["M5"])),
            ("M6", _cost(result_by_model["M6"])),
            ("M7", best_m7["cost"]),
        ]
        feasible = [(model, cost) for model, cost in comparable
                    if cost is not None]
        if feasible:
            best_model, best_cost = min(feasible, key=lambda item: item[1])
        else:
            best_model, best_cost = "none", None

        m3_cost = _cost(result_by_model["M3"])
        m7_gap = None
        m7_gap_pct = None
        if m3_cost is not None and best_m7["cost"] is not None:
            m7_gap = best_m7["cost"] - m3_cost
            m7_gap_pct = m7_gap / m3_cost * 100.0

        row = {
            "group": data_path.parent.name,
            "class": _instance_class(data_path.stem),
            "dataset": label,
            "m3_cost": m3_cost,
            "m5_cost": _cost(result_by_model["M5"]),
            "m6_cost": _cost(result_by_model["M6"]),
            "m7_cost": best_m7["cost"],
            "m7_label": best_m7["label"],
            "m7_transshipments": best_m7["transshipments"],
            "m7_gap": m7_gap,
            "m7_gap_pct": m7_gap_pct,
            "best_model": best_model,
            "best_cost": best_cost,
        }
        for model_label, result in result_by_model.items():
            row[f"{model_label.lower()}_transshipments"] = (
                result["transshipments"])
            row[f"{model_label.lower()}_elapsed"] = result["elapsed"]
        for run in m7_runs:
            row[f"{run['label']}_cost"] = run["cost"]
        rows.append(row)

    portfolio_label = args.m7_portfolio
    if args.no_legacy_safety:
        portfolio_label += "_no_legacy_safety"
    suffix = (
        f"{len(datasets)}datasets_{portfolio_label}_"
        f"iter{args.iterations}_seed{args.seed}"
    )
    if args.strong_initial_baselines:
        suffix += "_strong_baselines"
    if args.model7_legacy_initial:
        suffix += "_legacy_m7_initial"
    csv_path = OUT_DIR / f"model3567_all_nonbeijing_{suffix}.csv"
    md_path = csv_path.with_suffix(".md")
    _write_csv(csv_path, rows)
    group_rows = _summarize(rows, key="group")
    overall_rows = _summarize(rows)
    _write_md(md_path, rows, group_rows, overall_rows, args)

    print("\nAll non-Beijing comparison completed")
    for row in overall_rows:
        print(
            f"{row['model']}: feasible={row['feasible']}, "
            f"failed={row['failed']}, better={row['better']}, "
            f"tie={row['tie']}, worse={row['worse']}, "
            f"recovered={row['recovered']}, "
            f"avg_gap_pct={_fmt(row['avg_gap_pct'])}%"
        )
    print(f"Markdown: {md_path}")
    print(f"CSV: {csv_path}")


if __name__ == "__main__":
    main()
