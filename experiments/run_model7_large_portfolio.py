# experiments/run_model7_large_portfolio.py
# -*- coding: utf-8 -*-
"""Model 7 portfolio experiments with optional hybrid safety variants."""
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


BASE_COMPACT_VARIANTS = [
    {
        "label": "sparse_greedy_T1S1",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 1,
        "max_per_satellite": 1,
    },
    {
        "label": "sparse_beam_T1S1",
        "strategy": "beam",
        "depth": 2,
        "max_total": 1,
        "max_per_satellite": 1,
    },
    {
        "label": "sparse_cons_T1S1",
        "strategy": "conservative",
        "depth": 1,
        "max_total": 1,
        "max_per_satellite": 1,
        "prune": True,
        "final_refine": True,
    },
    {
        "label": "sparse_guard_T1S1",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 1,
        "max_per_satellite": 1,
        "baseline_guard": True,
    },
    {
        "label": "mid_guard_T2S1",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 2,
        "max_per_satellite": 1,
        "baseline_guard": True,
    },
    {
        "label": "default_greedy_T3S2",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 3,
        "max_per_satellite": 2,
    },
    {
        "label": "wide_cons_T5S3",
        "strategy": "conservative",
        "depth": 1,
        "max_total": 5,
        "max_per_satellite": 3,
        "prune": True,
        "final_refine": True,
    },
    {
        "label": "wide_guard_T5S3",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 5,
        "max_per_satellite": 3,
        "baseline_guard": True,
    },
    {
        "label": "nearest_cons_T5S3",
        "strategy": "conservative",
        "depth": 1,
        "max_total": 5,
        "max_per_satellite": 3,
        "prune": True,
        "final_refine": True,
        "candidate_mode": "nearest_k",
        "max_relocation_candidates": 3,
    },
]


LEGACY_SAFETY_VARIANTS = [
    {
        "label": "legacy_greedy_T1S1",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 1,
        "max_per_satellite": 1,
        "strong_initial": False,
    },
    {
        "label": "legacy_beam_T1S1",
        "strategy": "beam",
        "depth": 2,
        "max_total": 1,
        "max_per_satellite": 1,
        "strong_initial": False,
    },
    {
        "label": "legacy_cons_T1S1",
        "strategy": "conservative",
        "depth": 1,
        "max_total": 1,
        "max_per_satellite": 1,
        "prune": True,
        "final_refine": True,
        "strong_initial": False,
    },
    {
        "label": "legacy_guard_T1S1",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 1,
        "max_per_satellite": 1,
        "baseline_guard": True,
        "strong_initial": False,
    },
    {
        "label": "legacy_guard_route_refine_T1S1",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 1,
        "max_per_satellite": 1,
        "baseline_guard": True,
        "route_local_refine": True,
        "strong_initial": False,
    },
    {
        "label": "legacy_wide_cons_T5S3",
        "strategy": "conservative",
        "depth": 1,
        "max_total": 5,
        "max_per_satellite": 3,
        "prune": True,
        "final_refine": True,
        "strong_initial": False,
    },
]


COMPACT_VARIANTS = BASE_COMPACT_VARIANTS + LEGACY_SAFETY_VARIANTS

V1_OPTIMIZED_EXTRA_VARIANTS = [
    {
        "label": "risk_sparse_guard_T1S1",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 1,
        "max_per_satellite": 1,
        "baseline_guard": True,
        "risk_aware": True,
        "slack_weight": 0.08,
        "capacity_weight": 0.04,
        "min_transfer_slack": 0.5,
        "min_return_slack": 0.5,
        "min_capacity_slack": 0.5,
    },
    {
        "label": "risk_nearest_guard_T5S3",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 5,
        "max_per_satellite": 3,
        "baseline_guard": True,
        "candidate_mode": "nearest_k",
        "max_relocation_candidates": 5,
        "risk_aware": True,
        "slack_weight": 0.08,
        "capacity_weight": 0.04,
        "min_transfer_slack": 0.5,
        "min_return_slack": 0.5,
        "min_capacity_slack": 0.5,
    },
    {
        "label": "exhaustive_sparse_guard_T1S1",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 1,
        "max_per_satellite": 1,
        "baseline_guard": True,
        "candidate_mode": "all_exhaustive",
    },
    {
        "label": "prefix_beam_T2S1",
        "strategy": "beam",
        "depth": 2,
        "selection": "best_prefix",
        "max_total": 2,
        "max_per_satellite": 1,
        "baseline_guard": True,
    },
]


