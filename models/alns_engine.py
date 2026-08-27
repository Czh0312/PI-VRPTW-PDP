# models/alns_engine.py
# -*- coding: utf-8 -*-
"""
通用 ALNS 搜索引擎。

该模块只负责 ALNS 的通用控制流：算子选择、破坏/修复、可行性检查、
模拟退火接受、算子权重更新和进度日志。具体模型的 evaluator、算子、
中转优化和输出格式由调用方注入。
"""
from dataclasses import dataclass
import math
import random
import time

from models.alns_base import _roulette, clean_empty_routes, FEAS_TOL


@dataclass
class ALNSConfig:
    max_iterations: int = 5000
    max_time: float = 120
    segment_size: int = 100
    sigma1: float = 33
    sigma2: float = 9
    sigma3: float = 3
    decay: float = 0.8
    e2_probability: float = 0.8
    max_remove_cap: int = 20
    temperature_end: float = 0.01
    target_cost: float | None = None
    target_tolerance: float = 1e-6
    cooling_schedule: str = "time"
    stagnation_time: float | None = None
    reheat_fraction: float = 0.5
    max_restarts: int = 0
    intensification_time: float = 0.0


@dataclass
class ALNSResult:
    current_solution: object
    best_solution: object
    best_cost: float
    iterations: int
    elapsed: float
    best_found_time: float | None
    target_hit_time: float | None
    termination_reason: str
    restarts: int
    local_search_improvements: int


class ALNSHooks:
    """模型扩展点。默认实现对应标准 destroy/repair ALNS。"""

    check_feasibility_after_cost = False

    def before_candidate(self, candidate, evaluator, current_solution, iteration):
        pass

    def apply_operators(self, candidate, evaluator, destroy_op, repair_op,
                        d_idx, r_idx, num_remove, echelon):
        removed = destroy_op(candidate, evaluator, num_remove, echelon)
        if r_idx == 1:
            repair_op(candidate, removed, evaluator, echelon, k=2)
        else:
            repair_op(candidate, removed, evaluator, echelon)
        clean_empty_routes(candidate)

    def after_operators(self, candidate, evaluator, current_solution, iteration,
                        d_idx, r_idx):
        pass

    def after_cost(self, candidate, evaluator, current_solution, iteration):
        pass

    def after_infeasible(self, evaluator, current_solution):
        pass

    def after_accept(self, evaluator, accepted_solution):
        pass

    def new_best_extra(self, best_solution):
        return ""

    def progress_extra(self, current_solution, best_solution):
        return ""

    def time_limit_message(self, iteration, max_time):
        return f"\n达到时间限制 {max_time}s"


def _task_counts(sol):
    total_e2 = sum(
        len(r.nodes)
        for routes in sol.e2_routes.values()
        for r in routes
    )
    total_e1 = sum(len(r.nodes) for r in sol.e1_routes)
    return total_e1, total_e2


def _route_group(sol, kind, key):
    return sol.e1_routes if kind == "e1" else sol.e2_routes[key]


