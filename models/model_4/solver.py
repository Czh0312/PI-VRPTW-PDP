# models/model_4/solver.py
# -*- coding: utf-8 -*-
"""
Model 4 两阶段中转优化：
  阶段一：调用 Model 2 的 MIP 求解（无中转）
  阶段二：对固定路线，用 0-1 MIP 精确选择中转方案
"""
from models.model_2.solver import solve_exact as solve_m2_exact
from models.alns_base import Route, Solution


def solve_exact(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                TFp=9999.0, verbose=True, time_limit=300, mip_gap=0.0,
                return_incumbent_on_time_limit=False, return_info=False,
                warm_start_routes=None, gurobi_params=None):
    """
    Run the Model 4 two-stage method.

    This method is exact only for its *fixed-route second-stage* subproblem;
    it is not a global Model 4 exact algorithm.  The return metadata always
    records that fact so comparison code cannot report it as certified exact.
    """
    if verbose:
        print(f"\n{'=' * 60}")
        print(f"Model 4 两阶段精确求解")
        print(f"{'=' * 60}")
        print(f"--- 阶段一：Model 2 MIP (无中转) ---")

    # 阶段一：Model 2 MIP
    e1_routes, e2_routes_by_sat, stage1_info = solve_m2_exact(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
        TFp=TFp, verbose=verbose, time_limit=time_limit, mip_gap=mip_gap,
        return_incumbent_on_time_limit=return_incumbent_on_time_limit,
        warm_start_routes=warm_start_routes, gurobi_params=gurobi_params,
        return_info=True,
    )

    if e1_routes is None:
        info = {
            "status": stage1_info.get("status", "NO_SOLUTION"),
            "solution_type": "none",
            "proven_optimal": False,
            "stage1": stage1_info,
        }
        return (None, None, info) if return_info else (None, None)

    # 阶段一 cost（直接从路线计算）
    stage1_cost = _compute_total_distance(
        problem, e1_tasks, e2_tasks_by_sat, e1_routes, e2_routes_by_sat, TFp)

    if verbose:
        print(f"\n  阶段一完成: cost={stage1_cost:.2f}")
        print(f"\n--- 阶段二：固定路线中转选择 MIP ---")

    # 阶段二：固定路线上的精确中转选择
    from models.model_4.alns_solver import Evaluator, _path_with_relocations
    from models.model_4.transshipment_mip import solve_stage2_transshipment_mip

    sol = _routes_to_solution(e1_routes, e2_routes_by_sat)
    evaluator = Evaluator(problem, e1_tasks, e2_tasks_by_sat, TFp,
                          vehicles_per_satellite=vehicles_per_satellite)
    sol.task_to_phys = dict(evaluator.task_to_phys)
    sol.cost = evaluator.total_cost(sol)

    solve_stage2_transshipment_mip(sol, evaluator, verbose=verbose)

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

    evaluator.task_to_phys = dict(sol.task_to_phys)
    e1_routes_out = [
        _path_with_relocations(r, sol, evaluator)
        for r in sol.e1_routes
    ]
    e2_routes_out = {}
    for sat_id, routes in sol.e2_routes.items():
        e2_routes_out[sat_id] = [
            _path_with_relocations(r, sol, evaluator)
            for r in routes
        ]

    info = {
        "status": "TWO_STAGE_COMPLETE",
        "solution_type": "two_stage_not_global",
        "proven_optimal": False,
        "stage1": stage1_info,
        "objective": stage2_cost,
    }
    if return_info:
        return e1_routes_out, e2_routes_out, info
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
