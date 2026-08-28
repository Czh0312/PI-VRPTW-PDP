# models/routing/variants/model_7/alns_solver.py
# -*- coding: utf-8 -*-
"""
Model 7 (每轮中转试探 ALNS) 的 ALNS 求解器。

Model 7 沿用 Model 5/6 的中转定义，但中转触发时机不同：
每次 ALNS 生成 candidate 并完成 destroy/repair 后，都在 candidate
上进行一次受控中转试探。若 candidate 未被 ALNS 接受，本轮中转也随
candidate 一起丢弃。
"""
import random
import time

from models.routing.alns.types import clean_empty_routes, FEAS_TOL
from models.routing.alns.engine import ALNSConfig, ALNSHooks, run_alns_search
from models.routing.alns.initial_solution import build_strong_initial_solution
from models.routing.alns.variants.model_5 import (
    Evaluator,
    build_initial_solution,
    random_removal,
    worst_removal,
    shaw_removal,
    greedy_insertion,
    regret_insertion,
    optimize_transshipment,
    synchronize_transshipments,
    _path_with_relocations,
    _print_solution,
)


def solve_alns(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
               max_iterations=5000, max_time=120, seed=42, verbose=True,
               transshipment_candidate_mode="all_sampled",
               max_relocation_candidates=None,
               transshipment_time_budget=0.05,
               transshipment_sample_pickups=5,
               transshipment_sample_locations=3,
               transshipment_sample_carriers=3,
               transshipment_position_top_k=3,
               transshipment_interval=1,
               transshipment_min_net_saving=0.01,
               transshipment_max_total=None,
               transshipment_max_per_satellite=None,
               transshipment_risk_aware=False,
               transshipment_slack_weight=0.0,
               transshipment_capacity_weight=0.0,
               transshipment_min_transfer_slack=None,
               transshipment_min_return_slack=None,
               transshipment_min_capacity_slack=None,
               transshipment_probe_depth=1,
               transshipment_probe_schedule="fixed",
               transshipment_probe_selection="last",
               transshipment_probe_strategy="greedy",
               transshipment_probe_pool_size=4,
               transshipment_probe_alpha=0.8,
               transshipment_probe_beam_width=3,
               transshipment_conservative_min_saving=1.0,
               transshipment_probe_quality_gap=None,
               transshipment_stagnation_boost=False,
               transshipment_stagnation_window=30,
               transshipment_stagnation_depth=None,
               transshipment_stagnation_time_factor=2.0,
               transshipment_stagnation_candidate_mode=None,
               transshipment_stagnation_max_relocation_candidates=None,
               transshipment_stagnation_position_top_k=None,
               transshipment_prune=False,
               transshipment_prune_min_improvement=0.01,
               transshipment_prune_max_rounds=1,
               transshipment_final_refine=False,
               transshipment_final_refine_depth=10,
               transshipment_baseline_guard=False,
               transshipment_baseline_iterations=None,
               transshipment_baseline_time=None,
               route_local_refine=False,
               route_local_refine_rounds=2,
               route_local_refine_max_moves=20,
               candidate_route_local_refine=False,
               candidate_route_local_refine_rounds=1,
               candidate_route_local_refine_max_moves=2,
               inter_route_rebalance=False,
               inter_route_rebalance_rounds=1,
               inter_route_rebalance_max_moves=10,
               inter_route_rebalance_max_pairs=20,
               inter_route_rebalance_position_top_k=4,
               inter_route_pair_swap=False,
               inter_route_pair_swap_max_pairs=12,
               iterative_rebalance=False,
               iterative_rebalance_trigger="fixed",
               iterative_rebalance_interval=20,
               iterative_rebalance_stagnation_window=20,
               iterative_rebalance_rounds=1,
               iterative_rebalance_max_moves=2,
               iterative_rebalance_max_pairs=12,
               iterative_rebalance_position_top_k=3,
               iterative_rebalance_pair_swap=False,
               iterative_rebalance_pair_swap_max_pairs=8,
               strong_initial_solution=True):
    """
    Model 7: 每轮 ALNS candidate 中转试探。

    与 Model 5 的关键差异：
    - 不在初始解上预先执行中转优化；
    - 不把 transshipment_destroy 放入普通 destroy 算子；
    - 每个可评估 candidate 都执行中转试探，默认 interval=1；
    - 每轮最多连续应用 transshipment_probe_depth 次中转；
    - 中转修改只存在于 candidate 中，是否进入 current_solution 由
      ALNS 接受准则统一决定。
    """
    random.seed(seed)
    start_time = time.time()

    evaluator = Evaluator(problem, e1_tasks, e2_tasks_by_sat,
                          vehicles_per_satellite=vehicles_per_satellite)

    if strong_initial_solution:
        sol = build_strong_initial_solution(
            problem, e1_tasks, e2_tasks_by_sat, evaluator)
    else:
        sol = build_initial_solution(
            problem, e1_tasks, e2_tasks_by_sat, evaluator)
    if not evaluator.is_feasible(sol):
        if verbose:
            print("\n[WARN] Model 7 ALNS 初始解不满足全局时间/容量约束")
        return None, None

    sol.cost = evaluator.total_cost(sol)
    sol.task_to_phys = dict(evaluator.task_to_phys)
    baseline_best = sol.copy()
    baseline_cost = sol.cost

    if verbose:
        print(f"\n{'=' * 60}")
        print("ALNS 求解器 (Model 7 每轮中转试探)")
        print(f"{'=' * 60}")
        print(f"初始解目标值: {sol.cost:.2f}")
        print(f"初始解可行性: {evaluator.is_feasible(sol)}")
        print(f"E1 路径数: {len(sol.e1_routes)}")
        e2_count = sum(len(v) for v in sol.e2_routes.values())
        print(f"E2 路径数: {e2_count}")
        print("中转试探规则: "
              f"interval={transshipment_interval}, "
              f"depth={transshipment_probe_depth}, "
              f"schedule={transshipment_probe_schedule}, "
              f"selection={transshipment_probe_selection}, "
              f"strategy={transshipment_probe_strategy}, "
              f"time_budget={transshipment_time_budget}s, "
              f"min_net={transshipment_min_net_saving}, "
              f"risk_aware={transshipment_risk_aware}, "
              f"quality_gap={transshipment_probe_quality_gap}, "
              f"stagnation_boost={transshipment_stagnation_boost}, "
              f"prune={transshipment_prune}, "
              f"final_refine={transshipment_final_refine}, "
              f"baseline_guard={transshipment_baseline_guard}, "
              f"route_local_refine={route_local_refine}, "
              f"candidate_route_local_refine={candidate_route_local_refine}, "
              f"inter_route_rebalance={inter_route_rebalance}, "
              f"inter_route_pair_swap={inter_route_pair_swap}, "
              f"iterative_rebalance={iterative_rebalance}, "
              f"strong_initial={strong_initial_solution}, "
              f"max_total={transshipment_max_total}, "
              f"max_per_sat={transshipment_max_per_satellite}")

    if transshipment_baseline_guard:
        guard_iterations = (
            max_iterations if transshipment_baseline_iterations is None
            else transshipment_baseline_iterations
        )
        guard_time = (
            max_time if transshipment_baseline_time is None
            else transshipment_baseline_time
        )
        if verbose:
            print("\n--- Model 7 基线保护：先运行无中转 ALNS ---")
        baseline_result = run_alns_search(
            initial_solution=sol,
            evaluator=evaluator,
            destroy_ops=[random_removal, worst_removal, shaw_removal],
            repair_ops=[greedy_insertion, regret_insertion],
            config=ALNSConfig(
                max_iterations=guard_iterations,
                max_time=guard_time,
            ),
            verbose=verbose,
            start_time=time.time(),
        )
        baseline_best = baseline_result.best_solution.copy()
        evaluator.task_to_phys = dict(baseline_best.task_to_phys)
        synchronize_transshipments(baseline_best, evaluator)
        clean_empty_routes(baseline_best)
        synchronize_transshipments(baseline_best, evaluator)
        baseline_best.cost = evaluator.total_cost(baseline_best)
        baseline_cost = baseline_best.cost
        sol = baseline_best.copy()
        evaluator.task_to_phys = dict(sol.task_to_phys)
        if verbose:
            print(f"  基线保护解: cost={baseline_cost:.2f}")
            print("\n--- Model 7 中转嵌入搜索 ---")

    stats = {
        "trials": 0,
        "steps": 0,
        "applied": 0,
        "boosted": 0,
        "pruned": 0,
        "candidate_route_refine_moves": 0,
        "route_refine_moves": 0,
        "rebalance_moves": 0,
        "rebalance_swaps": 0,
        "iter_rebalance_calls": 0,
        "iter_rebalance_moves": 0,
        "iter_rebalance_swaps": 0,
        "postprocess_reverted": 0,
    }

    class Model7Hooks(ALNSHooks):
        def __init__(self):
            self.best_seen = sol.cost
            self.last_improvement_iteration = 0
            self.iteration_context = 0
            self.last_iter_rebalance_iteration = -10**9

        def before_candidate(self, candidate, evaluator, current_solution,
                             iteration):
            evaluator.task_to_phys = dict(candidate.task_to_phys)

        def after_operators(self, candidate, evaluator, current_solution,
                            iteration, d_idx, r_idx):
            synchronize_transshipments(candidate, evaluator)
            clean_empty_routes(candidate)
            synchronize_transshipments(candidate, evaluator)
            candidate.task_to_phys = dict(evaluator.task_to_phys)

        def after_cost(self, candidate, evaluator, current_solution, iteration):
            self.iteration_context = iteration
            if current_solution.cost < self.best_seen - FEAS_TOL:
                self.best_seen = current_solution.cost
                self.last_improvement_iteration = iteration

            interval_due = (
                transshipment_interval
                and iteration % transshipment_interval == 0
            )
            if not interval_due:
                return

            stats["trials"] += 1
            if (transshipment_probe_quality_gap is not None
                    and candidate.cost > current_solution.cost * (
                        1.0 + transshipment_probe_quality_gap) + FEAS_TOL):
                return

            depth = _effective_probe_depth(
                transshipment_probe_depth,
                transshipment_probe_schedule,
                iteration,
                max_iterations,
            )
            if depth <= 0:
                return

            boosted = (
                transshipment_stagnation_boost
                and iteration - self.last_improvement_iteration
                >= max(1, int(transshipment_stagnation_window or 1))
            )
            probe_depth = depth
            probe_time_budget = transshipment_time_budget
            probe_candidate_mode = transshipment_candidate_mode
            probe_max_relocation_candidates = max_relocation_candidates
            probe_position_top_k = transshipment_position_top_k
            if boosted:
                stats["boosted"] += 1
                probe_depth = max(
                    probe_depth,
                    int(transshipment_stagnation_depth or (depth + 1)),
                )
                probe_time_budget *= max(
                    1.0, float(transshipment_stagnation_time_factor or 1.0))
                if transshipment_stagnation_candidate_mode:
                    probe_candidate_mode = transshipment_stagnation_candidate_mode
                if transshipment_stagnation_max_relocation_candidates is not None:
                    probe_max_relocation_candidates = (
                        transshipment_stagnation_max_relocation_candidates)
                if transshipment_stagnation_position_top_k is not None:
                    probe_position_top_k = (
                        transshipment_stagnation_position_top_k)

            if transshipment_prune:
                stats["pruned"] += _prune_transshipments(
                    candidate, evaluator,
                    min_improvement=transshipment_prune_min_improvement,
                    max_rounds=transshipment_prune_max_rounds,
                )

            result = _run_transshipment_probe(
                candidate, evaluator, probe_depth,
                candidate_mode=probe_candidate_mode,
                max_relocation_candidates=probe_max_relocation_candidates,
                time_budget=probe_time_budget,
                sample_pickups=transshipment_sample_pickups,
                sample_locations=transshipment_sample_locations,
                sample_carriers=transshipment_sample_carriers,
                position_top_k=probe_position_top_k,
                min_net_saving=transshipment_min_net_saving,
                max_transshipments=transshipment_max_total,
                max_transshipments_per_sat=transshipment_max_per_satellite,
                risk_aware=transshipment_risk_aware,
                slack_weight=transshipment_slack_weight,
                capacity_weight=transshipment_capacity_weight,
                min_transfer_slack=transshipment_min_transfer_slack,
                min_return_slack=transshipment_min_return_slack,
                min_capacity_slack=transshipment_min_capacity_slack,
                selection=transshipment_probe_selection,
                strategy=transshipment_probe_strategy,
                pool_size=transshipment_probe_pool_size,
                alpha=transshipment_probe_alpha,
                beam_width=transshipment_probe_beam_width,
                conservative_min_saving=transshipment_conservative_min_saving,
            )
            stats["steps"] += result["steps"]
            stats["applied"] += result["applied"]

            if candidate_route_local_refine and result["applied"] > 0:
                stats["candidate_route_refine_moves"] += _final_route_local_search(
                    candidate, evaluator,
                    rounds=candidate_route_local_refine_rounds,
                    max_moves=candidate_route_local_refine_max_moves,
                )

            if transshipment_prune:
                stats["pruned"] += _prune_transshipments(
                    candidate, evaluator,
                    min_improvement=transshipment_prune_min_improvement,
                    max_rounds=transshipment_prune_max_rounds,
                )

            if self._iterative_rebalance_due(iteration):
                stats["iter_rebalance_calls"] += 1
                self.last_iter_rebalance_iteration = iteration
                moves, swaps = _final_inter_route_rebalance(
                    candidate, evaluator,
                    rounds=iterative_rebalance_rounds,
                    max_moves=iterative_rebalance_max_moves,
                    max_pairs=iterative_rebalance_max_pairs,
                    position_top_k=iterative_rebalance_position_top_k,
                    enable_swaps=iterative_rebalance_pair_swap,
                    max_swap_pairs=iterative_rebalance_pair_swap_max_pairs,
                )
                stats["iter_rebalance_moves"] += moves
                stats["iter_rebalance_swaps"] += swaps

        def after_infeasible(self, evaluator, current_solution):
            evaluator.task_to_phys = dict(current_solution.task_to_phys)

        def after_accept(self, evaluator, accepted_solution):
            evaluator.task_to_phys = dict(accepted_solution.task_to_phys)
            if accepted_solution.cost < self.best_seen - FEAS_TOL:
                self.best_seen = accepted_solution.cost
                self.last_improvement_iteration = self.iteration_context

        def new_best_extra(self, best_solution):
            return f"  中转: {len(best_solution.transshipments)}"

        def _iterative_rebalance_due(self, iteration):
            if not iterative_rebalance:
                return False

            trigger = (iterative_rebalance_trigger or "fixed").lower()
            interval = max(1, int(iterative_rebalance_interval or 1))
            window = max(1, int(iterative_rebalance_stagnation_window or 1))
            fixed_due = (
                trigger in ("fixed", "both")
                and (iteration + 1) % interval == 0
            )
            stagnation_due = (
                trigger in ("stagnation", "both")
                and iteration - self.last_improvement_iteration >= window
                and iteration - self.last_iter_rebalance_iteration >= interval
            )
            return fixed_due or stagnation_due

        def progress_extra(self, current_solution, best_solution):
            return (f"中转={len(current_solution.transshipments)}  "
                    f"试探={stats['trials']}  "
                    f"步数={stats['steps']}  成功={stats['applied']}  "
                    f"增强={stats['boosted']}  "
                    f"候精={stats['candidate_route_refine_moves']}  "
                    f"撤销={stats['pruned']}  ")

    result = run_alns_search(
        initial_solution=sol,
        evaluator=evaluator,
        destroy_ops=[random_removal, worst_removal, shaw_removal],
        repair_ops=[greedy_insertion, regret_insertion],
        config=ALNSConfig(max_iterations=max_iterations, max_time=max_time),
        hooks=Model7Hooks(),
        verbose=verbose,
        start_time=time.time() if transshipment_baseline_guard else start_time,
    )

    best_sol = result.best_solution
    best_cost = result.best_cost
    elapsed = time.time() - start_time

    evaluator.task_to_phys = dict(best_sol.task_to_phys)
    synchronize_transshipments(best_sol, evaluator)
    clean_empty_routes(best_sol)
    synchronize_transshipments(best_sol, evaluator)
    best_sol.cost = evaluator.total_cost(best_sol)
    postprocess_guard = best_sol.copy()
    postprocess_guard_cost = best_sol.cost

    if transshipment_final_refine:
        refined_steps = _final_refine_transshipments(
            best_sol, evaluator,
            depth=transshipment_final_refine_depth,
            candidate_mode=transshipment_candidate_mode,
            max_relocation_candidates=max_relocation_candidates,
            time_budget=transshipment_time_budget,
            sample_pickups=transshipment_sample_pickups,
            sample_locations=transshipment_sample_locations,
            sample_carriers=transshipment_sample_carriers,
            position_top_k=transshipment_position_top_k,
            min_net_saving=transshipment_min_net_saving,
            max_transshipments=transshipment_max_total,
            max_transshipments_per_sat=transshipment_max_per_satellite,
            risk_aware=transshipment_risk_aware,
            slack_weight=transshipment_slack_weight,
            capacity_weight=transshipment_capacity_weight,
            min_transfer_slack=transshipment_min_transfer_slack,
            min_return_slack=transshipment_min_return_slack,
            min_capacity_slack=transshipment_min_capacity_slack,
        )
        stats["steps"] += refined_steps
        best_sol.cost = evaluator.total_cost(best_sol)

    if route_local_refine:
        stats["route_refine_moves"] += _final_route_local_search(
            best_sol, evaluator,
            rounds=route_local_refine_rounds,
            max_moves=route_local_refine_max_moves,
        )
    if inter_route_rebalance:
        moves, swaps = _final_inter_route_rebalance(
            best_sol, evaluator,
            rounds=inter_route_rebalance_rounds,
            max_moves=inter_route_rebalance_max_moves,
            max_pairs=inter_route_rebalance_max_pairs,
            position_top_k=inter_route_rebalance_position_top_k,
            enable_swaps=inter_route_pair_swap,
            max_swap_pairs=inter_route_pair_swap_max_pairs,
        )
        stats["rebalance_moves"] += moves
        stats["rebalance_swaps"] += swaps
        if route_local_refine and moves + swaps > 0:
            stats["route_refine_moves"] += _final_route_local_search(
                best_sol, evaluator,
                rounds=route_local_refine_rounds,
                max_moves=route_local_refine_max_moves,
            )
    best_cost = best_sol.cost

    if best_cost > postprocess_guard_cost + FEAS_TOL:
        best_sol = postprocess_guard.copy()
        best_cost = postprocess_guard_cost
        stats["postprocess_reverted"] += 1
        evaluator.task_to_phys = dict(best_sol.task_to_phys)
        synchronize_transshipments(best_sol, evaluator)

    guard_best = baseline_best.copy()
    guard_cost = baseline_cost
    guard_route_moves = 0
    guard_rebalance_moves = 0
    guard_rebalance_swaps = 0
    if transshipment_baseline_guard and route_local_refine:
        guard_route_moves = _final_route_local_search(
            guard_best, evaluator,
            rounds=route_local_refine_rounds,
            max_moves=route_local_refine_max_moves,
        )
        guard_cost = guard_best.cost
    if transshipment_baseline_guard and inter_route_rebalance:
        guard_rebalance_moves, guard_rebalance_swaps = _final_inter_route_rebalance(
            guard_best, evaluator,
            rounds=inter_route_rebalance_rounds,
            max_moves=inter_route_rebalance_max_moves,
            max_pairs=inter_route_rebalance_max_pairs,
            position_top_k=inter_route_rebalance_position_top_k,
            enable_swaps=inter_route_pair_swap,
            max_swap_pairs=inter_route_pair_swap_max_pairs,
        )
        guard_cost = guard_best.cost
        if route_local_refine and guard_rebalance_moves + guard_rebalance_swaps > 0:
            guard_route_moves += _final_route_local_search(
                guard_best, evaluator,
                rounds=route_local_refine_rounds,
                max_moves=route_local_refine_max_moves,
            )
            guard_cost = guard_best.cost

    if transshipment_baseline_guard and guard_cost < best_cost - FEAS_TOL:
        best_sol = guard_best.copy()
        best_cost = guard_cost
        stats["route_refine_moves"] += guard_route_moves
        stats["rebalance_moves"] += guard_rebalance_moves
        stats["rebalance_swaps"] += guard_rebalance_swaps
        evaluator.task_to_phys = dict(best_sol.task_to_phys)
        synchronize_transshipments(best_sol, evaluator)

    if verbose:
        print(f"\n{'=' * 60}")
        print("Model 7 求解完成")
        print(f"最优目标值: {best_cost:.2f}")
        if transshipment_baseline_guard:
            print(f"基线保护目标值: {baseline_cost:.2f}")
        print(f"中转试探次数: {stats['trials']}")
        print(f"中转试探步数: {stats['steps']}")
        print(f"中转成功加入候选次数: {stats['applied']}")
        print(f"停滞增强次数: {stats['boosted']}")
        print(f"中转撤销次数: {stats['pruned']}")
        print(f"候选路线精修移动次数: {stats['candidate_route_refine_moves']}")
        print(f"路线精修移动次数: {stats['route_refine_moves']}")
        print(f"跨车重排移动次数: {stats['rebalance_moves']}")
        print(f"跨车交换次数: {stats['rebalance_swaps']}")
        print(f"迭代跨车重排触发次数: {stats['iter_rebalance_calls']}")
        print(f"迭代跨车重排移动次数: {stats['iter_rebalance_moves']}")
        print(f"迭代跨车交换次数: {stats['iter_rebalance_swaps']}")
        print(f"后处理回退次数: {stats['postprocess_reverted']}")
        print(f"最优解中转操作数: {len(best_sol.transshipments)}")
        print(f"总耗时: {elapsed:.2f} 秒")
        print(f"{'=' * 60}")

    e1_routes_out = [
        _path_with_relocations(r, best_sol, evaluator)
        for r in best_sol.e1_routes
    ]
    e2_routes_out = {}
    for sat_id, routes in best_sol.e2_routes.items():
        e2_routes_out[sat_id] = [
            _path_with_relocations(r, best_sol, evaluator)
            for r in routes
        ]

    if verbose:
        _print_solution(best_sol, evaluator)
    return e1_routes_out, e2_routes_out


