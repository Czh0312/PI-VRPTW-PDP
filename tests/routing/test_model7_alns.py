"""Regression checks for the hybrid free-assignment/relay Model 7 ALNS."""

import json
from pathlib import Path

from models.routing.alns.variants.model_7 import solve_alns
from models.routing.common.data_loader import parse_dataset
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.task_builder import build_tasks_for_model


ROOT = Path(__file__).resolve().parents[2]


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

