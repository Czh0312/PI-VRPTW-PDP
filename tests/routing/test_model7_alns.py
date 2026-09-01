"""Regression checks for the hybrid free-assignment/relay Model 7 ALNS."""

import json
from pathlib import Path

from models.routing.alns.variants.model_7 import M7_DESTROY_OPS, solve_alns
from models.routing.alns.variants.model_2 import (
    _candidate_seeds as m2_candidate_seeds,
)
from models.routing.alns.variants.model_4 import (
    Evaluator as M4Evaluator,
    _remove_customer as m4_remove_customer,
    build_initial_solution as build_m4_initial_solution,
    _candidate_seeds as m4_candidate_seeds,
)
from models.routing.common.data_loader import parse_dataset
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.task_builder import build_tasks_for_model


ROOT = Path(__file__).resolve().parents[2]


def test_model7_uses_full_free_assignment_destroy_set():
    names = {operator.__name__ for operator in M7_DESTROY_OPS}
    assert "assignment_boundary_removal" in names
    assert "two_satellite_removal" in names


def test_large_free_assignment_seeds_repair_impossible_deadline():
    nodes, _ = parse_dataset(str(
        ROOT / "data/zhou_later_pickup/large/standard_same_day/Set2_200-10-3.csv"
    ))
    fleet = json.loads((
        ROOT / "config/routing/validation/zhou_large.json"
    ).read_text(encoding="utf-8"))
    problem = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()

    # C93 cannot meet due=75 through its input S3 assignment even on the
    # direct lower-bound path.  Both free-assignment solvers must expose a
    # repaired seed instead of relying only on the structurally infeasible one.
    assert any(seed["C93"] != "S3" for seed in m2_candidate_seeds(problem))
    assert any(seed["C93"] != "S3" for seed in m4_candidate_seeds(problem))


def test_same_assignment_delivery_positions_share_e2_start_time():
    nodes, _ = parse_dataset(str(
        ROOT / "data/zhou_later_pickup/small/standard_same_day/Set1_7-2-1b.csv"
    ))
    fleet = json.loads((
        ROOT / "config/routing/validation/zhou_small.json"
    ).read_text(encoding="utf-8"))
    problem = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    evaluator = M4Evaluator(
        problem, TFp=9999.0,
        vehicles_per_satellite=problem.fleet.e2_per_sat_count,
    )
    solution = build_m4_initial_solution(problem, evaluator)
    customer = sorted(evaluator.assignment(solution))[0]
    removed = solution.copy()
    m4_remove_customer(removed, customer)
    target_satellite = next(
        satellite for satellite, routes in removed.e2_routes.items()
        if routes and routes[0].nodes
    )
    delivery = evaluator.catalog[customer]["d"].id

    starts = []
    for position in (0, len(removed.e2_routes[target_satellite][0].nodes)):
        candidate = removed.copy()
        candidate.e2_routes[target_satellite][0].nodes.insert(
            position, delivery)
        evaluator.rebuild_from_solution(candidate)
        starts.append(evaluator.e2_start_times(candidate)[target_satellite])

    assert abs(starts[0] - starts[1]) <= 1e-9


def test_model7_combines_free_assignment_and_relay_accounting():
    nodes, _ = parse_dataset(str(
        ROOT / "data/zhou_later_pickup/small/standard_same_day/Set1_7-2-1b.csv"
    ))
    fleet = json.loads((
        ROOT / "config/routing/validation/zhou_small.json"
    ).read_text(encoding="utf-8"))
    problem = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()

    # A free-assignment model must not receive tasks pre-bucketed by the input
    # assignment. The solver rebuilds both echelons from its chosen assignment.
    e1_tasks, e2_tasks = build_tasks_for_model(problem, 7)
    assert e1_tasks == []
    assert all(not tasks for tasks in e2_tasks.values())

    e1_routes, e2_routes, assignment, info = solve_alns(
        problem,
        e1_tasks,
        e2_tasks,
        problem.fleet.e2_per_sat_count,
        max_iterations=100,
        max_time=1.0,
        stage1_time_ratio=0.5,
        transshipment_time_budget=0.5,
        seed=42,
        verbose=False,
        return_info=True,
    )

    assert e1_routes and e2_routes
    assert set(assignment) == {customer.id for customer in problem.customers}
    assert info["feasible"]
    assert info["model_scope"] == "free_assignment_plus_async_event_handoff"
    assert info["assignment"] == assignment
    assert info["transshipment_count"] == len(info["transshipment_events"])
    assert abs(
        info["free_assignment_objective"]
        - info["objective"]
        - info["transshipment_improvement"]
    ) < 1e-8
    assert info["objective"] <= info["free_assignment_objective"] + 1e-8
