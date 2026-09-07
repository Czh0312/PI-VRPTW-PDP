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



def test_assignment_pool_retains_distinct_assignments_and_copies():
    from types import SimpleNamespace
    from copy import deepcopy
    from models.routing.alns.variants.model_7 import AssignmentPoolHooks

    class Candidate:
        def __init__(self, assignment, cost):
            self.assignment, self.cost = assignment, cost

        def copy(self):
            return deepcopy(self)

    evaluator = SimpleNamespace(assignment=lambda sol: sol.assignment)
    hooks = AssignmentPoolHooks(2)
    first = Candidate({1: 1}, 10)
    hooks.remember(evaluator, first)
    first.cost = 99
    hooks.remember(evaluator, Candidate({1: 1}, 12))
    hooks.remember(evaluator, Candidate({1: 2}, 11))
    assert sorted(sol.cost for sol in hooks.pool.values()) == [10, 11]
    hooks.remember(evaluator, Candidate({1: 3}, 9))
    assert {tuple(sol.assignment.values()) for sol in hooks.pool.values()} == {(1,), (3,)}


def test_portfolio_can_select_worse_delivery_assignment_after_relay(monkeypatch):
    from copy import deepcopy
    from types import SimpleNamespace
    from models.routing.alns.variants import model_7

    class Candidate:
        def __init__(self, satellite, cost):
            self.assignment = {1: satellite}
            self.cost = cost
            self.e1_routes, self.e2_routes = [], {}
            self.task_to_phys, self.transshipments = {}, {}

        def copy(self):
            return deepcopy(self)

    class Evaluator:
        def __init__(self, *args, **kwargs):
            self.task_to_phys = {}

        def assignment(self, sol):
            return sol.assignment

        def total_cost(self, sol):
            return sol.cost

        def is_feasible(self, sol):
            return True

    def search(**kwargs):
        kwargs["hooks"].remember(kwargs["evaluator"], Candidate(2, 110))
        return SimpleNamespace(
            best_solution=Candidate(1, 100), best_cost=100,
            iterations=1, best_found_time=0, target_hit_time=None,
            termination_reason="iterations", restarts=0,
            local_search_improvements=0)

    def relay(sol, evaluator, **kwargs):
        if sol.assignment[1] == 2:
            sol.cost = 80
        return None

    monkeypatch.setattr(model_7, "FreeAssignmentEvaluator", Evaluator)
    monkeypatch.setattr(model_7, "RelayEvaluator", Evaluator)
    monkeypatch.setattr(model_7, "run_alns_search", search)
    monkeypatch.setattr(model_7, "build_tasks_from_assignment", lambda *a: ([], {}))
    monkeypatch.setattr(model_7, "postprocess_transshipment", relay)
    problem = SimpleNamespace(customers=[SimpleNamespace(id=1, assigned_sat=1)])
    *_, assignment, info = model_7.solve_alns(
        problem, initial_solution=Candidate(1, 100), max_time=10,
        verbose=False, return_info=True)
    assert assignment == {1: 2}
    assert info["objective"] == 80
    assert info["stage1_best_objective"] == 100
    assert info["free_assignment_objective"] == 110
    assert info["transshipment_improvement"] == 30
    assert info["assignment_candidates_evaluated"] == 2