def _effective_probe_depth(max_depth, schedule, iteration, max_iterations):
    max_depth = max(0, int(max_depth or 0))
    if max_depth <= 1:
        return max_depth

    schedule = (schedule or "fixed").lower()
    if schedule == "fixed":
        return max_depth
    if schedule == "decreasing":
        progress = (iteration + 1) / max(max_iterations, 1)
        if progress < 1.0 / 3.0:
            return max_depth
        if progress < 2.0 / 3.0:
            return max(1, max_depth - 1)
        return 1
    raise ValueError("transshipment_probe_schedule must be fixed or decreasing")


def _run_transshipment_probe(candidate, evaluator, depth, selection="last",
                             strategy="greedy", pool_size=4, alpha=0.8,
                             beam_width=3, conservative_min_saving=1.0,
                             **kwargs):
    strategy = (strategy or "greedy").lower()
    if strategy == "conservative":
        kwargs = dict(kwargs)
        kwargs["min_net_saving"] = max(
            kwargs.get("min_net_saving", 0.01),
            conservative_min_saving,
        )
        strategy = "greedy"
    if strategy == "greedy":
        return _run_greedy_transshipment_probe(
            candidate, evaluator, depth, selection=selection, **kwargs)
    if strategy == "range_random":
        return _run_range_random_probe(
            candidate, evaluator, depth, pool_size=pool_size,
            alpha=alpha, **kwargs)
    if strategy == "beam":
        return _run_beam_probe(
            candidate, evaluator, depth, pool_size=pool_size,
            beam_width=beam_width, **kwargs)
    raise ValueError(
        "transshipment_probe_strategy must be one of: "
        "greedy, range_random, conservative, beam"
    )


