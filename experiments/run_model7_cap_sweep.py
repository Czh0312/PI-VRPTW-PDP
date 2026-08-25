# experiments/run_model7_cap_sweep.py
# -*- coding: utf-8 -*-
"""Sweep Model 7 transshipment caps against Model 3."""
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
from models.model_3.alns_solver import solve_alns as solve_m3  # noqa: E402
from models.model_7.alns_solver import solve_alns as solve_m7  # noqa: E402


BASE_VARIANTS = [
    {
        "label": "greedy_H1",
        "strategy": "greedy",
        "depth": 1,
        "pool_size": 4,
        "beam_width": 3,
        "prune": False,
        "final_refine": False,
        "baseline_guard": False,
        "route_local_refine": False,
    },
    {
        "label": "beam_H2",
        "strategy": "beam",
        "depth": 2,
        "pool_size": 4,
        "beam_width": 3,
        "prune": False,
        "final_refine": False,
        "baseline_guard": False,
        "route_local_refine": False,
    },
    {
        "label": "cons_prune_refine",
        "strategy": "conservative",
        "depth": 1,
        "pool_size": 4,
        "beam_width": 3,
        "prune": True,
        "final_refine": True,
        "baseline_guard": False,
        "route_local_refine": False,
    },
    {
        "label": "guard_greedy",
        "strategy": "greedy",
        "depth": 1,
        "pool_size": 4,
        "beam_width": 3,
        "prune": False,
        "final_refine": False,
        "baseline_guard": True,
        "route_local_refine": False,
    },
    {
        "label": "guard_route_refine",
        "strategy": "greedy",
        "depth": 1,
        "pool_size": 4,
        "beam_width": 3,
        "prune": False,
        "final_refine": False,
        "baseline_guard": True,
        "route_local_refine": True,
    },
]


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


def _dataset_label(path):
    return f"{path.parent.name}_{path.stem}"


def _parse_caps(items):
    caps = []
    for item in items:
        if ":" in item:
            total, per_sat = item.split(":", 1)
            caps.append((int(total), int(per_sat)))
        else:
            total = int(item)
            caps.append((total, max(1, min(total, 2))))
    return caps