def _first_improving_route_move(sol, evaluator, deadline):
    """Return the first feasible improving relocate/swap neighbor.

    Cross-route moves stay inside one echelon/satellite, so free-assignment
    models keep the satellite assignment unchanged during this polishing step.
    The model evaluator remains the authority for precedence, time and capacity.
    """
    descriptors = [("e1", None)] + [
        ("e2", sat) for sat in sorted(sol.e2_routes)
    ]
    base_cost = sol.cost

    def accept(candidate):
        clean_empty_routes(candidate)
        candidate.cost = evaluator.total_cost(candidate)
        return (candidate if candidate.cost < base_cost - FEAS_TOL
                and evaluator.is_feasible(candidate) else None)

    for kind, key in descriptors:
        routes = _route_group(sol, kind, key)
        for route_idx, route in enumerate(routes):
            n = len(route.nodes)
            for old_pos in range(n):
                for new_pos in range(n):
                    if time.time() >= deadline:
                        return None
                    if new_pos == old_pos:
                        continue
                    candidate = sol.copy()
                    nodes = _route_group(candidate, kind, key)[route_idx].nodes
                    node = nodes.pop(old_pos)
                    nodes.insert(new_pos, node)
                    improved = accept(candidate)
                    if improved is not None:
                        return improved
            for left in range(n):
                for right in range(left + 1, n):
                    if time.time() >= deadline:
                        return None
                    candidate = sol.copy()
                    nodes = _route_group(candidate, kind, key)[route_idx].nodes
                    nodes[left], nodes[right] = nodes[right], nodes[left]
                    improved = accept(candidate)
                    if improved is not None:
                        return improved

        for src_idx, src in enumerate(routes):
            for dst_idx, dst in enumerate(routes):
                if src_idx == dst_idx:
                    continue
                for src_pos in range(len(src.nodes)):
                    for dst_pos in range(len(dst.nodes) + 1):
                        if time.time() >= deadline:
                            return None
                        candidate = sol.copy()
                        group = _route_group(candidate, kind, key)
                        node = group[src_idx].nodes.pop(src_pos)
                        group[dst_idx].nodes.insert(dst_pos, node)
                        improved = accept(candidate)
                        if improved is not None:
                            return improved
                for src_pos in range(len(src.nodes)):
                    for dst_pos in range(len(dst.nodes)):
                        if time.time() >= deadline:
                            return None
                        candidate = sol.copy()
                        group = _route_group(candidate, kind, key)
                        (group[src_idx].nodes[src_pos],
                         group[dst_idx].nodes[dst_pos]) = (
                            group[dst_idx].nodes[dst_pos],
                            group[src_idx].nodes[src_pos])
                        improved = accept(candidate)
                        if improved is not None:
                            return improved

        task_map = (getattr(evaluator, "e1_task_map", None)
                    if kind == "e1"
                    else getattr(evaluator, "e2_task_map", None))
        if task_map:
            pairs = {}
            for route in routes:
                for node_id in route.nodes:
                    task = task_map.get(node_id)
                    if task is not None:
                        pairs.setdefault(task.cust, {})[task.kind] = node_id
            for pair in pairs.values():
                if "d" not in pair or "p" not in pair:
                    continue
                removed = sol.copy()
                removed_routes = _route_group(removed, kind, key)
                for route in removed_routes:
                    route.nodes = [node for node in route.nodes
                                   if node not in (pair["d"], pair["p"])]
                for d_route_idx, d_route in enumerate(removed_routes):
                    for d_pos in range(len(d_route.nodes) + 1):
                        with_delivery = removed.copy()
                        delivery_routes = _route_group(with_delivery, kind, key)
                        delivery_routes[d_route_idx].nodes.insert(
                            d_pos, pair["d"])
                        for p_route_idx, p_route in enumerate(delivery_routes):
                            for p_pos in range(len(p_route.nodes) + 1):
                                if time.time() >= deadline:
                                    return None
                                candidate = with_delivery.copy()
                                candidate_routes = _route_group(
                                    candidate, kind, key)
                                candidate_routes[p_route_idx].nodes.insert(
                                    p_pos, pair["p"])
                                improved = accept(candidate)
                                if improved is not None:
                                    return improved
    return None


def _intensify(sol, evaluator, deadline):
    improvements = 0
    current = sol.copy()
    current.cost = evaluator.total_cost(current)
    while time.time() < deadline:
        candidate = _first_improving_route_move(current, evaluator, deadline)
        if candidate is None:
            break
        current = candidate
        improvements += 1
    evaluator.is_feasible(current)
    return current, improvements