def _run_greedy_transshipment_probe(candidate, evaluator, depth,
                                    selection="last", **kwargs):
    selection = (selection or "last").lower()
    if selection not in ("last", "best_prefix"):
        raise ValueError(
            "transshipment_probe_selection must be last or best_prefix")

    evaluator.task_to_phys = dict(candidate.task_to_phys)
    candidate.cost = evaluator.total_cost(candidate)
    best = candidate.copy()
    working = candidate.copy()
    steps = 0
    applied = 0

    for _ in range(depth):
        evaluator.task_to_phys = dict(working.task_to_phys)
        before_count = len(working.transshipments)
        steps += 1
        improved = optimize_transshipment(working, evaluator, **kwargs)
        synchronize_transshipments(working, evaluator)
        working.task_to_phys = dict(evaluator.task_to_phys)
        working.cost = evaluator.total_cost(working)

        if not improved:
            break

        if len(working.transshipments) > before_count:
            applied += 1

        if working.cost < best.cost - FEAS_TOL:
            best = working.copy()

    chosen = best if selection == "best_prefix" else working
    _replace_solution(candidate, chosen)
    evaluator.task_to_phys = dict(candidate.task_to_phys)
    candidate.cost = evaluator.total_cost(candidate)
    return {"steps": steps, "applied": applied}


