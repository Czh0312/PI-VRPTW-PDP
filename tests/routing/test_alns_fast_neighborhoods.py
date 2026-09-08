"""Behavioral checks for route/assignment isolation and no-relay equivalence."""
import json
import random
from pathlib import Path

import pytest

from models.routing.alns.variants import model_3, model_4, model_6
from models.routing.common.data_loader import parse_dataset
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.task_builder import build_tasks_for_model

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def problem():
    nodes, _ = parse_dataset(str(
        ROOT / "data/zhou_later_pickup/small/standard_same_day/Set1_7-2-1b.csv"))
    fleet = json.loads((ROOT / "config/routing/validation/zhou_small.json").read_text())
    result = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    result.aggregate_demands_from_pre_assignment()
    return result


def test_fixed_moves_preserve_current_assignment_after_rejected_partial_state(problem):
    evaluator = model_4.Evaluator(problem, vehicles_per_satellite=problem.fleet.e2_per_sat_count)
    solution = model_4.build_initial_solution(problem, evaluator)
    assignment = evaluator.assignment(solution)
    original_paths = [r.full_path() for rs in solution.e2_routes.values() for r in rs]
    hooks = model_4.M4Hooks(1.0)
    for destroy_idx in range(6):
        for repair_idx in range(4):
            random.seed(100 * destroy_idx + repair_idx)
            rejected = solution.copy()
            model_4._remove_customer(rejected, next(iter(assignment)))
            evaluator.rebuild_from_solution(rejected)
            candidate = solution.copy()
            hooks.apply_operators(candidate, evaluator, None, None,
                                  destroy_idx, repair_idx, 3, "e2")
            assert evaluator.assignment(candidate) == assignment
            cost, feasible = evaluator.evaluate(candidate)
            fresh = model_4.Evaluator(problem, vehicles_per_satellite=problem.fleet.e2_per_sat_count)
            fresh_cost, fresh_feasible = fresh.evaluate(candidate.copy())
            assert cost == pytest.approx(fresh_cost)
            assert feasible == fresh_feasible
    assert hooks.neighborhood_attempts == {"fixed_routes": 24, "assignment": 0}
    assert original_paths == [r.full_path() for rs in solution.e2_routes.values() for r in rs]


def test_assignment_cache_reuses_tasks_but_rechecks_route_schedule(problem):
    evaluator = model_4.Evaluator(problem, vehicles_per_satellite=problem.fleet.e2_per_sat_count)
    solution = model_4.build_initial_solution(problem, evaluator)
    evaluator.evaluate(solution)
    tasks = evaluator.e1_task_map
    changed = solution.copy()
    for routes in changed.e2_routes.values():
        for route in routes:
            route.nodes.reverse()
    cost, feasible = evaluator.evaluate(changed)
    assert evaluator.e1_task_map is tasks
    fresh = model_4.Evaluator(problem, vehicles_per_satellite=problem.fleet.e2_per_sat_count)
    reference = changed.copy()
    reference_cost, reference_feasible = fresh.evaluate(reference)
    assert cost == pytest.approx(reference_cost)
    assert feasible == reference_feasible
    assert [r.nodes for r in changed.e1_routes] == [r.nodes for r in reference.e1_routes]


@pytest.mark.parametrize("fast", [False, True])
def test_no_relay_m6_matches_m3_under_iteration_budget(problem, fast):
    e1, e2 = build_tasks_for_model(problem, 3)
    common = dict(max_iterations=120, max_time=20, seed=42,
                  verbose=False, return_info=True,
                  alns_config_overrides=dict(cooling_schedule="iteration",
                      stagnation_fraction=None, segment_time=None,
                      reward_time_exponent=0, intensification_fraction=0))
    routes1, routes2, base = model_3.solve_alns(
        problem, e1, e2, problem.fleet.e2_per_sat_count, **common)
    actual1, actual2, actual = model_6.solve_alns(
        problem, e1, e2, problem.fleet.e2_per_sat_count,
        transshipment_time_budget=0, refill_unused_time=False,
        fast_stage1=fast, **common)
    assert actual["feasible"]
    assert actual["objective"] == pytest.approx(base["objective"])
    assert (actual1, actual2) == (routes1, routes2)
    assert actual["transshipment_count"] == 0
