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


@dataclass
class ALNSResult:
    current_solution: object
    best_solution: object
    best_cost: float
    iterations: int
    elapsed: float


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


def run_alns_search(initial_solution, evaluator, destroy_ops, repair_ops,
                    config=None, hooks=None, verbose=True, start_time=None):
    config = config or ALNSConfig()
    hooks = hooks or ALNSHooks()
    start_time = time.time() if start_time is None else start_time

    sol = initial_solution
    best_sol = sol.copy()
    best_cost = sol.cost

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

    total_e1, total_e2 = _task_counts(sol)
    iteration = -1

    for iteration in range(config.max_iterations):
        if time.time() - start_time > config.max_time:
            if verbose:
                msg = hooks.time_limit_message(iteration, config.max_time)
                if msg:
                    print(msg)
            break

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
            temperature *= cooling_rate
            continue

        candidate.cost = evaluator.total_cost(candidate)
        hooks.after_cost(candidate, evaluator, sol, iteration)

        if not evaluator.is_feasible(candidate):
            hooks.after_infeasible(evaluator, sol)
            temperature *= cooling_rate
            continue

        delta = candidate.cost - sol.cost
        if delta < -FEAS_TOL:
            sol = candidate
            hooks.after_accept(evaluator, sol)
            if sol.cost < best_cost - FEAS_TOL:
                best_sol = sol.copy()
                best_cost = sol.cost
                d_scores[d_idx] += config.sigma1
                r_scores[r_idx] += config.sigma1
                if verbose and iteration % 100 == 0:
                    extra = hooks.new_best_extra(best_sol)
                    print(f"  [{iteration:5d}] ★ 新最优: {best_cost:.2f}{extra}")
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
    )
