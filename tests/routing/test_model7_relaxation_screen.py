"""Licensed integration checks for M7's metric partition proof screen."""
import pytest

pytest.importorskip("gurobipy", reason="partition proof integration requires Gurobi")

from tests.routing.test_model7_exact import _problem
from models.routing.variants.model_7.exact_solver import _partition_relaxation_screen
from models.routing.variants.model_6.exact_solver import solve_exact
from models.routing.common.task_builder import build_tasks_for_model


def test_screen_cutoff_is_backed_by_full_handoff_optimum():
    problem = _problem()
    e1, e2 = build_tasks_for_model(problem, 6)
    _, _, exact = solve_exact(problem, e1, e2, 2, time_limit=10,
        verbose=False, return_info=True, use_relaxation_certificate=False)
    assert exact["proven_optimal"]
    cutoff = exact["objective"] - 1e-9
    probe = _partition_relaxation_screen(problem, e1, e2, 2,
        TFp=9999, cutoff=cutoff, time_limit=3, gurobi_params={"Cutoff": -1e6})
    assert probe["pruned"]
    assert probe["status"] == "CUTOFF"
    assert probe["cutoff"] == cutoff
    assert probe["best_bound"] >= cutoff


def test_relaxed_solution_below_cutoff_does_not_prune():
    problem = _problem()
    e1, e2 = build_tasks_for_model(problem, 6)
    probe = _partition_relaxation_screen(problem, e1, e2, 2,
        TFp=9999, cutoff=1e6, time_limit=3)
    assert not probe["pruned"]
    assert probe["best_bound"] < 1e6
    assert "objective" not in probe


def test_nonmetric_distances_disable_partition_screen(monkeypatch):
    problem = _problem()
    e1, e2 = build_tasks_for_model(problem, 6)
    distance = problem.distance
    monkeypatch.setattr(problem, "distance", lambda a, b:
        100.0 if (a, b) == ("D0", "C0") else distance(a, b))
    assert _partition_relaxation_screen(problem, e1, e2, 2,
        TFp=9999, cutoff=100, time_limit=3) is None