def _run_range_random_probe(candidate, evaluator, depth, pool_size=4,
                            alpha=0.8, **kwargs):
    evaluator.task_to_phys = dict(candidate.task_to_phys)
    candidate.cost = evaluator.total_cost(candidate)
    working = candidate.copy()
    steps = 0
    applied = 0

    for _ in range(depth):
        steps += 1
        options = _generate_transshipment_options(
            working, evaluator, pool_size, **kwargs)
        if not options:
            break

        best_gain = max(gain for gain, _ in options)
        threshold = max(kwargs.get("min_net_saving", 0.01), alpha * best_gain)
        eligible = [(gain, sol) for gain, sol in options
                    if gain >= threshold - FEAS_TOL]
        if not eligible:
            break

        weights = [max(gain, FEAS_TOL) for gain, _ in eligible]
        chosen = _weighted_choice(eligible, weights)[1]
        if len(chosen.transshipments) > len(working.transshipments):
            applied += 1
        working = chosen.copy()

    _replace_solution(candidate, working)
    evaluator.task_to_phys = dict(candidate.task_to_phys)
    candidate.cost = evaluator.total_cost(candidate)
    return {"steps": steps, "applied": applied}


def _run_beam_probe(candidate, evaluator, depth, pool_size=4, beam_width=3,
                    **kwargs):
    evaluator.task_to_phys = dict(candidate.task_to_phys)
    candidate.cost = evaluator.total_cost(candidate)
    beam = [candidate.copy()]
    steps = 0
    applied = 0

    for _ in range(depth):
        steps += 1
        expanded = []
        for sol in beam:
            expanded.extend(_generate_transshipment_options(
                sol, evaluator, pool_size, **kwargs))
        if not expanded:
            break
        expanded.sort(key=lambda item: item[1].cost)
        next_beam = []
        seen = set()
        for _, sol in expanded:
            signature = _solution_signature(sol)
            if signature in seen:
                continue
            seen.add(signature)
            next_beam.append(sol.copy())
            if len(next_beam) >= max(1, beam_width):
                break
        if not next_beam:
            break
        applied += 1
        beam = next_beam

    best = min(beam, key=lambda sol: sol.cost)
    _replace_solution(candidate, best)
    evaluator.task_to_phys = dict(candidate.task_to_phys)
    candidate.cost = evaluator.total_cost(candidate)
    return {"steps": steps, "applied": applied}


