# experiments/run_model5_before_after_medium_large.py
# -*- coding: utf-8 -*-
"""Compare Models 2/3/4 with Model 5 before/after optimization.

The "after" Model 5 variant keeps the same transshipment probe as the base
variant and only enables final route-local refinement plus inter-route
rebalance. This makes the before/after comparison narrow and reproducible.
"""
import argparse
import csv
import json
import re
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
from models.model_2.alns_solver import solve_alns as solve_m2  # noqa: E402
from models.model_3.alns_solver import solve_alns as solve_m3  # noqa: E402
from models.model_4.alns_solver import solve_alns as solve_m4  # noqa: E402
from models.model_5.alns_solver import solve_alns as solve_m5  # noqa: E402


DEFAULT_DATASETS = [
    "data/50_customers/C101.csv",
    "data/50_customers/C103.csv",
    "data/50_customers/R101.csv",
    "data/50_customers/R103.csv",
    "data/50_customers/RC101.csv",
    "data/50_customers/RC103.csv",
    "data/100_customers/C101.csv",
    "data/100_customers/C103.csv",
    "data/100_customers/R101.csv",
    "data/100_customers/R103.csv",
    "data/100_customers/RC101.csv",
    "data/100_customers/RC103.csv",
]


def _safe_label(text):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")


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


def _args(base_args, *, m5_after=False):
    return SimpleNamespace(
        iterations=base_args.iterations,
        time=base_args.time,
        seed=base_args.seed,
        baseline_strong_initial=not base_args.no_strong_baselines,
        transshipment_time_budget=base_args.transshipment_time_budget,
        transshipment_candidate_mode=base_args.transshipment_candidate_mode,
        max_relocation_candidates=base_args.max_relocation_candidates,
        sample_pickups=base_args.sample_pickups,
        sample_locations=base_args.sample_locations,
        sample_carriers=base_args.sample_carriers,
        position_top_k=base_args.position_top_k,
        min_net_saving=base_args.min_net_saving,
        max_total=base_args.max_total,
        max_per_satellite=base_args.max_per_satellite,
        stage1_time_ratio=base_args.stage1_time_ratio,
        model5_depth=1,
        model5_schedule="fixed",
        model5_selection="last",
        model5_strategy="greedy",
        model5_pool_size=4,
        model5_alpha=0.8,
        model5_beam_width=3,
        model5_conservative_min_saving=1.0,
        model5_risk_aware=False,
        model5_slack_weight=0.0,
        model5_capacity_weight=0.0,
        model5_min_transfer_slack=None,
        model5_min_return_slack=None,
        model5_min_capacity_slack=None,
        model5_quality_gap=None,
        model5_stagnation_boost=False,
        model5_stagnation_window=30,
        model5_stagnation_depth=None,
        model5_stagnation_time_factor=2.0,
        model5_stagnation_candidate_mode=None,
        model5_stagnation_max_relocation_candidates=None,
        model5_stagnation_position_top_k=None,
        model5_prune=False,
        model5_prune_min_improvement=0.01,
        model5_prune_max_rounds=1,
        model5_final_refine=False,
        model5_final_refine_depth=10,
        model5_baseline_guard=False,
        model5_baseline_iterations=None,
        model5_baseline_time=None,
        model5_route_local_refine=m5_after,
        model5_route_local_refine_rounds=base_args.route_local_refine_rounds,
        model5_route_local_refine_max_moves=base_args.route_local_refine_max_moves,
        model5_candidate_route_local_refine=False,
        model5_candidate_route_local_refine_rounds=1,
        model5_candidate_route_local_refine_max_moves=2,
        model5_inter_route_rebalance=m5_after,
        model5_inter_route_rebalance_rounds=1,
        model5_inter_route_rebalance_max_moves=base_args.rebalance_max_moves,
        model5_inter_route_rebalance_max_pairs=20,
        model5_inter_route_rebalance_position_top_k=4,
        model5_inter_route_pair_swap=False,
        model5_inter_route_pair_swap_max_pairs=12,
        model5_iterative_rebalance=False,
        model5_iterative_rebalance_trigger="fixed",
        model5_iterative_rebalance_interval=20,
        model5_iterative_rebalance_stagnation_window=20,
        model5_iterative_rebalance_rounds=1,
        model5_iterative_rebalance_max_moves=2,
        model5_iterative_rebalance_max_pairs=12,
        model5_iterative_rebalance_position_top_k=3,
        model5_iterative_rebalance_pair_swap=False,
        model5_iterative_rebalance_pair_swap_max_pairs=8,
        model5_strong_initial=True,
    )


