"""Regression checks for the hybrid free-assignment/relay Model 7 ALNS."""

import json
from pathlib import Path

import pytest

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


@pytest.mark.parametrize(
    "refill_cost,refill_feasible,expected_assignment,expected_cost,expected_baseline",
    [(None, True, 2, 80, 110), (90, True, 2, 80, 110),
     (70, True, 1, 70, 70), (60, False, 2, 80, 110)],
)
def test_portfolio_can_select_worse_delivery_assignment_after_relay(
    monkeypatch, refill_cost, refill_feasible,
    expected_assignment, expected_cost, expected_baseline,
):
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
            return getattr(sol, "feasible", True)

    search_calls = []
    def search(**kwargs):
        search_calls.append(kwargs)
        if len(search_calls) == 1:
            kwargs["hooks"].remember(kwargs["evaluator"], Candidate(2, 110))
            solution = Candidate(1, 100)
        else:
            # Refill must start from the clean stage-one incumbent, even
            # when a different assignment won the relay portfolio search.
            assert kwargs["initial_solution"].assignment == {1: 1}
            assert kwargs["initial_solution"].cost == 100
            assert not kwargs["initial_solution"].transshipments
            solution = Candidate(1, refill_cost)
            solution.feasible = refill_feasible
        return SimpleNamespace(
            best_solution=solution, best_cost=solution.cost,
            iterations=1, best_found_time=len(search_calls)-1, target_hit_time=None,
            termination_reason="iterations", restarts=0,
            local_search_improvements=0, diagnostics={})

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
        verbose=False, return_info=True, refill_unused_time=refill_cost is not None)
    assert assignment == {1: expected_assignment}
    assert info["objective"] == expected_cost
    assert info["stage1_best_objective"] == 100
    assert info["free_assignment_objective"] == expected_baseline
    assert info["transshipment_improvement"] == expected_baseline - expected_cost
    assert info["assignment_candidates_evaluated"] == 2
    assert len(search_calls) == (1 if refill_cost is None else 2)
    assert info["refill_iterations"] == (0 if refill_cost is None else 1)
    assert info["feasible"]
