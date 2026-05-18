# models/model_4/solver.py
# -*- coding: utf-8 -*-
"""
Model 4 两阶段精确求解：
  阶段一：调用 Model 2 的 MIP 求解（无中转）
  阶段二：对固定路线，后处理搜索中转机会（启发式）
"""
from models.model_2.solver import solve_exact as solve_m2_exact
from models.model_2.alns_solver import solve_alns as solve_m2_alns
from models.alns_base import Route, Solution


def solve_exact(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                TFp=9999.0, verbose=True):
    """
    Model 4 两阶段精确求解
    阶段一用 Model 2 的 MIP，阶段二用启发式后处理
    """
    if verbose:
        print(f"\n{'=' * 60}")
        print(f"Model 4 两阶段精确求解")
        print(f"{'=' * 60}")
        print(f"--- 阶段一：Model 2 MIP (无中转) ---")

    # 阶段一：Model 2 MIP
    e1_routes, e2_routes_by_sat = solve_m2_exact(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
        TFp=TFp, verbose=verbose
    )

    if e1_routes is None:
        return None, None

    # 阶段一 cost（直接从路线计算）
    stage1_cost = _compute_total_distance(
        problem, e1_tasks, e2_tasks_by_sat, e1_routes, e2_routes_by_sat, TFp)

    if verbose:
        print(f"\n  阶段一完成: cost={stage1_cost:.2f}")
        print(f"\n--- 阶段二：后处理中转搜索 ---")

    # 阶段二：启发式后处理中转
    from models.model_4.alns_solver import Evaluator, postprocess_transshipment

    sol = _routes_to_solution(e1_routes, e2_routes_by_sat)
    evaluator = Evaluator(problem, e1_tasks, e2_tasks_by_sat, TFp,
                          vehicles_per_satellite=vehicles_per_satellite)
    sol.cost = evaluator.total_cost(sol)

    postprocess_transshipment(sol, evaluator, verbose=verbose)

    stage2_cost = evaluator.total_cost(sol)
    if verbose:
        print(f"\n  阶段二完成: cost={stage2_cost:.2f} "
              f"(改进: {stage1_cost - stage2_cost:.2f})")
        print(f"  中转操作数: {len(sol.transshipments)}")
        print(f"\n{'=' * 60}")
        print(f"Model 4 求解完成")
        print(f"阶段一(无中转): {stage1_cost:.2f}")
        print(f"阶段二(中转后): {stage2_cost:.2f}")
        print(f"{'=' * 60}")

    e1_routes_out = [r.full_path() for r in sol.e1_routes]
    e2_routes_out = {}
    for sat_id, routes in sol.e2_routes.items():
        e2_routes_out[sat_id] = [r.full_path() for r in routes]

    return e1_routes_out, e2_routes_out


def _routes_to_solution(e1_routes, e2_routes_by_sat):
    """将 MIP 输出的路线格式转换为 ALNS Solution 格式"""
    sol = Solution()
    for route in e1_routes:
        base = route[0]
        nodes = route[1:-1]  # 去掉首尾的 depot
        sol.e1_routes.append(Route(base, nodes))

    for sat_id, routes in e2_routes_by_sat.items():
        sat_routes = []
        for route in routes:
            base = route[0]
            nodes = route[1:-1]
            sat_routes.append(Route(base, nodes))
        sol.e2_routes[sat_id] = sat_routes

    return sol


def _compute_total_distance(problem, e1_tasks, e2_tasks_by_sat,
                            e1_routes, e2_routes_by_sat, TFp=9999.0):
    """从路线计算总距离"""
    from models.model_4.alns_solver import Evaluator

    evaluator = Evaluator(problem, e1_tasks, e2_tasks_by_sat, TFp)
    sol = _routes_to_solution(e1_routes, e2_routes_by_sat)
    return evaluator.total_cost(sol)