STAGNATION_EXTRA_VARIANTS = [
    {
        "label": "stagnation_exhaustive_T1S1",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 1,
        "max_per_satellite": 1,
        "baseline_guard": True,
        "stagnation_boost": True,
        "stagnation_window": 12,
        "stagnation_depth": 2,
        "stagnation_time_factor": 2.5,
        "stagnation_candidate_mode": "all_exhaustive",
        "stagnation_position_top_k": 5,
    },
    {
        "label": "stagnation_nearest_T5S3",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 5,
        "max_per_satellite": 3,
        "baseline_guard": True,
        "candidate_mode": "nearest_k",
        "max_relocation_candidates": 5,
        "stagnation_boost": True,
        "stagnation_window": 15,
        "stagnation_depth": 2,
        "stagnation_time_factor": 2.0,
        "stagnation_candidate_mode": "nearest_k",
        "stagnation_max_relocation_candidates": 8,
        "stagnation_position_top_k": 5,
    },
    {
        "label": "stagnation_prefix_beam_T3S1",
        "strategy": "beam",
        "depth": 2,
        "selection": "best_prefix",
        "max_total": 3,
        "max_per_satellite": 1,
        "baseline_guard": True,
        "stagnation_boost": True,
        "stagnation_window": 15,
        "stagnation_depth": 3,
        "stagnation_time_factor": 2.0,
    },
    {
        "label": "risk_refine_guard_T5S3",
        "strategy": "conservative",
        "depth": 1,
        "max_total": 5,
        "max_per_satellite": 3,
        "baseline_guard": True,
        "candidate_mode": "nearest_k",
        "max_relocation_candidates": 5,
        "risk_aware": True,
        "slack_weight": 0.10,
        "capacity_weight": 0.05,
        "min_transfer_slack": 0.5,
        "min_return_slack": 0.5,
        "min_capacity_slack": 0.5,
        "prune": True,
        "final_refine": True,
        "route_local_refine": True,
    },
]


REBALANCE_EXTRA_VARIANTS = [
    {
        "label": "rebalance_guard_T1S1",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 1,
        "max_per_satellite": 1,
        "baseline_guard": True,
        "inter_route_rebalance": True,
        "inter_route_rebalance_rounds": 2,
        "inter_route_rebalance_max_moves": 8,
        "inter_route_rebalance_max_pairs": 24,
        "inter_route_rebalance_position_top_k": 4,
    },
    {
        "label": "risk_rebalance_guard_T5S3",
        "strategy": "conservative",
        "depth": 1,
        "max_total": 5,
        "max_per_satellite": 3,
        "baseline_guard": True,
        "candidate_mode": "nearest_k",
        "max_relocation_candidates": 5,
        "risk_aware": True,
        "slack_weight": 0.10,
        "capacity_weight": 0.05,
        "min_transfer_slack": 0.5,
        "min_return_slack": 0.5,
        "min_capacity_slack": 0.5,
        "prune": True,
        "final_refine": True,
        "route_local_refine": True,
        "inter_route_rebalance": True,
        "inter_route_rebalance_rounds": 2,
        "inter_route_rebalance_max_moves": 10,
        "inter_route_rebalance_max_pairs": 30,
        "inter_route_rebalance_position_top_k": 4,
    },
    {
        "label": "stagnation_rebalance_T5S3",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 5,
        "max_per_satellite": 3,
        "baseline_guard": True,
        "candidate_mode": "nearest_k",
        "max_relocation_candidates": 5,
        "stagnation_boost": True,
        "stagnation_window": 15,
        "stagnation_depth": 2,
        "stagnation_time_factor": 2.0,
        "stagnation_candidate_mode": "nearest_k",
        "stagnation_max_relocation_candidates": 8,
        "stagnation_position_top_k": 5,
        "inter_route_rebalance": True,
        "inter_route_rebalance_rounds": 2,
        "inter_route_rebalance_max_moves": 10,
        "inter_route_rebalance_max_pairs": 30,
        "inter_route_rebalance_position_top_k": 4,
    },
]