def run_alns_search(initial_solution, evaluator, destroy_ops, repair_ops,
                    config=None, hooks=None, verbose=True, start_time=None):
    config = config or ALNSConfig()
    hooks = hooks or ALNSHooks()
    start_time = time.time() if start_time is None else start_time
    # Expensive model-specific operators may consult this deadline so one
    # neighborhood evaluation cannot substantially overrun the wall-clock cap.
    evaluator._alns_deadline = start_time + config.max_time

    sol = initial_solution
    current_is_feasible = evaluator.is_feasible(sol)
    best_sol = sol.copy() if current_is_feasible else None
    best_cost = sol.cost if current_is_feasible else float("inf")
    best_found_time = ((time.time() - start_time)
                       if current_is_feasible else None)
    target_hit_time = None
    if (current_is_feasible and config.target_cost is not None
            and best_cost <= config.target_cost + config.target_tolerance):
        target_hit_time = time.time() - start_time

    n_d = len(destroy_ops)
    n_r = len(repair_ops)
    d_weights = [1.0] * n_d
    r_weights = [1.0] * n_r
    d_scores = [0.0] * n_d
    r_scores = [0.0] * n_r
    d_counts = [0] * n_d
    r_counts = [0] * n_r

    t_start = max(sol.cost * 0.05, 10.0)
    t_end = config.temperature_end
    temperature = t_start
    cooling_rate = (t_end / t_start) ** (
        1.0 / max(config.max_iterations, 1)
    )
    phase_start_elapsed = time.time() - start_time
    phase_start_temperature = t_start
    last_best_elapsed = best_found_time or phase_start_elapsed
    last_restart_elapsed = phase_start_elapsed
    restarts = 0
    local_search_improvements = 0

    total_e1, total_e2 = _task_counts(sol)
    iteration = -1
    termination_reason = "target" if target_hit_time is not None else "iterations"

    for iteration in range(0 if target_hit_time is not None
                           else config.max_iterations):
        elapsed_now = time.time() - start_time
        if elapsed_now > config.max_time:
            termination_reason = "time"
            if verbose:
                msg = hooks.time_limit_message(iteration, config.max_time)
                if msg:
                    print(msg)
            break

        if config.cooling_schedule == "time":
            phase_duration = max(config.max_time - phase_start_elapsed, 1e-9)
            phase_progress = min(
                1.0, max(0.0, (elapsed_now - phase_start_elapsed)
                         / phase_duration))
            temperature = phase_start_temperature * (
                t_end / phase_start_temperature) ** phase_progress

        stagnation_anchor = max(last_best_elapsed, last_restart_elapsed)
        if (config.stagnation_time is not None
                and config.stagnation_time > 0
                and best_sol is not None
                and restarts < config.max_restarts
                and elapsed_now - stagnation_anchor >= config.stagnation_time):
            if config.intensification_time > 0:
                deadline = min(
                    start_time + config.max_time,
                    time.time() + config.intensification_time)
                intensified, improvement_count = _intensify(
                    best_sol, evaluator, deadline)
                local_search_improvements += improvement_count
                if intensified.cost < best_cost - FEAS_TOL:
                    best_sol = intensified.copy()
                    best_cost = intensified.cost
                    best_found_time = time.time() - start_time
                    last_best_elapsed = best_found_time
                    if (config.target_cost is not None
                            and best_cost <= (config.target_cost
                                              + config.target_tolerance)):
                        target_hit_time = best_found_time
                        termination_reason = "target"
                        break
            sol = best_sol.copy()
            current_is_feasible = True
            hooks.after_accept(evaluator, sol)
            d_weights = [1.0] * n_d
            r_weights = [1.0] * n_r
            d_scores = [0.0] * n_d
            r_scores = [0.0] * n_r
            d_counts = [0] * n_d
            r_counts = [0] * n_r
            restarts += 1
            last_restart_elapsed = elapsed_now
            phase_start_elapsed = elapsed_now
            phase_start_temperature = max(
                temperature, t_start * config.reheat_fraction)
            temperature = phase_start_temperature

        d_idx = _roulette(d_weights)
        r_idx = _roulette(r_weights)
        d_counts[d_idx] += 1
        r_counts[r_idx] += 1

        if random.random() < config.e2_probability and total_e2 > 0:
            echelon = 'e2'
            n_tasks = total_e2
        else:
            echelon = 'e1'
            n_tasks = total_e1

        max_remove = max(2, min(n_tasks // 2, config.max_remove_cap))
        num_remove = random.randint(1, max(1, max_remove))

        candidate = sol.copy()
        hooks.before_candidate(candidate, evaluator, sol, iteration)
        hooks.apply_operators(
            candidate, evaluator, destroy_ops[d_idx], repair_ops[r_idx],
            d_idx, r_idx, num_remove, echelon,
        )
        hooks.after_operators(candidate, evaluator, sol, iteration, d_idx, r_idx)

        if (hooks.check_feasibility_after_cost
                and not evaluator.is_feasible(candidate)):
            hooks.after_infeasible(evaluator, sol)
            if config.cooling_schedule == "iteration":
                temperature *= cooling_rate
            continue

        candidate.cost = evaluator.total_cost(candidate)
        hooks.after_cost(candidate, evaluator, sol, iteration)

        if not evaluator.is_feasible(candidate):
            hooks.after_infeasible(evaluator, sol)
            if config.cooling_schedule == "iteration":
                temperature *= cooling_rate
            continue

        # Free-assignment models may deliberately start from an infeasible
        # assignment and repair it through their first destroy/repair move.
        # The first feasible candidate must replace that seed unconditionally;
        # an infeasible seed is never a valid incumbent.
        if not current_is_feasible:
            sol = candidate
            current_is_feasible = True
            if candidate.cost < best_cost:
                best_sol = candidate.copy()
                best_cost = candidate.cost
                best_found_time = time.time() - start_time
                last_best_elapsed = best_found_time
                d_scores[d_idx] += config.sigma1
                r_scores[r_idx] += config.sigma1
                if (config.target_cost is not None
                        and best_cost <= (config.target_cost
                                          + config.target_tolerance)):
                    target_hit_time = best_found_time
                    termination_reason = "target"
                    break
            if config.cooling_schedule == "iteration":
                temperature *= cooling_rate
            continue

        delta = candidate.cost - sol.cost
        if delta < -FEAS_TOL:
            sol = candidate
            hooks.after_accept(evaluator, sol)
            if sol.cost < best_cost - FEAS_TOL:
                best_sol = sol.copy()
                best_cost = sol.cost
                best_found_time = time.time() - start_time
                last_best_elapsed = best_found_time
                d_scores[d_idx] += config.sigma1
                r_scores[r_idx] += config.sigma1
                if verbose and iteration % 100 == 0:
                    extra = hooks.new_best_extra(best_sol)
                    print(f"  [{iteration:5d}] ★ 新最优: {best_cost:.2f}{extra}")
                if (config.target_cost is not None
                        and best_cost <= (config.target_cost
                                          + config.target_tolerance)):
                    target_hit_time = best_found_time
                    termination_reason = "target"
                    break
            else:
                d_scores[d_idx] += config.sigma2
                r_scores[r_idx] += config.sigma2
        elif temperature > 1e-10:
            prob = math.exp(-delta / temperature)
            if random.random() < prob:
                sol = candidate
                hooks.after_accept(evaluator, sol)
                d_scores[d_idx] += config.sigma3
                r_scores[r_idx] += config.sigma3

        if config.cooling_schedule == "iteration":
            temperature *= cooling_rate

        if (iteration + 1) % config.segment_size == 0:
            for i in range(n_d):
                if d_counts[i] > 0:
                    d_weights[i] = (
                        config.decay * d_weights[i] +
                        (1 - config.decay) * d_scores[i] / d_counts[i]
                    )
                d_scores[i] = 0.0
                d_counts[i] = 0
            for i in range(n_r):
                if r_counts[i] > 0:
                    r_weights[i] = (
                        config.decay * r_weights[i] +
                        (1 - config.decay) * r_scores[i] / r_counts[i]
                    )
                r_scores[i] = 0.0
                r_counts[i] = 0

            if verbose:
                elapsed = time.time() - start_time
                extra = hooks.progress_extra(sol, best_sol)
                print(f"  [{iteration + 1:5d}] cost={sol.cost:.2f}  "
                      f"best={best_cost:.2f}  T={temperature:.4f}  "
                      f"{extra}time={elapsed:.1f}s")

    elapsed = time.time() - start_time
    return ALNSResult(
        current_solution=sol,
        best_solution=best_sol,
        best_cost=best_cost,
        iterations=min(iteration + 1, config.max_iterations),
        elapsed=elapsed,
        best_found_time=best_found_time,
        target_hit_time=target_hit_time,
        termination_reason=termination_reason,
        restarts=restarts,
        local_search_improvements=local_search_improvements,
    )
