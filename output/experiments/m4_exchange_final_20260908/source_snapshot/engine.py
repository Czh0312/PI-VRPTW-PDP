# models/routing/alns/engine.py
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
import statistics
import time

from models.routing.alns.types import _roulette, clean_empty_routes, FEAS_TOL


@dataclass
class ALNSConfig:
    max_iterations: int = 5000
    max_time: float = 120
    segment_size: int = 100
    segment_time: float | None = 1.0
    min_segment_uses: int = 10
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
    stagnation_fraction: float | None = 0.30
    reheat_fraction: float = 0.5
    max_restarts: int = 2
    intensification_time: float = 0.0
    intensification_fraction: float = 0.08
    temperature_target_acceptance: float = 0.50
    temperature_calibration_samples: int = 20
    reward_time_exponent: float = 0.5
    operator_weight_floor: float = 0.05


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
    diagnostics: dict


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


def evaluate_solution(evaluator, solution):
    """Evaluate one candidate without duplicating model-specific rebuilds.

    Free-assignment evaluators reconstruct their task catalogues and E1 routes
    from the E2 assignment.  Calling ``total_cost`` and ``is_feasible``
    separately used to perform that reconstruction twice for every candidate.
    Evaluators may now provide ``evaluate(solution) -> (cost, feasible)``;
    fixed-assignment evaluators keep the backward-compatible fallback.
    """
    combined = getattr(evaluator, "evaluate", None)
    if callable(combined):
        cost, feasible = combined(solution)
    else:
        cost = evaluator.total_cost(solution)
        feasible = evaluator.is_feasible(solution)
    solution.cost = cost
    return cost, bool(feasible)


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
        if callable(getattr(evaluator, "evaluate", None)):
            _, feasible = evaluate_solution(evaluator, candidate)
        else:
            # Fixed-assignment cost evaluation does not rebuild assignments.
            # Reject non-improving neighbors before a full schedule audit.
            candidate.cost = evaluator.total_cost(candidate)
            feasible = (candidate.cost < base_cost - FEAS_TOL
                        and evaluator.is_feasible(candidate))
        return (candidate if candidate.cost < base_cost - FEAS_TOL
                and feasible else None)

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
    evaluate_solution(evaluator, current)
    while time.time() < deadline:
        candidate = _first_improving_route_move(current, evaluator, deadline)
        if candidate is None:
            break
        current = candidate
        improvements += 1
    evaluate_solution(evaluator, current)
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
    # Learn destroy-repair *pairs*. Crediting the two operators separately
    # hides interactions: one destroy may work well with one repair and poorly
    # with another.
    n_pairs = n_d * n_r
    pair_weights = [1.0] * n_pairs
    pair_scores = [0.0] * n_pairs
    pair_counts = [0] * n_pairs
    pair_times = [0.0] * n_pairs
    pair_totals = [
        {
            "attempts": 0,
            "feasible": 0,
            "accepted": 0,
            "improving": 0,
            "new_best": 0,
            "infeasible": 0,
            "runtime": 0.0,
        }
        for _ in range(n_pairs)
    ]

    t_start = max(sol.cost * 0.05, 10.0)
    t_end = config.temperature_end
    temperature = t_start
    cooling_rate = (t_end / t_start) ** (
        1.0 / max(config.max_iterations, 1)
    )
    phase_start_elapsed = time.time() - start_time
    phase_start_temperature = t_start
    positive_deltas = []
    temperature_calibrated = False
    weight_updates = 0
    last_weight_update_elapsed = phase_start_elapsed
    last_best_elapsed = best_found_time or phase_start_elapsed
    last_restart_elapsed = phase_start_elapsed
    restarts = 0
    local_search_improvements = 0

    total_e1, total_e2 = _task_counts(sol)
    iteration = -1
    termination_reason = "target" if target_hit_time is not None else "iterations"
    stagnation_limit = config.stagnation_time
    if (stagnation_limit is None
            and config.stagnation_fraction is not None):
        stagnation_limit = max(
            0.05, config.max_time * config.stagnation_fraction)
    intensification_limit = config.intensification_time
    if intensification_limit <= 0 and config.intensification_fraction > 0:
        intensification_limit = max(
            0.01, config.max_time * config.intensification_fraction)

    def update_pair_weights(elapsed, force=False):
        """Update weights after enough evidence or elapsed wall-clock time."""
        nonlocal pair_scores, pair_counts, pair_times
        nonlocal last_weight_update_elapsed, weight_updates
        uses = sum(pair_counts)
        count_trigger = config.segment_size > 0 and uses >= config.segment_size
        time_trigger = (
            config.segment_time is not None
            and config.segment_time > 0
            and uses >= max(1, config.min_segment_uses)
            and elapsed - last_weight_update_elapsed >= config.segment_time
        )
        if not force and not count_trigger and not time_trigger:
            return False
        if uses <= 0:
            return False

        used_avg_times = [
            pair_times[i] / pair_counts[i]
            for i in range(n_pairs)
            if pair_counts[i] > 0 and pair_times[i] > 0
        ]
        median_time = (statistics.median(used_avg_times)
                       if used_avg_times else 1.0)
        for i in range(n_pairs):
            if pair_counts[i] <= 0:
                continue
            quality = pair_scores[i] / pair_counts[i]
            avg_time = pair_times[i] / pair_counts[i]
            time_factor = 1.0
            if avg_time > 0 and median_time > 0:
                time_factor = (median_time / avg_time) ** max(
                    0.0, config.reward_time_exponent)
                time_factor = min(4.0, max(0.25, time_factor))
            target_weight = quality * time_factor
            pair_weights[i] = max(
                config.operator_weight_floor,
                config.decay * pair_weights[i]
                + (1 - config.decay) * target_weight,
            )
        pair_scores = [0.0] * n_pairs
        pair_counts = [0] * n_pairs
        pair_times = [0.0] * n_pairs
        last_weight_update_elapsed = elapsed
        weight_updates += 1
        return True

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
        if (stagnation_limit is not None
                and stagnation_limit > 0
                and best_sol is not None
                and restarts < config.max_restarts
                and elapsed_now - stagnation_anchor >= stagnation_limit):
            if intensification_limit > 0:
                deadline = min(
                    start_time + config.max_time,
                    time.time() + intensification_limit)
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
            # Preserve both learned weights and the open observation segment.
            # Resetting segment evidence here can prevent expensive models
            # from ever reaching a wall-clock or attempt-count update.
            restarts += 1
            last_restart_elapsed = elapsed_now
            phase_start_elapsed = elapsed_now
            phase_start_temperature = max(
                temperature, t_start * config.reheat_fraction)
            temperature = phase_start_temperature

        pair_idx = _roulette(pair_weights)
        d_idx, r_idx = divmod(pair_idx, n_r)
        pair_counts[pair_idx] += 1
        pair_totals[pair_idx]["attempts"] += 1
        attempt_started = time.perf_counter()

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
            attempt_runtime = time.perf_counter() - attempt_started
            pair_times[pair_idx] += attempt_runtime
            pair_totals[pair_idx]["runtime"] += attempt_runtime
            pair_totals[pair_idx]["infeasible"] += 1
            hooks.after_infeasible(evaluator, sol)
            if config.cooling_schedule == "iteration":
                temperature *= cooling_rate
            update_pair_weights(time.time() - start_time)
            continue

        _, candidate_is_feasible = evaluate_solution(evaluator, candidate)
        hooks.after_cost(candidate, evaluator, sol, iteration)

        if not candidate_is_feasible:
            attempt_runtime = time.perf_counter() - attempt_started
            pair_times[pair_idx] += attempt_runtime
            pair_totals[pair_idx]["runtime"] += attempt_runtime
            pair_totals[pair_idx]["infeasible"] += 1
            hooks.after_infeasible(evaluator, sol)
            if config.cooling_schedule == "iteration":
                temperature *= cooling_rate
            update_pair_weights(time.time() - start_time)
            continue

        attempt_runtime = time.perf_counter() - attempt_started
        pair_times[pair_idx] += attempt_runtime
        pair_totals[pair_idx]["runtime"] += attempt_runtime
        pair_totals[pair_idx]["feasible"] += 1

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
                pair_scores[pair_idx] += config.sigma1
                pair_totals[pair_idx]["new_best"] += 1
                if (config.target_cost is not None
                        and best_cost <= (config.target_cost
                                          + config.target_tolerance)):
                    target_hit_time = best_found_time
                    termination_reason = "target"
                    break
            if config.cooling_schedule == "iteration":
                temperature *= cooling_rate
            pair_totals[pair_idx]["accepted"] += 1
            update_pair_weights(time.time() - start_time)
            continue

        delta = candidate.cost - sol.cost
        if delta > FEAS_TOL and not temperature_calibrated:
            positive_deltas.append(delta)
            sample_count = max(1, config.temperature_calibration_samples)
            if len(positive_deltas) >= sample_count:
                target_acceptance = config.temperature_target_acceptance
                if 0.0 < target_acceptance < 1.0:
                    typical_delta = statistics.median(positive_deltas)
                    calibrated = -typical_delta / math.log(target_acceptance)
                    if math.isfinite(calibrated) and calibrated > 1e-10:
                        temperature = calibrated
                        phase_start_temperature = calibrated
                        phase_start_elapsed = time.time() - start_time
                        temperature_calibrated = True
        if delta < -FEAS_TOL:
            sol = candidate
            hooks.after_accept(evaluator, sol)
            pair_totals[pair_idx]["accepted"] += 1
            pair_totals[pair_idx]["improving"] += 1
            if sol.cost < best_cost - FEAS_TOL:
                best_sol = sol.copy()
                best_cost = sol.cost
                best_found_time = time.time() - start_time
                last_best_elapsed = best_found_time
                pair_scores[pair_idx] += config.sigma1
                pair_totals[pair_idx]["new_best"] += 1
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
                pair_scores[pair_idx] += config.sigma2
        elif temperature > 1e-10:
            prob = math.exp(-delta / temperature)
            if random.random() < prob:
                sol = candidate
                hooks.after_accept(evaluator, sol)
                # Do not reward structural no-ops merely because exp(0)=1.
                # Equal-cost candidates may still be accepted for diversity.
                if delta > FEAS_TOL:
                    pair_scores[pair_idx] += config.sigma3
                pair_totals[pair_idx]["accepted"] += 1

        if config.cooling_schedule == "iteration":
            temperature *= cooling_rate

        weights_changed = update_pair_weights(time.time() - start_time)
        if weights_changed and verbose:
            elapsed = time.time() - start_time
            extra = hooks.progress_extra(sol, best_sol)
            print(f"  [{iteration + 1:5d}] cost={sol.cost:.2f}  "
                  f"best={best_cost:.2f}  T={temperature:.4f}  "
                  f"{extra}time={elapsed:.1f}s")

    elapsed = time.time() - start_time
    online_weight_updates = weight_updates
    update_pair_weights(elapsed, force=True)
    pair_diagnostics = []
    for pair_idx, totals in enumerate(pair_totals):
        d_idx, r_idx = divmod(pair_idx, n_r)
        attempts = totals["attempts"]
        feasible = totals["feasible"]
        pair_diagnostics.append({
            "destroy_index": d_idx,
            "destroy": getattr(destroy_ops[d_idx], "__name__", str(d_idx)),
            "repair_index": r_idx,
            "repair": getattr(repair_ops[r_idx], "__name__", str(r_idx)),
            **totals,
            "feasible_rate": feasible / attempts if attempts else 0.0,
            "acceptance_rate": (
                totals["accepted"] / feasible if feasible else 0.0),
            "final_weight": pair_weights[pair_idx],
        })
    diagnostics = {
        "weight_updates": weight_updates,
        "online_weight_updates": online_weight_updates,
        "temperature_calibrated": temperature_calibrated,
        "temperature_positive_delta_samples": len(positive_deltas),
        "operator_pairs": pair_diagnostics,
    }
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
        diagnostics=diagnostics,
    )