SWAP_EXTRA_VARIANTS = [
    {
        "label": "swap_rebalance_guard_T1S1",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 1,
        "max_per_satellite": 1,
        "baseline_guard": True,
        "inter_route_rebalance": True,
        "inter_route_rebalance_rounds": 2,
        "inter_route_rebalance_max_moves": 10,
        "inter_route_rebalance_max_pairs": 24,
        "inter_route_rebalance_position_top_k": 4,
        "inter_route_pair_swap": True,
        "inter_route_pair_swap_max_pairs": 12,
    },
    {
        "label": "risk_swap_rebalance_T5S3",
        "strategy": "conservative",
        "depth": 1,
        "max_total": 5,
        "max_per_satellite": 3,
        "baseline_guard": True,
        "candidate_mode": "nearest_k",
        "max_relocation_candidates": 5,
        "risk_aware": True,
        "slack_weight": 0.10,
        "capacity_weight": 0.05,
        "min_transfer_slack": 0.5,
        "min_return_slack": 0.5,
        "min_capacity_slack": 0.5,
        "prune": True,
        "final_refine": True,
        "route_local_refine": True,
        "inter_route_rebalance": True,
        "inter_route_rebalance_rounds": 2,
        "inter_route_rebalance_max_moves": 12,
        "inter_route_rebalance_max_pairs": 30,
        "inter_route_rebalance_position_top_k": 4,
        "inter_route_pair_swap": True,
        "inter_route_pair_swap_max_pairs": 14,
    },
]


ITERATIVE_REBALANCE_EXTRA_VARIANTS = [
    {
        "label": "v5_fixed_rebalance_T1S1",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 1,
        "max_per_satellite": 1,
        "baseline_guard": True,
        "inter_route_rebalance": True,
        "inter_route_rebalance_rounds": 2,
        "inter_route_rebalance_max_moves": 8,
        "inter_route_rebalance_max_pairs": 24,
        "inter_route_rebalance_position_top_k": 4,
        "iterative_rebalance": True,
        "iterative_rebalance_trigger": "fixed",
        "iterative_rebalance_interval": 10,
        "iterative_rebalance_rounds": 1,
        "iterative_rebalance_max_moves": 1,
        "iterative_rebalance_max_pairs": 12,
        "iterative_rebalance_position_top_k": 3,
    },
    {
        "label": "v5_stag_rebalance_T1S1",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 1,
        "max_per_satellite": 1,
        "baseline_guard": True,
        "inter_route_rebalance": True,
        "inter_route_rebalance_rounds": 2,
        "inter_route_rebalance_max_moves": 8,
        "inter_route_rebalance_max_pairs": 24,
        "inter_route_rebalance_position_top_k": 4,
        "iterative_rebalance": True,
        "iterative_rebalance_trigger": "stagnation",
        "iterative_rebalance_interval": 8,
        "iterative_rebalance_stagnation_window": 12,
        "iterative_rebalance_rounds": 1,
        "iterative_rebalance_max_moves": 2,
        "iterative_rebalance_max_pairs": 16,
        "iterative_rebalance_position_top_k": 3,
    },
    {
        "label": "v5_stag_swap_T1S1",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 1,
        "max_per_satellite": 1,
        "baseline_guard": True,
        "inter_route_rebalance": True,
        "inter_route_rebalance_rounds": 2,
        "inter_route_rebalance_max_moves": 10,
        "inter_route_rebalance_max_pairs": 24,
        "inter_route_rebalance_position_top_k": 4,
        "inter_route_pair_swap": True,
        "inter_route_pair_swap_max_pairs": 12,
        "iterative_rebalance": True,
        "iterative_rebalance_trigger": "stagnation",
        "iterative_rebalance_interval": 8,
        "iterative_rebalance_stagnation_window": 12,
        "iterative_rebalance_rounds": 1,
        "iterative_rebalance_max_moves": 2,
        "iterative_rebalance_max_pairs": 16,
        "iterative_rebalance_position_top_k": 3,
        "iterative_rebalance_pair_swap": True,
        "iterative_rebalance_pair_swap_max_pairs": 8,
    },
]


