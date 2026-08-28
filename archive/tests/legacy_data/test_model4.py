# -*- coding: utf-8 -*-
"""Model 4 回归测试。

验证：
  1) 小规模精确解与 M3 已知最优一致（单仓算例 M4 == M3）；
  2) M4 ALNS 在 5/20 客户算例上给出可行解，且不劣于其初始解；
  3) 交叉验证：M4 解按推导分配转成 M3 任务后仍被 M3 求值器判为可行。
  4) E2 车辆的起始配送载量和逐节点容量都严格受限。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Customer, Depot, Satellite
from models.routing.alns.types import Route, Solution
from models.routing.variants.model_3.alns_solver import Evaluator as M3Evaluator
from models.routing.variants.model_4.alns_solver import (
    Evaluator as M4Evaluator,
    build_initial_solution,
    build_tasks_from_assignment,
    solve_alns as solve_m4_alns,
)
from models.routing.variants.model_4.exact import solve_exact
from models.routing.variants.model_4.single_satellite_exact_bp import (
    solve_single_satellite_direct_program_mip,
)
from models.routing.common.data_loader import parse_dataset

FLEET = json.loads(
    (ROOT / "config" / "routing" / "validation" / "zhou_small.json").read_text(encoding="utf-8"))


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
    """Reject the two cases that the former M4 load model admitted.

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


def test_exact_matches_m3():
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


def test_accelerated_exact_matches_known_two_satellite_optimum():
    """Every enabled acceleration remains a full two-satellite certificate."""
    problem = load(
        "data/zhou_later_pickup/small/standard_same_day/Set1_7-2-1b.csv")
    options = {
        "prune_e2_arcs": True,
        "arc_specific_time_m": True,
        "drop_implied_precedence": True,
        "route_key_symmetry": True,
        "branch_assignment_first": True,
    }
    _, _, assignment, info = solve_exact(
        problem,
        problem.fleet.e2_per_sat_count,
        time_limit=60,
        verbose=False,
        return_info=True,
        formulation_options=options,
    )
    assert info["proven_optimal"], info
    assert abs(info["objective"] - 304.1510302675342) < 1e-6, info
    assert info["formulation_stats"]["e2_arcs_pruned_per_vehicle_pool"] > 0
    assert info["formulation_stats"]["precedence_rows_skipped"] == 686
    assert set(assignment.values()) == {"S1"}, assignment
    print("  PASS accelerated two-satellite exact formulation preserves optimum")


def test_single_satellite_direct_program_matches_exact():
    """The complete-program MIP and its fixed-count partition preserve the optimum."""
    problem = load("data/5_customers/C101.csv")
    result = solve_single_satellite_direct_program_mip(
        problem,
        problem.fleet.e2_per_sat_count,
        time_limit=None,
        subtour_strengthening_size=2,
    )
    info = result.info
    assert info["proven_optimal"], info
    assert abs(info["objective"] - 391.570637) < 1e-3, info
    assert info["best_bound"] == info["objective"], info
    print(f"  ✓ single-satellite direct program = {info['objective']:.4f}")

    fixed = solve_single_satellite_direct_program_mip(
        problem,
        problem.fleet.e2_per_sat_count,
        time_limit=30,
        subtour_strengthening_size=2,
        warm_start_e2_routes_by_sat=result.e2_routes_by_sat,
        required_vehicle_count=3,
    )
    fixed_info = fixed.info
    assert not fixed_info["proven_optimal"], fixed_info
    assert fixed_info["subproblem_proven_optimal"], fixed_info
    assert fixed_info["required_vehicle_count"] == 3, fixed_info
    assert fixed_info["best_bound"] is None, fixed_info
    assert abs(fixed_info["objective"] - info["objective"]) < 1e-6, fixed_info
    assert abs(
        fixed_info["subproblem_best_bound"] - fixed_info["objective"]
    ) < 1e-6, fixed_info
    print("  ✓ fixed-3-vehicle partition closes without claiming a global proof")


def test_alns_feasible_and_cross_validated():
    for path in ["data/5_customers/C101.csv", "data/20_customers/C101.csv"]:
        problem = load(path)
        vps = problem.fleet.e2_per_sat_count
        r = solve_m4_alns(problem, vps, max_iterations=120, max_time=15,
                          seed=42, verbose=False)
        assert r[0] is not None, f"{path} M4 ALNS 未找到可行解"
        e1, e2, assign = r

        # 交叉验证：按推导分配构建 M3 任务，用 M3 求值器重新判可行
        e1_tasks, e2_tasks_by_sat = build_tasks_from_assignment(
            problem, assign)
        m3 = M3Evaluator(problem, e1_tasks, e2_tasks_by_sat, 9999.0, vps)
        sol = Solution()
        depot = problem.depot.id
        sol.e1_routes = [Route(depot, list(rr[1:-1])) for rr in e1]
        for sat, routes in e2.items():
            sol.e2_routes[sat] = [Route(sat, list(rr[1:-1]))
                                  for rr in routes]
        assert m3.is_feasible(sol), f"{path} 交叉验证失败：M3 判不可行"

        # 初始解不劣于 M4 自身构造的初始解
        ev6 = M4Evaluator(problem, 9999.0, vps)
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
    test_exact_matches_m3()
    test_accelerated_exact_matches_known_two_satellite_optimum()
    test_single_satellite_direct_program_matches_exact()
    test_alns_feasible_and_cross_validated()
    print("\nModel 4 回归测试全部通过")