def _generate_transshipment_options(base, evaluator, pool_size, **kwargs):
    options = []
    base_cost = evaluator.total_cost(base)
    attempts = max(1, int(pool_size or 1))

    for _ in range(attempts):
        trial = base.copy()
        evaluator.task_to_phys = dict(trial.task_to_phys)
        improved = optimize_transshipment(trial, evaluator, **kwargs)
        synchronize_transshipments(trial, evaluator)
        trial.task_to_phys = dict(evaluator.task_to_phys)
        trial.cost = evaluator.total_cost(trial)
        gain = base_cost - trial.cost
        if improved and gain >= kwargs.get("min_net_saving", 0.01) - FEAS_TOL:
            options.append((gain, trial.copy()))

    return options


def _weighted_choice(items, weights):
    total = sum(weights)
    if total <= FEAS_TOL:
        return random.choice(items)
    marker = random.random() * total
    acc = 0.0
    for item, weight in zip(items, weights):
        acc += weight
        if acc >= marker:
            return item
    return items[-1]


def _solution_signature(sol):
    trans_keys = tuple(sorted(sol.transshipments.keys()))
    e2_paths = tuple(
        (sat_id, tuple(tuple(route.nodes) for route in routes))
        for sat_id, routes in sorted(sol.e2_routes.items())
    )
    return trans_keys, e2_paths