STABLE_V6_EXTRA_VARIANTS = [
    {
        "label": "v6_fixed_rebalance_T2S1",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 2,
        "max_per_satellite": 1,
        "baseline_guard": True,
        "route_local_refine": True,
        "inter_route_rebalance": True,
        "inter_route_rebalance_rounds": 2,
        "inter_route_rebalance_max_moves": 10,
        "inter_route_rebalance_max_pairs": 30,
        "inter_route_rebalance_position_top_k": 4,
        "iterative_rebalance": True,
        "iterative_rebalance_trigger": "fixed",
        "iterative_rebalance_interval": 10,
        "iterative_rebalance_rounds": 1,
        "iterative_rebalance_max_moves": 2,
        "iterative_rebalance_max_pairs": 16,
        "iterative_rebalance_position_top_k": 3,
    },
    {
        "label": "v6_stag_swap_T2S1",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 2,
        "max_per_satellite": 1,
        "baseline_guard": True,
        "route_local_refine": True,
        "inter_route_rebalance": True,
        "inter_route_rebalance_rounds": 2,
        "inter_route_rebalance_max_moves": 12,
        "inter_route_rebalance_max_pairs": 30,
        "inter_route_rebalance_position_top_k": 4,
        "inter_route_pair_swap": True,
        "inter_route_pair_swap_max_pairs": 14,
        "iterative_rebalance": True,
        "iterative_rebalance_trigger": "stagnation",
        "iterative_rebalance_interval": 8,
        "iterative_rebalance_stagnation_window": 12,
        "iterative_rebalance_rounds": 1,
        "iterative_rebalance_max_moves": 2,
        "iterative_rebalance_max_pairs": 18,
        "iterative_rebalance_position_top_k": 3,
        "iterative_rebalance_pair_swap": True,
        "iterative_rebalance_pair_swap_max_pairs": 10,
    },
    {
        "label": "v6_cand_refine_rebalance_T1S1",
        "strategy": "greedy",
        "depth": 1,
        "max_total": 1,
        "max_per_satellite": 1,
        "baseline_guard": True,
        "candidate_route_local_refine": True,
        "candidate_route_local_refine_rounds": 1,
        "candidate_route_local_refine_max_moves": 3,
        "route_local_refine": True,
        "inter_route_rebalance": True,
        "inter_route_rebalance_rounds": 2,
        "inter_route_rebalance_max_moves": 10,
        "inter_route_rebalance_max_pairs": 30,
        "inter_route_rebalance_position_top_k": 4,
        "iterative_rebalance": True,
        "iterative_rebalance_trigger": "both",
        "iterative_rebalance_interval": 10,
        "iterative_rebalance_stagnation_window": 12,
        "iterative_rebalance_rounds": 1,
        "iterative_rebalance_max_moves": 1,
        "iterative_rebalance_max_pairs": 16,
        "iterative_rebalance_position_top_k": 3,
    },
    {
        "label": "v6_risk_cand_rebalance_T5S3",
        "strategy": "conservative",
        "depth": 1,
        "max_total": 5,
        "max_per_satellite": 3,
        "baseline_guard": True,
        "candidate_mode": "nearest_k",
        "max_relocation_candidates": 6,
        "risk_aware": True,
        "slack_weight": 0.10,
        "capacity_weight": 0.05,
        "min_transfer_slack": 0.5,
        "min_return_slack": 0.5,
        "min_capacity_slack": 0.5,
        "prune": True,
        "final_refine": True,
        "candidate_route_local_refine": True,
        "candidate_route_local_refine_rounds": 1,
        "candidate_route_local_refine_max_moves": 2,
        "route_local_refine": True,
        "inter_route_rebalance": True,
        "inter_route_rebalance_rounds": 2,
        "inter_route_rebalance_max_moves": 12,
        "inter_route_rebalance_max_pairs": 32,
        "inter_route_rebalance_position_top_k": 4,
    },
]


