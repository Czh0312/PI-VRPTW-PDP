# -*- coding: utf-8 -*-
"""Model 6 回归测试。

验证：
  1) 小规模精确解与 M2 已知最优一致（单仓算例 M6 == M2）；
  2) M6 ALNS 在 5/20 客户算例上给出可行解，且不劣于其初始解；
  3) 交叉验证：M6 解按推导分配转成 M2 任务后仍被 M2 求值器判为可行。
  4) E2 车辆的起始配送载量和逐节点容量都严格受限。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.instance import ProblemInstance
from core.node import Customer, Depot, Satellite
from models.alns_base import Route, Solution
from models.model_2.alns_solver import Evaluator as M2Evaluator
from models.model_6.alns_solver import (
    Evaluator as M6Evaluator,
    build_initial_solution,
    build_tasks_from_assignment,
    solve_alns as solve_m6_alns,
)
from models.model_6.exact import solve_exact
from utils.data_loader import parse_dataset

FLEET = json.loads(
    (ROOT / "config" / "fleet_config.json").read_text(encoding="utf-8"))


def load(path):
    nodes, _ = parse_dataset(str(ROOT / path))
    problem = ProblemInstance(nodes, FLEET, speed=FLEET.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    return problem


def _capacity_regression_problem(capacity, demands, container_weight):
    """Build a tiny physical instance that exposes E2 pickup-load mistakes."""
    nodes = {
        "D0": Depot("D0", 0.0, 0.0),
        "S0": Satellite("S0", 0.0, 10.0),
    }
    for idx, demand in enumerate(demands):
        nodes[f"C{idx}"] = Customer(
            f"C{idx}", 10.0 + 10.0 * idx, 10.0,
            demand=demand, service_time=1.0,
            tw_del=(0.0, 1000.0), tw_pick=(0.0, 1000.0),
            assigned_sat="S0",
        )
    fleet = {
        "E1_vehicle_count": 1,
        "E1_Qk": 100.0,
        "E1_Ck": 100.0,
        "vehicles_per_satellite": 1,
        "E2_Qv": float(capacity),
        "E2_Cv": 100.0,
        "container_weight": float(container_weight),
        "container_volume": 1.0,
        "speed": 1.0,
    }
    problem = ProblemInstance(nodes, fleet, speed=1.0)
    problem.aggregate_demands_from_pre_assignment()
    return problem


def test_e2_capacity_regressions():
    """Reject the two cases that the former M6 load model admitted.

    The first case catches a pickup that exceeds capacity at its task node.
    The second catches the subtler error where an early pickup could be used
    as fictitious cargo for a later delivery.  The boundary case documents
    that the repaired equality is not over-restrictive.
    """
    pickup_over_cap = _capacity_regression_problem(
        capacity=10.0, demands=[0.0], container_weight=11.0)
    _, _, _, info = solve_exact(
        pickup_over_cap, 1, time_limit=20, verbose=False, return_info=True)
    assert info["sol_count"] == 0, info

    delivery_over_cap = _capacity_regression_problem(
        capacity=10.0, demands=[3.0, 3.0], container_weight=3.0)
    _, _, _, info = solve_exact(
        delivery_over_cap, 1, time_limit=20, verbose=False, return_info=True)
    assert info["sol_count"] == 0, info

    boundary_feasible = _capacity_regression_problem(
        capacity=12.0, demands=[3.0, 3.0], container_weight=3.0)
    _, _, _, info = solve_exact(
        boundary_feasible, 1, time_limit=20, verbose=False, return_info=True)
    assert info["proven_optimal"], info
    print("  ✓ E2 load/capacity counterexamples are handled correctly")


def test_exact_matches_m2():
    cases = [
        ("data/5_customers/C101.csv", 391.570637),
        ("data/10_customers/C101.csv", 543.093837),
    ]
    for path, known in cases:
        problem = load(path)
        _, _, _, info = solve_exact(
            problem, problem.fleet.e2_per_sat_count,
            time_limit=180, return_info=True)
        assert info["proven_optimal"], f"{path} 未证明最优"
        assert abs(info["objective"] - known) < 1e-3, (
            f"{path} 目标值 {info['objective']} != {known}")
        print(f"  ✓ exact {path} = {info['objective']:.4f}")


def test_alns_feasible_and_cross_validated():
    for path in ["data/5_customers/C101.csv", "data/20_customers/C101.csv"]:
        problem = load(path)
        vps = problem.fleet.e2_per_sat_count
        r = solve_m6_alns(problem, vps, max_iterations=120, max_time=15,
                          seed=42, verbose=False)
        assert r[0] is not None, f"{path} M6 ALNS 未找到可行解"
        e1, e2, assign = r

        # 交叉验证：按推导分配构建 M2 任务，用 M2 求值器重新判可行
        e1_tasks, e2_tasks_by_sat = build_tasks_from_assignment(
            problem, assign)
        m2 = M2Evaluator(problem, e1_tasks, e2_tasks_by_sat, 9999.0, vps)
        sol = Solution()
        depot = problem.depot.id
        sol.e1_routes = [Route(depot, list(rr[1:-1])) for rr in e1]
        for sat, routes in e2.items():
            sol.e2_routes[sat] = [Route(sat, list(rr[1:-1]))
                                  for rr in routes]
        assert m2.is_feasible(sol), f"{path} 交叉验证失败：M2 判不可行"

        # 初始解不劣于 M6 自身构造的初始解
        ev6 = M6Evaluator(problem, 9999.0, vps)
        init = build_initial_solution(problem, ev6, 9999.0)
        init_cost = ev6.total_cost(init)
        sol_cost = ev6.total_cost(sol)
        assert sol_cost <= init_cost + 1e-6, (
            f"{path} 搜索后成本 {sol_cost} 高于初始 {init_cost}")
        print(f"  ✓ alns {path} feasible, cost={sol_cost:.2f}, "
              f"init={init_cost:.2f}, moved="
              f"{sum(1 for c, s in assign.items() if problem.nodes[c].assigned_sat != s)}")


if __name__ == "__main__":
    test_e2_capacity_regressions()
    test_exact_matches_m2()
    test_alns_feasible_and_cross_validated()
    print("\nModel 6 回归测试全部通过")