def _make_args(args, variant=None, seed=None, max_total=None,
               max_per_satellite=None):
    variant = variant or {}
    return SimpleNamespace(
        iterations=args.iterations,
        time=args.time,
        seed=args.seed if seed is None else seed,
        transshipment_candidate_mode=args.transshipment_candidate_mode,
        max_relocation_candidates=args.max_relocation_candidates,
        transshipment_time_budget=args.transshipment_time_budget,
        sample_pickups=args.sample_pickups,
        sample_locations=args.sample_locations,
        sample_carriers=args.sample_carriers,
        position_top_k=args.position_top_k,
        min_net_saving=args.min_net_saving,
        max_total=max_total,
        max_per_satellite=max_per_satellite,
        stage1_time_ratio=0.95,
        model7_depth=variant.get("depth", 1),
        model7_schedule="fixed",
        model7_selection="last",
        model7_strategy=variant.get("strategy", "greedy"),
        model7_pool_size=variant.get("pool_size", 4),
        model7_alpha=0.8,
        model7_beam_width=variant.get("beam_width", 3),
        model7_conservative_min_saving=args.conservative_min_saving,
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
        model7_strong_initial=not args.model7_legacy_initial,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Sweep Model 7 max transshipment caps.")
    parser.add_argument("--datasets", nargs="*", default=None)
    parser.add_argument("--caps", nargs="+", default=["1:1", "2:1", "3:2", "5:3"])
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument("--time", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--model3-seed", type=int, default=None)
    parser.add_argument("--transshipment-time-budget", type=float, default=0.05)
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
    parser.add_argument("--model7-legacy-initial", action="store_true",
                        help="Use the legacy Model 5 initial solution in Model 7.")
    args = parser.parse_args()

    datasets = _resolve_datasets(args.datasets)
    caps = _parse_caps(args.caps)
    with open(DEFAULT_FLEET, "r", encoding="utf-8") as f:
        fleet_cfg = json.load(f)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = []
    for index, data_path in enumerate(datasets, start=1):
        dataset = _dataset_label(data_path)
        print(f"\n[{index}/{len(datasets)}] {dataset}")
        model3_seed = args.seed if args.model3_seed is None else args.model3_seed
        model3_args = _make_args(
            args, seed=model3_seed, max_total=0, max_per_satellite=0)
        model3 = _run_one(3, solve_m3, data_path, fleet_cfg, model3_args)
        print(f"  Model 3: {model3['cost']}")

        runs = []
        for max_total, max_per_sat in caps:
            for variant in BASE_VARIANTS:
                label = f"{variant['label']}_T{max_total}_S{max_per_sat}"
                v_args = _make_args(
                    args, variant,
                    max_total=max_total,
                    max_per_satellite=max_per_sat,
                )
                result = _run_one(7, solve_m7, data_path, fleet_cfg, v_args)
                result["label"] = label
                result["base_variant"] = variant["label"]
                result["max_total"] = max_total
                result["max_per_satellite"] = max_per_sat
                runs.append(result)
                print(f"  {label}: {result['cost']}")

        feasible = [
            run for run in runs
            if run["success"] and run["cost"] is not None
        ]
        best = min(feasible, key=lambda run: run["cost"])
        gap = best["cost"] - model3["cost"]
        verdict = "better" if gap < -1e-9 else (
            "tie" if abs(gap) < 1e-9 else "worse")

        row = {
            "dataset": dataset,
            "model3_cost": model3["cost"],
            "model3_trans": model3["transshipments"],
            "best_m7_cost": best["cost"],
            "best_m7_trans": best["transshipments"],
            "best_m7_label": best["label"],
            "best_base_variant": best["base_variant"],
            "best_max_total": best["max_total"],
            "best_max_per_satellite": best["max_per_satellite"],
            "gap": gap,
            "gap_pct": gap / model3["cost"] * 100.0,
            "verdict": verdict,
        }
        for run in runs:
            row[f"{run['label']}_cost"] = run["cost"]
            row[f"{run['label']}_trans"] = run["transshipments"]
        rows.append(row)

    label = "all10" if args.datasets is None else f"{len(datasets)}datasets"
    cap_label = "-".join(f"T{t}S{s}" for t, s in caps)
    csv_path = OUT_DIR / (
        f"model7_cap_sweep_{label}_iter{args.iterations}_"
        f"seed{args.seed}_{cap_label}.csv"
    )
    _write_csv(csv_path, rows)
    md_path = csv_path.with_suffix(".md")
    _write_md(md_path, rows)

    better = sum(row["verdict"] == "better" for row in rows)
    tie = sum(row["verdict"] == "tie" for row in rows)
    worse = sum(row["verdict"] == "worse" for row in rows)
    avg_gap_pct = sum(row["gap_pct"] for row in rows) / max(len(rows), 1)
    print("\nCap sweep summary")
    print(f"better={better}, tie={tie}, worse={worse}, "
          f"avg_gap_pct={avg_gap_pct:.2f}%")
    print(f"Markdown: {md_path}")
    print(f"CSV: {csv_path}")


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


def _write_md(path, rows):
    verdict_label = {"better": "更好", "tie": "持平", "worse": "更差"}
    lines = [
        "# Model 7 Cap Sweep",
        "",
        "gap = Best M7 - Model 3；负数表示 Model 7 更好。",
        "",
        "| 算例 | Model 3 | Best M7 | 策略 | T | S | gap | gap% | 结论 |",
        "| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['dataset']} "
            f"| {row['model3_cost']:.2f} / {row['model3_trans']} "
            f"| {row['best_m7_cost']:.2f} / {row['best_m7_trans']} "
            f"| {row['best_base_variant']} "
            f"| {row['best_max_total']} "
            f"| {row['best_max_per_satellite']} "
            f"| {row['gap']:.2f} "
            f"| {row['gap_pct']:.2f}% "
            f"| {verdict_label[row['verdict']]} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