def _prune_transshipments(sol, evaluator, min_improvement=0.01,
                          max_rounds=1):
    if not sol.transshipments:
        evaluator.task_to_phys = dict(sol.task_to_phys)
        sol.cost = evaluator.total_cost(sol)
        return 0

    removed = 0
    rounds = max(1, int(max_rounds or 1))

    for _ in range(rounds):
        evaluator.task_to_phys = dict(sol.task_to_phys)
        sol.cost = evaluator.total_cost(sol)
        base_cost = sol.cost
        best_trial = None
        best_gain = 0.0

        for key in list(sol.transshipments.keys()):
            trial = sol.copy()
            evaluator.task_to_phys = dict(trial.task_to_phys)
            if not _undo_transshipment(trial, evaluator, key):
                continue
            synchronize_transshipments(trial, evaluator)
            if not evaluator.is_feasible(trial):
                continue
            trial.cost = evaluator.total_cost(trial)
            gain = base_cost - trial.cost
            if gain > best_gain + FEAS_TOL:
                best_gain = gain
                best_trial = trial

        if best_trial is None or best_gain < min_improvement - FEAS_TOL:
            break

        _replace_solution(sol, best_trial)
        evaluator.task_to_phys = dict(sol.task_to_phys)
        removed += 1

    sol.cost = evaluator.total_cost(sol)
    return removed


def _undo_transshipment(sol, evaluator, key):
    if key not in sol.transshipments:
        return False

    sat_id, _ben_idx, pick_node, new_phys = key
    old_phys, _carrier_idx, _cw, _cv = sol.transshipments[key]

    evaluator.task_to_phys[pick_node] = old_phys
    sol.task_to_phys[pick_node] = old_phys

    pick_vnode = f"TRANS_PICK_{pick_node}@{old_phys}"
    drop_vnode = f"TRANS_DROP_{pick_node}@{new_phys}"
    for route in sol.e2_routes.get(sat_id, []):
        route.nodes = [
            nid for nid in route.nodes
            if nid not in (pick_vnode, drop_vnode)
        ]
        route.transport_loads.pop(pick_vnode, None)
        route.transport_loads.pop(drop_vnode, None)

    del sol.transshipments[key]
    sol.task_to_phys = dict(evaluator.task_to_phys)
    return True


def _final_refine_transshipments(sol, evaluator, depth=10, **kwargs):
    steps = 0
    for _ in range(max(0, int(depth or 0))):
        evaluator.task_to_phys = dict(sol.task_to_phys)
        improved = optimize_transshipment(sol, evaluator, **kwargs)
        synchronize_transshipments(sol, evaluator)
        sol.task_to_phys = dict(evaluator.task_to_phys)
        sol.cost = evaluator.total_cost(sol)
        steps += 1
        if not improved:
            break
    return steps


def _final_route_local_search(sol, evaluator, rounds=2, max_moves=20):
    """Final intra-route relocate refinement guarded by full feasibility."""
    moves = 0
    rounds = max(0, int(rounds or 0))
    max_moves = None if max_moves is None else max(0, int(max_moves))

    for _ in range(rounds):
        if max_moves is not None and moves >= max_moves:
            break

        evaluator.task_to_phys = dict(sol.task_to_phys)
        synchronize_transshipments(sol, evaluator)
        sol.cost = evaluator.total_cost(sol)
        best_trial = None
        best_gain = 0.0
        base_cost = sol.cost

        for route_ref in _iter_route_refs(sol):
            route = _route_by_ref(sol, route_ref)
            n_nodes = len(route.nodes)
            if n_nodes < 2:
                continue

            for source_idx, node_id in enumerate(route.nodes):
                if _is_virtual_transport_node(node_id):
                    continue
                for insert_idx in range(n_nodes + 1):
                    target_idx = insert_idx - 1 if insert_idx > source_idx else insert_idx
                    if target_idx == source_idx:
                        continue

                    trial = sol.copy()
                    trial_route = _route_by_ref(trial, route_ref)
                    moved = trial_route.nodes.pop(source_idx)
                    trial_route.nodes.insert(target_idx, moved)
                    evaluator.task_to_phys = dict(trial.task_to_phys)
                    synchronize_transshipments(trial, evaluator)

                    if not evaluator.is_feasible(trial):
                        continue

                    trial.task_to_phys = dict(evaluator.task_to_phys)
                    trial.cost = evaluator.total_cost(trial)
                    gain = base_cost - trial.cost
                    if gain > best_gain + FEAS_TOL:
                        best_gain = gain
                        best_trial = trial

        if best_trial is None:
            break

        _replace_solution(sol, best_trial)
        moves += 1

    evaluator.task_to_phys = dict(sol.task_to_phys)
    synchronize_transshipments(sol, evaluator)
    sol.cost = evaluator.total_cost(sol)
    return moves


