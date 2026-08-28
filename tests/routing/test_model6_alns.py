# -*- coding: utf-8 -*-
"""Regression checks for Model 6 same-run transshipment accounting."""
from pathlib import Path

from models.routing.common.domain.instance import ProblemInstance
from models.routing.alns.variants.model_6 import solve_alns as solve_m6
from models.routing.common.data_loader import parse_dataset
from models.routing.common.task_builder import build_tasks_for_model


ROOT = Path(__file__).resolve().parents[2]


def test_m6_reports_same_run_before_after_identity():
    nodes, _ = parse_dataset(str(
        ROOT / "data/zhou_later_pickup/small/standard_same_day/Set1_7-2-1b.csv"
    ))
    import json

    fleet = json.loads(
        (ROOT / "config/routing/validation/zhou_small.json").read_text(encoding="utf-8")
    )
    problem = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    problem.aggregate_demands_from_pre_assignment()
    e1_tasks, e2_tasks = build_tasks_for_model(problem, 6)
    _e1, _e2, info = solve_m6(
        problem,
        e1_tasks,
        e2_tasks,
        problem.fleet.e2_per_sat_count,
        TFp=9999.0,
        max_iterations=100,
        max_time=1.0,
        seed=42,
        verbose=False,
        strong_initial_solution=True,
        return_info=True,
        stage1_time_ratio=0.5,
        transshipment_time_budget=0.5,
        transshipment_sample_pickups=None,
        transshipment_sample_locations=None,
        transshipment_sample_carriers=None,
        transshipment_position_top_k=None,
        refill_unused_time=False,
    )
    assert info["feasible"]
    assert abs(
        info["stage1_objective"]
        - info["objective"]
        - info["transshipment_improvement"]
    ) < 1e-8
    assert info["transshipment_count"] == len(info["transshipment_events"])
    assert info["transshipment_search_elapsed_seconds"] >= 0.0
    for event in info["transshipment_events"]:
        assert abs(
            event["distance_before"]
            - event["distance_after"]
            - event["actual_distance_saving"]
        ) < 1e-8