OPTIMIZED_EXTRA_VARIANTS = (
    V1_OPTIMIZED_EXTRA_VARIANTS
    + STAGNATION_EXTRA_VARIANTS
    + REBALANCE_EXTRA_VARIANTS
    + SWAP_EXTRA_VARIANTS
    + ITERATIVE_REBALANCE_EXTRA_VARIANTS
    + STABLE_V6_EXTRA_VARIANTS
)


def get_compact_variants(include_legacy_safety=True):
    variants = list(BASE_COMPACT_VARIANTS)
    if include_legacy_safety:
        variants.extend(LEGACY_SAFETY_VARIANTS)
    return variants


def get_optimized_variants(include_legacy_safety=True):
    variants = get_compact_variants(include_legacy_safety)
    variants.extend(OPTIMIZED_EXTRA_VARIANTS)
    return variants


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
        max_total=variant.get("max_total", 1),
        max_per_satellite=variant.get("max_per_satellite", 1),
        stage1_time_ratio=0.95,
        model7_depth=variant.get("depth", 1),
        model7_schedule="fixed",
        model7_selection=variant.get("selection", "last"),
        model7_strategy=variant.get("strategy", "greedy"),
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
        model7_quality_gap=variant.get("quality_gap", None),
        model7_stagnation_boost=variant.get("stagnation_boost", False),
        model7_stagnation_window=variant.get("stagnation_window", 30),
        model7_stagnation_depth=variant.get("stagnation_depth", None),
        model7_stagnation_time_factor=variant.get(
            "stagnation_time_factor", 2.0),
        model7_stagnation_candidate_mode=variant.get(
            "stagnation_candidate_mode", None),
        model7_stagnation_max_relocation_candidates=variant.get(
            "stagnation_max_relocation_candidates", None),
        model7_stagnation_position_top_k=variant.get(
            "stagnation_position_top_k", None),
        model7_prune=variant.get("prune", False),
        model7_prune_min_improvement=args.prune_min_improvement,
        model7_prune_max_rounds=args.prune_max_rounds,
        model7_final_refine=variant.get("final_refine", False),
        model7_final_refine_depth=args.final_refine_depth,
        model7_baseline_guard=variant.get("baseline_guard", False),
        model7_baseline_iterations=None,
        model7_baseline_time=None,
        model7_route_local_refine=variant.get(
            "route_local_refine", False),
        model7_route_local_refine_rounds=args.route_local_refine_rounds,
        model7_route_local_refine_max_moves=args.route_local_refine_max_moves,
        model7_candidate_route_local_refine=variant.get(
            "candidate_route_local_refine", False),
        model7_candidate_route_local_refine_rounds=variant.get(
            "candidate_route_local_refine_rounds", 1),
        model7_candidate_route_local_refine_max_moves=variant.get(
            "candidate_route_local_refine_max_moves", 2),
        model7_inter_route_rebalance=variant.get(
            "inter_route_rebalance", False),
        model7_inter_route_rebalance_rounds=variant.get(
            "inter_route_rebalance_rounds", 1),
        model7_inter_route_rebalance_max_moves=variant.get(
            "inter_route_rebalance_max_moves", 10),
        model7_inter_route_rebalance_max_pairs=variant.get(
            "inter_route_rebalance_max_pairs", 20),
        model7_inter_route_rebalance_position_top_k=variant.get(
            "inter_route_rebalance_position_top_k", 4),
        model7_inter_route_pair_swap=variant.get(
            "inter_route_pair_swap", False),
        model7_inter_route_pair_swap_max_pairs=variant.get(
            "inter_route_pair_swap_max_pairs", 12),
        model7_iterative_rebalance=variant.get(
            "iterative_rebalance", False),
        model7_iterative_rebalance_trigger=variant.get(
            "iterative_rebalance_trigger", "fixed"),
        model7_iterative_rebalance_interval=variant.get(
            "iterative_rebalance_interval", 20),
        model7_iterative_rebalance_stagnation_window=variant.get(
            "iterative_rebalance_stagnation_window", 20),
        model7_iterative_rebalance_rounds=variant.get(
            "iterative_rebalance_rounds", 1),
        model7_iterative_rebalance_max_moves=variant.get(
            "iterative_rebalance_max_moves", 2),
        model7_iterative_rebalance_max_pairs=variant.get(
            "iterative_rebalance_max_pairs", 12),
        model7_iterative_rebalance_position_top_k=variant.get(
            "iterative_rebalance_position_top_k", 3),
        model7_iterative_rebalance_pair_swap=variant.get(
            "iterative_rebalance_pair_swap", False),
        model7_iterative_rebalance_pair_swap_max_pairs=variant.get(
            "iterative_rebalance_pair_swap_max_pairs", 8),
        model7_strong_initial=variant.get(
            "strong_initial", not args.model7_legacy_initial),
    )


