# -*- coding: utf-8 -*-
"""Regression checks for Model 6 same-run transshipment accounting."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from models.routing.common.domain.instance import ProblemInstance
from models.routing.alns.variants.model_6 import (
    _undo_transshipment,
    solve_alns as solve_m6,
)
from models.routing.alns.variants import model_6 as model6_module
from models.routing.alns.types import Route, Solution
from models.routing.common.data_loader import parse_dataset
from models.routing.common.task_builder import build_tasks_for_model


ROOT = Path(__file__).resolve().parents[2]


def test_m6_handoff_can_be_removed_for_exchange_search():
    sol = Solution()
    pickup = "C1p"
    old_phys, new_phys = "C1", "C2"
    pick_virtual = f"TRANS_PICK_{pickup}@{old_phys}"
    drop_virtual = f"TRANS_DROP_{pickup}@{new_phys}"
    carrier = Route("S0", [pick_virtual, drop_virtual])
    carrier.transport_loads = {
        pick_virtual: (2.0, 1.0), drop_virtual: (2.0, 1.0)}
    sol.e2_routes = {"S0": [Route("S0", [pickup]), carrier]}
    sol.task_to_phys = {pickup: new_phys}
    key = ("S0", 0, pickup, new_phys)
    sol.transshipments[key] = (old_phys, 1, 2.0, 1.0)

    class Evaluator:
        task_to_phys = {}

        @staticmethod
        def total_cost(_solution):
            return 0.0

        @staticmethod
        def is_feasible(_solution):
            return True

    evaluator = Evaluator()
    assert _undo_transshipment(sol, evaluator, key)
    assert not sol.transshipments
    assert sol.task_to_phys[pickup] == old_phys
    cleaned_carrier = sol.e2_routes["S0"][1]
    assert cleaned_carrier.nodes == []
    assert cleaned_carrier.transport_loads == {}


def test_m6_pure_removal_exchange_does_not_expand_false_as_audit_event(monkeypatch):
    """An exchange can improve solely by deleting an existing handoff."""
    sol = Solution()
    sol.task_to_phys = {}

    class Evaluator:
        task_to_phys = {}

        @staticmethod
        def total_cost(_solution):
            return 10.0

    evaluator = Evaluator()
    monkeypatch.setattr(
        model6_module, "_search_and_apply_relocation",
        lambda *_args, **_kwargs: False)
    exchanges = iter([
        {
            "removed_key": ("S0", 0, "C1p", "C2"),
            "added": False,
            "before_cost": 12.0,
            "after_cost": 10.0,
        },
        False,
    ])
    monkeypatch.setattr(
        model6_module, "_search_and_apply_transshipment_exchange",
        lambda *_args, **_kwargs: next(exchanges))
    audit = [{"pickup_node": "C1p"}]

    model6_module.postprocess_transshipment(
        sol, evaluator, verbose=False, time_budget=1.0, audit_log=audit)

    assert audit == []


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
    phases = info["phase_diagnostics"]
    assert phases["refill"] is None
    assert phases["stage1"]["iterations"] == info["iterations"]
    assert phases["transshipment"]["objective"] == info["objective"]
    assert phases["stage1"]["objective"] == info["stage1_objective"]
    for event in info["transshipment_events"]:
        assert abs(
            event["distance_before"]
            - event["distance_after"]
            - event["actual_distance_saving"]
        ) < 1e-8


def test_exhausted_relocation_budget_does_not_start_exchange(monkeypatch):
    from types import SimpleNamespace
    from models.routing.alns.variants import model_6

    now = [0.0]
    monkeypatch.setattr(model_6.time, "time", lambda: now[0])

    def exhausted(*args, **kwargs):
        now[0] += kwargs["time_budget"]
        return False

    def unexpected(*args, **kwargs):
        raise AssertionError("exchange started after the deadline")

    monkeypatch.setattr(model_6, "_search_and_apply_relocation", exhausted)
    monkeypatch.setattr(model_6, "_search_and_apply_transshipment_exchange", unexpected)
    solution = SimpleNamespace(task_to_phys={}, cost=10.0)
    evaluator = SimpleNamespace(total_cost=lambda sol: sol.cost, task_to_phys={})
    model_6.postprocess_transshipment(solution, evaluator, time_budget=1, verbose=False)


@pytest.mark.parametrize("scan_elapsed,expected_budget", [(0.8, 0.2), (1.1, None)])
def test_exchange_uses_only_budget_left_after_relocation(monkeypatch, scan_elapsed, expected_budget):
    clock = [0.0]
    budgets = []
    monkeypatch.setattr(model6_module.time, "time", lambda: clock[0])

    def relocation(*args, **kwargs):
        clock[0] += scan_elapsed
        return False

    def exchange(*args, **kwargs):
        budgets.append(kwargs["time_budget"])
        return False

    monkeypatch.setattr(model6_module, "_search_and_apply_relocation", relocation)
    monkeypatch.setattr(model6_module, "_search_and_apply_transshipment_exchange", exchange)
    evaluator = SimpleNamespace(task_to_phys={}, total_cost=lambda sol: 10.0)
    model6_module.postprocess_transshipment(Solution(), evaluator, verbose=False, time_budget=1.0)
    if expected_budget is None:
        assert budgets == []
    else:
        assert budgets == pytest.approx([expected_budget])


def test_relocation_samples_stably_ordered_physical_locations(monkeypatch):
    tasks = [SimpleNamespace(id=f"{cust}p", cust=cust, kind="p")
             for cust in ["C3", "C1", "C2"]]
    solution = Solution()
    solution.e2_routes = {"S": [Route("S", [task.id for task in tasks])]}
    evaluator = SimpleNamespace(
        e2_tasks_by_sat={"S": tasks}, e2_task_map={task.id: task for task in tasks},
        is_feasible=lambda sol: True, e1_schedule=lambda sol: ({"S": 0.0}, {}),
        phys=lambda node: node[:-1], route_distance=lambda route: 0.0,
    )
    populations = []

    def sampled(population, count):
        populations.append(list(population))
        return []

    monkeypatch.setattr(model6_module.random, "sample", sampled)
    model6_module._search_and_apply_relocation(solution, evaluator,
        time_budget=5.0, sample_pickups=None, sample_locations=1)
    assert populations == [["C1", "C2"], ["C2", "C3"], ["C1", "C3"]]
