"""Regression test for preserving a fixed-assignment M3 incumbent in M4."""

from tools.configuration.loader import load_json
from pathlib import Path

from models.routing.alns.types import Route, Solution
from models.routing.baselines.alns.model_3 import solve_alns as solve_m3
from models.routing.baselines.alns.model_4 import solve_alns as solve_m4
from models.routing.common.data_loader import parse_dataset
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.task_builder import build_tasks_for_model


ROOT = Path(__file__).resolve().parents[2]


def _as_solution(e1_paths, e2_paths):
    solution = Solution()
    solution.e1_routes = [
        Route(path[0], list(path[1:-1])) for path in e1_paths
    ]
    solution.e2_routes = {
        sat: [Route(path[0], list(path[1:-1])) for path in paths]
        for sat, paths in e2_paths.items()
    }
    return solution


def test_model4_keeps_external_model3_incumbent():
    nodes, _ = parse_dataset(str(
        ROOT / "data/zhou_later_pickup/small/standard_same_day/Set1_7-2-1b.csv"
    ))
    fleet = load_json(ROOT / "config/vehicles/zhou_small.json")
    problem = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    vps = problem.fleet.e2_per_sat_count

    e1_tasks, e2_tasks = build_tasks_for_model(problem, 3)
    m3_e1, m3_e2, m3_info = solve_m3(
        problem, e1_tasks, e2_tasks, vps,
        max_iterations=100,
        max_time=0.2,
        seed=42,
        verbose=False,
        return_info=True,
    )
    inherited = _as_solution(m3_e1, m3_e2)
    _, _, _, m4_info = solve_m4(
        problem, vps,
        initial_solution=inherited,
        max_iterations=1,
        max_time=0.1,
        seed=42,
        verbose=False,
        return_info=True,
    )

    assert m4_info["feasible"]
    assert abs(m4_info["initial_objective"] - m3_info["objective"]) < 1e-8
    assert m4_info["objective"] <= m3_info["objective"] + 1e-8