def main():
    parser = argparse.ArgumentParser(
        description="Run Model 7 portfolio on benchmark instances.")
    parser.add_argument("--datasets", nargs="+", required=True)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--time", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=42)
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
    parser.add_argument("--no-legacy-safety", action="store_true",
                        help="Use only the strong-initial compact variants.")
    parser.add_argument("--tag", default=None,
                        help="Optional filename tag to avoid overwriting prior runs.")
    args = parser.parse_args()

    datasets = _resolve_datasets(args.datasets)
    with open(DEFAULT_FLEET, "r", encoding="utf-8") as f:
        fleet_cfg = json.load(f)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = []
    for index, data_path in enumerate(datasets, start=1):
        dataset = _dataset_label(data_path)
        print(f"\n[{index}/{len(datasets)}] {dataset}")

        base_args = _make_args(args)
        model3 = _run_one(3, solve_m3, data_path, fleet_cfg, base_args)
        print(f"  Model 3: {model3['cost']}")

        runs = []
        variants = get_compact_variants(
            include_legacy_safety=not args.no_legacy_safety)
        for variant in variants:
            v_args = _make_args(args, variant)
            result = _run_one(7, solve_m7, data_path, fleet_cfg, v_args)
            result["label"] = variant["label"]
            runs.append(result)
            print(f"  {variant['label']}: {result['cost']}")

        feasible = [
            run for run in runs
            if run["success"] and run["cost"] is not None
        ]
        if feasible:
            best = min(feasible, key=lambda run: run["cost"])
        else:
            best = {
                "cost": None,
                "transshipments": None,
                "label": "no_feasible_m7",
            }

        if model3["cost"] is None and best["cost"] is not None:
            gap = None
            gap_pct = None
            verdict = "m3_failed_m7_feasible"
        elif model3["cost"] is not None and best["cost"] is None:
            gap = None
            gap_pct = None
            verdict = "m7_failed"
        elif model3["cost"] is None and best["cost"] is None:
            gap = None
            gap_pct = None
            verdict = "both_failed"
        else:
            gap = best["cost"] - model3["cost"]
            gap_pct = gap / model3["cost"] * 100.0
            verdict = "better" if gap < -1e-9 else (
                "tie" if abs(gap) < 1e-9 else "worse")

        row = {
            "dataset": dataset,
            "model3_cost": model3["cost"],
            "model3_trans": model3["transshipments"],
            "best_m7_cost": best["cost"],
            "best_m7_trans": best["transshipments"],
            "best_m7_label": best["label"],
            "gap": gap,
            "gap_pct": gap_pct,
            "verdict": verdict,
        }
        for run in runs:
            row[f"{run['label']}_cost"] = run["cost"]
            row[f"{run['label']}_trans"] = run["transshipments"]
        rows.append(row)

    portfolio_label = (
        "compact" if args.no_legacy_safety else "hybrid_safety"
    )
    initial_label = "legacy_initial" if args.model7_legacy_initial else "strong_initial"
    if args.model7_legacy_initial:
        portfolio_label = f"{portfolio_label}_legacy_base"
    label = f"{len(datasets)}datasets_{initial_label}"
    label = f"{label}_{portfolio_label}"
    if args.tag:
        label = f"{label}_{args.tag}"
    csv_path = OUT_DIR / (
        f"model7_large_portfolio_{label}_iter{args.iterations}_"
        f"seed{args.seed}_summary.csv"
    )
    _write_csv(csv_path, rows)
    md_path = csv_path.with_suffix(".md")
    _write_md(md_path, rows)

    comparable = [row for row in rows if row["gap_pct"] is not None]
    better = sum(row["verdict"] == "better" for row in rows)
    tie = sum(row["verdict"] == "tie" for row in rows)
    worse = sum(row["verdict"] == "worse" for row in rows)
    recovered = sum(row["verdict"] == "m3_failed_m7_feasible" for row in rows)
    m7_failed = sum(row["verdict"] == "m7_failed" for row in rows)
    both_failed = sum(row["verdict"] == "both_failed" for row in rows)
    avg_gap_pct = (
        sum(row["gap_pct"] for row in comparable) / max(len(comparable), 1)
    )
    print("\nLarge portfolio summary")
    print(f"better={better}, tie={tie}, worse={worse}, "
          f"recovered={recovered}, m7_failed={m7_failed}, "
          f"both_failed={both_failed}, "
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
    verdict_label = {
        "better": "更好",
        "tie": "持平",
        "worse": "更差",
        "m3_failed_m7_feasible": "M3无解/M7可行",
        "m7_failed": "M7无解",
        "both_failed": "均无解",
    }
    lines = [
        "# Model 7 Large Portfolio",
        "",
        "gap = Best M7 - Model 3；负数表示 Model 7 更好。",
        "",
        "| 算例 | Model 3 | Best M7 | 策略 | gap | gap% | 结论 |",
        "| --- | ---: | ---: | --- | ---: | ---: | --- |",
    ]
    for row in rows:
        m3 = "-" if row["model3_cost"] is None else (
            f"{row['model3_cost']:.2f} / {row['model3_trans']}")
        best = "-" if row["best_m7_cost"] is None else (
            f"{row['best_m7_cost']:.2f} / {row['best_m7_trans']}")
        gap = "-" if row["gap"] is None else f"{row['gap']:.2f}"
        gap_pct = "-" if row["gap_pct"] is None else f"{row['gap_pct']:.2f}%"
        lines.append(
            f"| {row['dataset']} "
            f"| {m3} "
            f"| {best} "
            f"| {row['best_m7_label']} "
            f"| {gap} "
            f"| {gap_pct} "
            f"| {verdict_label[row['verdict']]} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
