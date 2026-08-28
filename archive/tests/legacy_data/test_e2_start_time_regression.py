# experiments/test_e2_start_time_regression.py
# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from models.routing.common.domain.instance import ProblemInstance
from models.routing.alns.types import Route, Solution
from models.routing.variants.model_6.alns_solver import (
    Evaluator,
    build_initial_solution,
    greedy_insertion,
    regret_insertion,
)
from models.routing.common.data_loader import parse_dataset
from models.routing.common.task_builder import build_tasks_for_model


DATA_FILE = PROJECT_ROOT / "data" / "10_customers" / "C103.csv"
FLEET_FILE = PROJECT_ROOT / "config" / "routing" / "validation" / "zhou_small.json"


def _model6_context():
    with FLEET_FILE.open("r", encoding="utf-8") as f:
        fleet_config = json.load(f)
    nodes_dict, _ = parse_dataset(str(DATA_FILE))
    problem = ProblemInstance(
        nodes_dict,
        fleet_config,
        speed=fleet_config.get("speed", 1.0),
    )
    problem.aggregate_demands_from_pre_assignment()
    e1_tasks, e2_tasks_by_sat = build_tasks_for_model(problem, 6)
    evaluator = Evaluator(
        problem,
        e1_tasks,
        e2_tasks_by_sat,
        TFp=9999.0,
        vehicles_per_satellite=problem.fleet.e2_per_sat_count,
    )
    base_sol = build_initial_solution(
        problem, e1_tasks, e2_tasks_by_sat, evaluator)
    return evaluator, base_sol


def _partial_solution(base_sol):
    sol = Solution()
    sol.e1_routes = [route.copy() for route in base_sol.e1_routes]
    sol.e2_routes = {"S6": [Route("S6", ["C2d", "C95d"])]}
    return sol


def _assert_insert_uses_real_start(repair_op):
    evaluator, base_sol = _model6_context()
    e2_start_by_sat = evaluator.e2_start_times(base_sol)
    start_time = e2_start_by_sat["S6"]
    assert start_time == 75.0
    assert evaluator.e2_task_satellite("C28d") == "S6"

    old_best_route = Route("S6", ["C28d", "C2d", "C95d"])
    true_best_route = Route("S6", ["C2d", "C95d", "C28d"])

    assert evaluator.check_e2_route_feasible(old_best_route, "S6", 0.0)
    assert not evaluator.check_e2_route_feasible(
        old_best_route, "S6", start_time)
    assert evaluator.check_e2_route_feasible(
        true_best_route, "S6", start_time)

    sol = _partial_solution(base_sol)
    if repair_op is regret_insertion:
        repair_op(sol, ["C28d"], evaluator, "e2", k=2)
    else:
        repair_op(sol, ["C28d"], evaluator, "e2")

    repaired_route = sol.e2_routes["S6"][0]
    assert repaired_route.nodes == true_best_route.nodes
    assert evaluator.check_e2_route_feasible(
        repaired_route, "S6", start_time)


def main():
    _assert_insert_uses_real_start(greedy_insertion)
    _assert_insert_uses_real_start(regret_insertion)
    print("E2 start-time regression passed")


if __name__ == "__main__":
    main()