def _final_inter_route_rebalance(sol, evaluator, rounds=1, max_moves=10,
                                 max_pairs=20, position_top_k=4,
                                 enable_swaps=False,
                                 max_swap_pairs=12):
    """Final same-satellite customer-pair relocate/swap across E2 routes."""
    moves = 0
    swaps = 0
    rounds = max(0, int(rounds or 0))
    max_moves = None if max_moves is None else max(0, int(max_moves))

    for _ in range(rounds):
        if max_moves is not None and moves + swaps >= max_moves:
            break

        evaluator.task_to_phys = dict(sol.task_to_phys)
        synchronize_transshipments(sol, evaluator)
        clean_empty_routes(sol)
        synchronize_transshipments(sol, evaluator)
        sol.cost = evaluator.total_cost(sol)
        base_cost = sol.cost
        best_trial = None
        best_gain = 0.0
        best_kind = None

        for sat_id in sorted(sol.e2_routes):
            routes = sol.e2_routes.get(sat_id, [])
            if len(routes) < 2:
                continue

            pair_refs = _e2_customer_pair_refs(
                sol, evaluator, sat_id, max_pairs=max_pairs)
            for source_idx, d_id, p_id in pair_refs:
                for target_idx in range(len(routes)):
                    if target_idx == source_idx:
                        continue

                    distance_gain_bound = _pair_relocate_distance_gain_bound(
                        routes[source_idx], routes[target_idx],
                        d_id, p_id, evaluator, position_top_k)
                    if (distance_gain_bound is not None
                            and distance_gain_bound <= FEAS_TOL):
                        continue

                    trial = _best_pair_relocate_trial(
                        sol, evaluator, sat_id, source_idx, target_idx,
                        d_id, p_id, position_top_k)
                    if trial is None:
                        continue

                    gain = base_cost - trial.cost
                    if gain > best_gain + FEAS_TOL:
                        best_gain = gain
                        best_trial = trial
                        best_kind = "move"

            if not enable_swaps:
                continue

            swap_refs = pair_refs
            if max_swap_pairs is not None:
                swap_refs = swap_refs[:max(0, int(max_swap_pairs))]
            for a_pos, (route_a_idx, a_d, a_p) in enumerate(swap_refs):
                for route_b_idx, b_d, b_p in swap_refs[a_pos + 1:]:
                    if route_a_idx == route_b_idx:
                        continue

                    trial = _best_pair_swap_trial(
                        sol, evaluator, sat_id,
                        route_a_idx, route_b_idx,
                        a_d, a_p, b_d, b_p,
                        position_top_k)
                    if trial is None:
                        continue

                    gain = base_cost - trial.cost
                    if gain > best_gain + FEAS_TOL:
                        best_gain = gain
                        best_trial = trial
                        best_kind = "swap"

        if best_trial is None:
            break

        _replace_solution(sol, best_trial)
        if best_kind == "swap":
            swaps += 1
        else:
            moves += 1

    evaluator.task_to_phys = dict(sol.task_to_phys)
    synchronize_transshipments(sol, evaluator)
    clean_empty_routes(sol)
    synchronize_transshipments(sol, evaluator)
    sol.cost = evaluator.total_cost(sol)
    return moves, swaps


def _e2_customer_pair_refs(sol, evaluator, sat_id, max_pairs=None):
    routes = sol.e2_routes.get(sat_id, [])
    refs = []
    for task in evaluator.e2_tasks_by_sat.get(sat_id, []):
        if task.kind != "d":
            continue
        d_id = task.id
        p_id = f"{task.cust}p"
        if p_id not in evaluator.e2_task_map:
            continue
        for route_idx, route in enumerate(routes):
            if d_id in route.nodes and p_id in route.nodes:
                refs.append((route_idx, d_id, p_id))
                break

    refs.sort(key=lambda item: _pair_remove_saving(
        routes[item[0]], item[1], item[2], evaluator), reverse=True)
    if max_pairs is not None:
        refs = refs[:max(0, int(max_pairs))]
    return refs


def _pair_remove_saving(route, d_id, p_id, evaluator):
    old_dist = evaluator.route_distance(route)
    trial_route = route.copy()
    for node_id in (d_id, p_id):
        if node_id in trial_route.nodes:
            trial_route.nodes.remove(node_id)
    return old_dist - evaluator.route_distance(trial_route)


def _pair_relocate_distance_gain_bound(source_route, target_route,
                                       d_id, p_id, evaluator, position_top_k):
    source_saving = _pair_remove_saving(source_route, d_id, p_id, evaluator)
    if source_saving <= FEAS_TOL:
        return source_saving

    target_variants = _pair_insert_variants(
        target_route, d_id, p_id, evaluator, top_k=position_top_k)
    if not target_variants:
        return None

    target_delta = (
        target_variants[0][0] - evaluator.route_distance(target_route)
    )
    return source_saving - target_delta


def _best_pair_relocate_trial(sol, evaluator, sat_id, source_idx, target_idx,
                              d_id, p_id, position_top_k):
    base = sol.copy()
    routes = base.e2_routes.get(sat_id, [])
    if source_idx >= len(routes) or target_idx >= len(routes):
        return None

    source_route = routes[source_idx]
    if d_id not in source_route.nodes or p_id not in source_route.nodes:
        return None
    source_route.nodes.remove(d_id)
    source_route.nodes.remove(p_id)

    target_route = routes[target_idx]
    d_positions = _best_insert_positions(
        target_route.nodes, target_route.base, d_id, evaluator,
        top_k=position_top_k)

    best_trial = None
    best_cost = float("inf")
    for d_pos in d_positions:
        with_d = base.copy()
        with_d_route = with_d.e2_routes[sat_id][target_idx]
        with_d_route.nodes.insert(d_pos, d_id)
        p_positions = _best_insert_positions(
            with_d_route.nodes, with_d_route.base, p_id, evaluator,
            top_k=position_top_k, start_after=d_pos)

        for p_pos in p_positions:
            trial = with_d.copy()
            trial_route = trial.e2_routes[sat_id][target_idx]
            trial_route.nodes.insert(p_pos, p_id)
            evaluator.task_to_phys = dict(trial.task_to_phys)
            synchronize_transshipments(trial, evaluator)
            clean_empty_routes(trial)
            synchronize_transshipments(trial, evaluator)

            if not evaluator.is_feasible(trial):
                continue

            trial.task_to_phys = dict(evaluator.task_to_phys)
            trial.cost = evaluator.total_cost(trial)
            if trial.cost < best_cost - FEAS_TOL:
                best_cost = trial.cost
                best_trial = trial

    return best_trial


