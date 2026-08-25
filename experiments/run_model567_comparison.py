# experiments/run_model567_comparison.py
# -*- coding: utf-8 -*-
"""Compare Model 3/5/6/7 ALNS on non-Beijing benchmark instances."""
import argparse
import io
import json
import re
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.instance import ProblemInstance
from models.model_3.alns_solver import solve_alns as solve_m3
from models.model_5.alns_solver import solve_alns as solve_m5
from models.model_6.alns_solver import solve_alns as solve_m6
from models.model_7.alns_solver import solve_alns as solve_m7
from utils.data_loader import parse_dataset
from utils.task_builder import build_tasks_for_model


DEFAULT_DATA = ROOT / "data" / "10_customers" / "C101.csv"
DEFAULT_FLEET = ROOT / "config" / "fleet_config.json"
OUT_DIR = ROOT / "output" / "experiments"


def _extract(pattern, text, default=None, cast=float):
    matches = re.findall(pattern, text, flags=re.MULTILINE)
    if not matches:
        return default
    return cast(matches[-1])


def _run_one(model_id, solver_fn, data_path, fleet_cfg, args):
    nodes_dict, _ = parse_dataset(str(data_path))
    problem = ProblemInstance(
        nodes_dict, fleet_cfg, speed=fleet_cfg.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, model_id)

    kwargs = dict(
        problem=problem,
        e1_tasks=e1_tasks,
        e2_tasks_by_sat=e2_tasks_by_sat,
        vehicles_per_satellite=problem.fleet.e2_per_sat_count,
        max_iterations=args.iterations,
        max_time=args.time,
        seed=args.seed,
        verbose=True,
    )
    if model_id in (3, 6):
        kwargs["TFp"] = 9999.0
    if model_id in (3, 5, 6):
        kwargs["strong_initial_solution"] = getattr(
            args, "baseline_strong_initial", False)
    if model_id in (5, 6, 7):
        kwargs.update(
            transshipment_time_budget=args.transshipment_time_budget,
            transshipment_sample_pickups=args.sample_pickups,
            transshipment_sample_locations=args.sample_locations,
            transshipment_sample_carriers=args.sample_carriers,
            transshipment_position_top_k=args.position_top_k,
            transshipment_min_net_saving=args.min_net_saving,
            transshipment_max_total=args.max_total,
            transshipment_max_per_satellite=args.max_per_satellite,
        )
    if model_id in (5, 7):
        kwargs.update(
            transshipment_candidate_mode=getattr(
                args, "transshipment_candidate_mode", "all_sampled"),
            max_relocation_candidates=getattr(
                args, "max_relocation_candidates", None),
        )
    if model_id == 6:
        kwargs["stage1_time_ratio"] = args.stage1_time_ratio
    if model_id == 7:
        kwargs["transshipment_interval"] = 1
        kwargs["transshipment_probe_depth"] = args.model7_depth
        kwargs["transshipment_probe_schedule"] = args.model7_schedule
        kwargs["transshipment_probe_selection"] = args.model7_selection
        kwargs["transshipment_probe_strategy"] = args.model7_strategy
        kwargs["transshipment_probe_pool_size"] = args.model7_pool_size
        kwargs["transshipment_probe_alpha"] = args.model7_alpha
        kwargs["transshipment_probe_beam_width"] = args.model7_beam_width
        kwargs["transshipment_conservative_min_saving"] = (
            args.model7_conservative_min_saving)
        kwargs["transshipment_risk_aware"] = getattr(
            args, "model7_risk_aware", False)
        kwargs["transshipment_slack_weight"] = getattr(
            args, "model7_slack_weight", 0.0)
        kwargs["transshipment_capacity_weight"] = getattr(
            args, "model7_capacity_weight", 0.0)
        kwargs["transshipment_min_transfer_slack"] = getattr(
            args, "model7_min_transfer_slack", None)
        kwargs["transshipment_min_return_slack"] = getattr(
            args, "model7_min_return_slack", None)
        kwargs["transshipment_min_capacity_slack"] = getattr(
            args, "model7_min_capacity_slack", None)
        kwargs["transshipment_probe_quality_gap"] = args.model7_quality_gap
        kwargs["transshipment_stagnation_boost"] = getattr(
            args, "model7_stagnation_boost", False)
        kwargs["transshipment_stagnation_window"] = getattr(
            args, "model7_stagnation_window", 30)
        kwargs["transshipment_stagnation_depth"] = getattr(
            args, "model7_stagnation_depth", None)
        kwargs["transshipment_stagnation_time_factor"] = getattr(
            args, "model7_stagnation_time_factor", 2.0)
        kwargs["transshipment_stagnation_candidate_mode"] = getattr(
            args, "model7_stagnation_candidate_mode", None)
        kwargs["transshipment_stagnation_max_relocation_candidates"] = getattr(
            args, "model7_stagnation_max_relocation_candidates", None)
        kwargs["transshipment_stagnation_position_top_k"] = getattr(
            args, "model7_stagnation_position_top_k", None)
        kwargs["transshipment_prune"] = args.model7_prune
        kwargs["transshipment_prune_min_improvement"] = (
            args.model7_prune_min_improvement)
        kwargs["transshipment_prune_max_rounds"] = (
            args.model7_prune_max_rounds)
        kwargs["transshipment_final_refine"] = args.model7_final_refine
        kwargs["transshipment_final_refine_depth"] = (
            args.model7_final_refine_depth)
        kwargs["transshipment_baseline_guard"] = args.model7_baseline_guard
        kwargs["transshipment_baseline_iterations"] = (
            args.model7_baseline_iterations)
        kwargs["transshipment_baseline_time"] = args.model7_baseline_time
        kwargs["route_local_refine"] = getattr(
            args, "model7_route_local_refine", False)
        kwargs["route_local_refine_rounds"] = getattr(
            args, "model7_route_local_refine_rounds", 2)
        kwargs["route_local_refine_max_moves"] = getattr(
            args, "model7_route_local_refine_max_moves", 20)
        kwargs["candidate_route_local_refine"] = getattr(
            args, "model7_candidate_route_local_refine", False)
        kwargs["candidate_route_local_refine_rounds"] = getattr(
            args, "model7_candidate_route_local_refine_rounds", 1)
        kwargs["candidate_route_local_refine_max_moves"] = getattr(
            args, "model7_candidate_route_local_refine_max_moves", 2)
        kwargs["inter_route_rebalance"] = getattr(
            args, "model7_inter_route_rebalance", False)
        kwargs["inter_route_rebalance_rounds"] = getattr(
            args, "model7_inter_route_rebalance_rounds", 1)
        kwargs["inter_route_rebalance_max_moves"] = getattr(
            args, "model7_inter_route_rebalance_max_moves", 10)
        kwargs["inter_route_rebalance_max_pairs"] = getattr(
            args, "model7_inter_route_rebalance_max_pairs", 20)
        kwargs["inter_route_rebalance_position_top_k"] = getattr(
            args, "model7_inter_route_rebalance_position_top_k", 4)
        kwargs["inter_route_pair_swap"] = getattr(
            args, "model7_inter_route_pair_swap", False)
        kwargs["inter_route_pair_swap_max_pairs"] = getattr(
            args, "model7_inter_route_pair_swap_max_pairs", 12)
        kwargs["iterative_rebalance"] = getattr(
            args, "model7_iterative_rebalance", False)
        kwargs["iterative_rebalance_trigger"] = getattr(
            args, "model7_iterative_rebalance_trigger", "fixed")
        kwargs["iterative_rebalance_interval"] = getattr(
            args, "model7_iterative_rebalance_interval", 20)
        kwargs["iterative_rebalance_stagnation_window"] = getattr(
            args, "model7_iterative_rebalance_stagnation_window", 20)
        kwargs["iterative_rebalance_rounds"] = getattr(
            args, "model7_iterative_rebalance_rounds", 1)
        kwargs["iterative_rebalance_max_moves"] = getattr(
            args, "model7_iterative_rebalance_max_moves", 2)
        kwargs["iterative_rebalance_max_pairs"] = getattr(
            args, "model7_iterative_rebalance_max_pairs", 12)
        kwargs["iterative_rebalance_position_top_k"] = getattr(
            args, "model7_iterative_rebalance_position_top_k", 3)
        kwargs["iterative_rebalance_pair_swap"] = getattr(
            args, "model7_iterative_rebalance_pair_swap", False)
        kwargs["iterative_rebalance_pair_swap_max_pairs"] = getattr(
            args, "model7_iterative_rebalance_pair_swap_max_pairs", 8)
        kwargs["strong_initial_solution"] = getattr(
            args, "model7_strong_initial", True)

    buf = io.StringIO()
    start = time.time()
    with redirect_stdout(buf):
        e1_routes, e2_routes = solver_fn(**kwargs)
    elapsed = time.time() - start
    log = buf.getvalue()

    if model_id == 6:
        cost = _extract(r"阶段二\(中转后\):\s*([0-9.]+)", log)
        stage1 = _extract(r"阶段一\(无中转\):\s*([0-9.]+)", log)
    else:
        cost = _extract(r"最优目标值:\s*([0-9.]+)", log)
        stage1 = None

    return {
        "model": model_id,
        "success": bool(e1_routes and e2_routes),
        "cost": cost,
        "stage1_cost": stage1,
        "transshipments": _extract(
            r"(?:最优解中转操作数|中转操作数):\s*(\d+)",
            log, default=0, cast=int),
        "trials": _extract(r"中转试探次数:\s*(\d+)", log, default=None, cast=int),
        "steps": _extract(r"中转试探步数:\s*(\d+)", log, default=None, cast=int),
        "trial_applied": _extract(
            r"中转成功加入候选次数:\s*(\d+)", log, default=None, cast=int),
        "pruned": _extract(r"^中转撤销次数:\s*(\d+)", log, default=None, cast=int),
        "candidate_route_refine_moves": _extract(
            r"^候选路线精修移动次数:\s*(\d+)", log, default=None, cast=int),
        "route_refine_moves": _extract(
            r"^路线精修移动次数:\s*(\d+)", log, default=None, cast=int),
        "rebalance_moves": _extract(
            r"^跨车重排移动次数:\s*(\d+)", log, default=None, cast=int),
        "rebalance_swaps": _extract(
            r"^跨车交换次数:\s*(\d+)", log, default=None, cast=int),
        "iter_rebalance_calls": _extract(
            r"迭代跨车重排触发次数:\s*(\d+)", log, default=None, cast=int),
        "iter_rebalance_moves": _extract(
            r"迭代跨车重排移动次数:\s*(\d+)", log, default=None, cast=int),
        "iter_rebalance_swaps": _extract(
            r"迭代跨车交换次数:\s*(\d+)", log, default=None, cast=int),
        "elapsed": elapsed,
        "log": log,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Run Model 3/5/6/7 ALNS comparison on non-Beijing data.")
    parser.add_argument("--data", default=str(DEFAULT_DATA))
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument("--time", type=float, default=20.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--strong-initial-baselines",
                        dest="baseline_strong_initial",
                        action="store_true",
                        help="Use the strong initial solution for Models 3/5/6.")
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
    parser.add_argument("--max-total", type=int, default=None)
    parser.add_argument("--max-per-satellite", type=int, default=None)
    parser.add_argument("--stage1-time-ratio", type=float, default=0.95)
    parser.add_argument("--model7-depth", type=int, default=1)
    parser.add_argument("--model7-schedule", default="fixed",
                        choices=["fixed", "decreasing"])
    parser.add_argument("--model7-selection", default="last",
                        choices=["last", "best_prefix"])
    parser.add_argument("--model7-strategy", default="greedy",
                        choices=["greedy", "range_random", "conservative",
                                 "beam"])
    parser.add_argument("--model7-pool-size", type=int, default=4)
    parser.add_argument("--model7-alpha", type=float, default=0.8)
    parser.add_argument("--model7-beam-width", type=int, default=3)
    parser.add_argument("--model7-conservative-min-saving", type=float,
                        default=1.0)
    parser.add_argument("--model7-risk-aware", action="store_true")
    parser.add_argument("--model7-slack-weight", type=float, default=0.0)
    parser.add_argument("--model7-capacity-weight", type=float, default=0.0)
    parser.add_argument("--model7-min-transfer-slack", type=float,
                        default=None)
    parser.add_argument("--model7-min-return-slack", type=float, default=None)
    parser.add_argument("--model7-min-capacity-slack", type=float,
                        default=None)
    parser.add_argument("--model7-quality-gap", type=float, default=None)
    parser.add_argument("--model7-prune", action="store_true")
    parser.add_argument("--model7-prune-min-improvement", type=float,
                        default=0.01)
    parser.add_argument("--model7-prune-max-rounds", type=int, default=1)
    parser.add_argument("--model7-final-refine", action="store_true")
    parser.add_argument("--model7-final-refine-depth", type=int, default=10)
    parser.add_argument("--model7-baseline-guard", action="store_true")
    parser.add_argument("--model7-baseline-iterations", type=int, default=None)
    parser.add_argument("--model7-baseline-time", type=float, default=None)
    parser.add_argument("--model7-route-local-refine", action="store_true")
    parser.add_argument("--model7-route-local-refine-rounds", type=int,
                        default=2)
    parser.add_argument("--model7-route-local-refine-max-moves", type=int,
                        default=20)
    parser.add_argument("--model7-candidate-route-local-refine",
                        action="store_true")
    parser.add_argument("--model7-candidate-route-local-refine-rounds",
                        type=int, default=1)
    parser.add_argument("--model7-candidate-route-local-refine-max-moves",
                        type=int, default=2)
    parser.add_argument("--model7-inter-route-rebalance", action="store_true")
    parser.add_argument("--model7-inter-route-rebalance-rounds", type=int,
                        default=1)
    parser.add_argument("--model7-inter-route-rebalance-max-moves", type=int,
                        default=10)
    parser.add_argument("--model7-inter-route-rebalance-max-pairs", type=int,
                        default=20)
    parser.add_argument("--model7-inter-route-rebalance-position-top-k",
                        type=int, default=4)
    parser.add_argument("--model7-inter-route-pair-swap",
                        action="store_true")
    parser.add_argument("--model7-inter-route-pair-swap-max-pairs", type=int,
                        default=12)
    parser.add_argument("--model7-iterative-rebalance", action="store_true")
    parser.add_argument("--model7-iterative-rebalance-trigger",
                        default="fixed",
                        choices=["fixed", "stagnation", "both"])
    parser.add_argument("--model7-iterative-rebalance-interval", type=int,
                        default=20)
    parser.add_argument("--model7-iterative-rebalance-stagnation-window",
                        type=int, default=20)
    parser.add_argument("--model7-iterative-rebalance-rounds", type=int,
                        default=1)
    parser.add_argument("--model7-iterative-rebalance-max-moves", type=int,
                        default=2)
    parser.add_argument("--model7-iterative-rebalance-max-pairs", type=int,
                        default=12)
    parser.add_argument("--model7-iterative-rebalance-position-top-k",
                        type=int, default=3)
    parser.add_argument("--model7-iterative-rebalance-pair-swap",
                        action="store_true")
    parser.add_argument("--model7-iterative-rebalance-pair-swap-max-pairs",
                        type=int, default=8)
    parser.add_argument("--model7-legacy-initial", action="store_true",
                        help="Use the legacy Model 5 initial solution in Model 7.")
    parser.add_argument("--tag", default=None,
                        help="Optional suffix for output filenames.")
    args = parser.parse_args()
    args.model7_strong_initial = not args.model7_legacy_initial

    data_path = Path(args.data)
    if not data_path.is_absolute():
        data_path = ROOT / data_path

    with open(DEFAULT_FLEET, "r", encoding="utf-8") as f:
        fleet_cfg = json.load(f)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    solvers = [(3, solve_m3), (5, solve_m5), (6, solve_m6), (7, solve_m7)]
    results = []
    data_label = f"{data_path.parent.name}_{data_path.stem}"
    output_label = data_label
    if args.tag:
        safe_tag = re.sub(r"[^A-Za-z0-9_.-]+", "_", args.tag).strip("_")
        if safe_tag:
            output_label = f"{data_label}_{safe_tag}"
    for model_id, solver_fn in solvers:
        result = _run_one(model_id, solver_fn, data_path, fleet_cfg, args)
        results.append(result)
        log_path = OUT_DIR / (
            f"model{model_id}_{output_label}_"
            f"iter{args.iterations}_seed{args.seed}.log"
        )
        log_path.write_text(result["log"], encoding="utf-8")

    summary_path = OUT_DIR / (
        f"model3567_{output_label}_iter{args.iterations}_"
        f"seed{args.seed}_summary.csv"
    )
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("model,success,cost,stage1_cost,transshipments,"
                "trials,steps,trial_applied,pruned,"
                "candidate_route_refine_moves,route_refine_moves,"
                "rebalance_moves,rebalance_swaps,"
                "iter_rebalance_calls,iter_rebalance_moves,"
                "iter_rebalance_swaps,"
                "elapsed\n")
        for r in results:
            f.write(
                f"{r['model']},{r['success']},{r['cost']},"
                f"{r['stage1_cost']},{r['transshipments']},"
                f"{r['trials']},{r['steps']},{r['trial_applied']},"
                f"{r['pruned']},{r['candidate_route_refine_moves']},"
                f"{r['route_refine_moves']},"
                f"{r['rebalance_moves']},{r['rebalance_swaps']},"
                f"{r['iter_rebalance_calls']},{r['iter_rebalance_moves']},"
                f"{r['iter_rebalance_swaps']},"
                f"{r['elapsed']:.2f}\n"
            )

    print("Model 3/5/6/7 ALNS comparison")
    print(f"Data: {data_path}")
    print(f"Iterations: {args.iterations}, time limit/model: {args.time}s, "
          f"seed: {args.seed}")
    print("model  success  cost      stage1    trans  trials   steps  applied  rls  reb  swp  time")
    for r in results:
        cost = "-" if r["cost"] is None else f"{r['cost']:.2f}"
        stage1 = "-" if r["stage1_cost"] is None else f"{r['stage1_cost']:.2f}"
        trials = "-" if r["trials"] is None else str(r["trials"])
        steps = "-" if r["steps"] is None else str(r["steps"])
        applied = "-" if r["trial_applied"] is None else str(r["trial_applied"])
        rls = "-" if r["route_refine_moves"] is None else str(r["route_refine_moves"])
        reb = "-" if r["rebalance_moves"] is None else str(r["rebalance_moves"])
        swp = "-" if r["rebalance_swaps"] is None else str(r["rebalance_swaps"])
        print(f"{r['model']:>5}  {str(r['success']):>7}  {cost:>8}  "
              f"{stage1:>8}  {r['transshipments']:>5}  "
              f"{trials:>6}  {steps:>6}  {applied:>7}  "
              f"{rls:>3}  {reb:>3}  {swp:>3}  {r['elapsed']:>5.2f}s")
    print(f"Summary saved to: {summary_path}")


if __name__ == "__main__":
    main()