def _gap_pct(cost, baseline):
    if cost is None or baseline is None or baseline <= 0:
        return None
    return (cost - baseline) / baseline * 100.0


def _best_cost(result_by_label, keys):
    costs = [
        result_by_label[key]["cost"] for key in keys
        if result_by_label[key]["cost"] is not None
    ]
    return min(costs) if costs else None


def _fmt(value):
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _print_summary(rows):
    print("dataset                 m2       m3       m4       m5_before m5_after  "
          "after-before  after-vs-best234")
    for row in rows:
        print(
            f"{row['dataset']:<22} "
            f"{_fmt(row['m2_cost']):>8} {_fmt(row['m3_cost']):>8} "
            f"{_fmt(row['m4_cost']):>8} {_fmt(row['m5_before_cost']):>9} "
            f"{_fmt(row['m5_after_cost']):>8} "
            f"{_fmt(row['m5_delta_pct']):>11}% "
            f"{_fmt(row['m5_after_vs_best234_pct']):>15}%"
        )


def _aggregate(rows):
    valid_delta = [
        row["m5_delta_pct"] for row in rows
        if row["m5_delta_pct"] is not None
    ]
    valid_vs_best = [
        row["m5_after_vs_best234_pct"] for row in rows
        if row["m5_after_vs_best234_pct"] is not None
    ]
    wins = sum(
        1 for row in rows
        if row["m5_delta_pct"] is not None and row["m5_delta_pct"] < -1e-9
    )
    ties = sum(
        1 for row in rows
        if row["m5_delta_pct"] is not None
        and abs(row["m5_delta_pct"]) <= 1e-9
    )
    losses = sum(
        1 for row in rows
        if row["m5_delta_pct"] is not None and row["m5_delta_pct"] > 1e-9
    )
    beats_best234 = sum(
        1 for row in rows
        if row["m5_after_vs_best234_pct"] is not None
        and row["m5_after_vs_best234_pct"] < -1e-9
    )
    return {
        "n": len(rows),
        "m5_avg_delta_pct": (
            sum(valid_delta) / len(valid_delta) if valid_delta else None
        ),
        "m5_wins": wins,
        "m5_ties": ties,
        "m5_losses": losses,
        "m5_after_avg_vs_best234_pct": (
            sum(valid_vs_best) / len(valid_vs_best)
            if valid_vs_best else None
        ),
        "m5_after_beats_best234": beats_best234,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Medium/large before-after comparison for Model 5.")
    parser.add_argument("--datasets", nargs="+", default=DEFAULT_DATASETS)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--time", type=float, default=10.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--transshipment-time-budget", type=float, default=0.03)
    parser.add_argument("--transshipment-candidate-mode",
                        default="all_sampled",
                        choices=["nearest_k", "all_sampled", "all_exhaustive"])
    parser.add_argument("--max-relocation-candidates", type=int, default=None)
    parser.add_argument("--sample-pickups", type=int, default=5)
    parser.add_argument("--sample-locations", type=int, default=3)
    parser.add_argument("--sample-carriers", type=int, default=3)
    parser.add_argument("--position-top-k", type=int, default=3)
    parser.add_argument("--min-net-saving", type=float, default=0.01)
    parser.add_argument("--max-total", type=int, default=3)
    parser.add_argument("--max-per-satellite", type=int, default=2)
    parser.add_argument("--stage1-time-ratio", type=float, default=0.95)
    parser.add_argument("--route-local-refine-rounds", type=int, default=2)
    parser.add_argument("--route-local-refine-max-moves", type=int, default=20)
    parser.add_argument("--rebalance-max-moves", type=int, default=5)
    parser.add_argument("--no-strong-baselines", action="store_true",
                        help="Do not use strong initial solution for M2/M3/M4.")
    parser.add_argument("--tag", default="medium_large")
    args = parser.parse_args()

    with open(DEFAULT_FLEET, "r", encoding="utf-8") as f:
        fleet_cfg = json.load(f)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    label = _safe_label(args.tag) or "medium_large"
    csv_path = OUT_DIR / (
        f"model5_before_after_{label}_iter{args.iterations}_seed{args.seed}.csv"
    )
    summary_path = csv_path.with_suffix(".summary.json")

    rows = []
    base_args = _args(args, m5_after=False)
    after_args = _args(args, m5_after=True)
    solvers = {
        "m2": (2, solve_m2),
        "m3": (3, solve_m3),
        "m4": (4, solve_m4),
    }

    for data_path in _resolve_datasets(args.datasets):
        dataset_label = f"{data_path.parent.name}_{data_path.stem}"
        result_by_label = {}
        print(f"\nRunning {dataset_label}...")
        for label_name, (model_id, solver_fn) in solvers.items():
            result_by_label[label_name] = _run_one(
                model_id, solver_fn, data_path, fleet_cfg, base_args)
            print(f"  {label_name}: {_fmt(result_by_label[label_name]['cost'])}")

        result_by_label["m5_before"] = _run_one(
            5, solve_m5, data_path, fleet_cfg, base_args)
        print(f"  m5_before: {_fmt(result_by_label['m5_before']['cost'])}")

        result_by_label["m5_after"] = _run_one(
            5, solve_m5, data_path, fleet_cfg, after_args)
        print(f"  m5_after: {_fmt(result_by_label['m5_after']['cost'])}")

        best234 = _best_cost(result_by_label, ("m2", "m3", "m4"))
        row = {
            "dataset": dataset_label,
            "m2_cost": result_by_label["m2"]["cost"],
            "m3_cost": result_by_label["m3"]["cost"],
            "m4_cost": result_by_label["m4"]["cost"],
            "best234_cost": best234,
            "m5_before_cost": result_by_label["m5_before"]["cost"],
            "m5_after_cost": result_by_label["m5_after"]["cost"],
            "m5_delta_pct": _gap_pct(
                result_by_label["m5_after"]["cost"],
                result_by_label["m5_before"]["cost"],
            ),
            "m5_before_vs_best234_pct": _gap_pct(
                result_by_label["m5_before"]["cost"], best234),
            "m5_after_vs_best234_pct": _gap_pct(
                result_by_label["m5_after"]["cost"], best234),
            "m5_before_transshipments": result_by_label["m5_before"]["transshipments"],
            "m5_after_transshipments": result_by_label["m5_after"]["transshipments"],
            "m5_after_route_refine_moves": result_by_label["m5_after"]["route_refine_moves"],
            "m5_after_rebalance_moves": result_by_label["m5_after"]["rebalance_moves"],
            "m5_after_elapsed": result_by_label["m5_after"]["elapsed"],
        }
        rows.append(row)

    fields = list(rows[0].keys()) if rows else []
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    summary = _aggregate(rows)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\nModel 5 before/after medium-large comparison")
    print(f"CSV: {csv_path}")
    print(f"Summary: {summary_path}")
    _print_summary(rows)
    print("\nAggregate:")
    for key, value in summary.items():
        print(f"  {key}: {_fmt(value)}")


if __name__ == "__main__":
    main()