def _best_pair_swap_trial(sol, evaluator, sat_id, route_a_idx, route_b_idx,
                          a_d, a_p, b_d, b_p, position_top_k):
    base = sol.copy()
    routes = base.e2_routes.get(sat_id, [])
    if route_a_idx >= len(routes) or route_b_idx >= len(routes):
        return None
    if route_a_idx == route_b_idx:
        return None

    route_a = routes[route_a_idx]
    route_b = routes[route_b_idx]
    if a_d not in route_a.nodes or a_p not in route_a.nodes:
        return None
    if b_d not in route_b.nodes or b_p not in route_b.nodes:
        return None

    for node_id in (a_d, a_p):
        route_a.nodes.remove(node_id)
    for node_id in (b_d, b_p):
        route_b.nodes.remove(node_id)

    evaluator.task_to_phys = dict(base.task_to_phys)
    a_to_b_variants = _pair_insert_variants(
        route_b, a_d, a_p, evaluator, top_k=position_top_k)
    b_to_a_variants = _pair_insert_variants(
        route_a, b_d, b_p, evaluator, top_k=position_top_k)
    if not a_to_b_variants or not b_to_a_variants:
        return None

    best_trial = None
    best_cost = float("inf")
    for _dist_b, route_b_nodes in a_to_b_variants:
        for _dist_a, route_a_nodes in b_to_a_variants:
            trial = base.copy()
            trial.e2_routes[sat_id][route_a_idx].nodes = list(route_a_nodes)
            trial.e2_routes[sat_id][route_b_idx].nodes = list(route_b_nodes)
            evaluator.task_to_phys = dict(trial.task_to_phys)
            synchronize_transshipments(trial, evaluator)
            clean_empty_routes(trial)
            synchronize_transshipments(trial, evaluator)

            if not evaluator.is_feasible(trial):
                continue

            trial.task_to_phys = dict(evaluator.task_to_phys)
            trial.cost = evaluator.total_cost(trial)
            if trial.cost < best_cost - FEAS_TOL:
                best_cost = trial.cost
                best_trial = trial

    return best_trial


def _pair_insert_variants(route, d_id, p_id, evaluator, top_k=4):
    d_positions = _best_insert_positions(
        route.nodes, route.base, d_id, evaluator, top_k=top_k)

    variants = []
    seen = set()
    for d_pos in d_positions:
        with_d = list(route.nodes)
        with_d.insert(d_pos, d_id)
        p_positions = _best_insert_positions(
            with_d, route.base, p_id, evaluator,
            top_k=top_k, start_after=d_pos)

        for p_pos in p_positions:
            nodes = list(with_d)
            nodes.insert(p_pos, p_id)
            signature = tuple(nodes)
            if signature in seen:
                continue
            seen.add(signature)
            trial_route = route.copy()
            trial_route.nodes = nodes
            variants.append((evaluator.route_distance(trial_route), nodes))

    variants.sort(key=lambda item: item[0])
    if top_k is None:
        return variants
    return variants[:max(1, int(top_k))]


def _best_insert_positions(nodes, base, node_id, evaluator, top_k=4,
                           start_after=-1):
    node_phys = evaluator.phys(node_id)
    path = [base] + list(nodes) + [base]
    positions = []
    for pos in range(start_after + 1, len(nodes) + 1):
        prev_phys = evaluator.phys(path[pos])
        next_phys = evaluator.phys(path[pos + 1])
        detour = (
            evaluator.problem.distance(prev_phys, node_phys)
            + evaluator.problem.distance(node_phys, next_phys)
            - evaluator.problem.distance(prev_phys, next_phys)
        )
        positions.append((detour, pos))

    positions.sort(key=lambda item: item[0])
    if top_k is None:
        return [pos for _detour, pos in positions]
    return [pos for _detour, pos in positions[:max(1, int(top_k))]]


def _iter_route_refs(sol):
    for route_idx in range(len(sol.e1_routes)):
        yield ("e1", route_idx)
    for sat_id in sorted(sol.e2_routes):
        for route_idx in range(len(sol.e2_routes[sat_id])):
            yield ("e2", sat_id, route_idx)


def _route_by_ref(sol, route_ref):
    if route_ref[0] == "e1":
        return sol.e1_routes[route_ref[1]]
    return sol.e2_routes[route_ref[1]][route_ref[2]]


def _is_virtual_transport_node(node_id):
    return (
        isinstance(node_id, str)
        and (node_id.startswith("TRANS_PICK_")
             or node_id.startswith("TRANS_DROP_"))
    )


def _replace_solution(target, source):
    target.e1_routes = [r.copy() for r in source.e1_routes]
    target.e2_routes = {
        sat_id: [r.copy() for r in routes]
        for sat_id, routes in source.e2_routes.items()
    }
    target.cost = source.cost
    target.task_to_phys = dict(source.task_to_phys)
    target.transshipments = dict(source.transshipments)
