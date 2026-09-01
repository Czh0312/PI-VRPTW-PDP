from types import SimpleNamespace
import time

from models.routing.alns.engine import (
    ALNSConfig, run_alns_search, _first_improving_route_move,
)
from models.routing.alns.types import Route, Solution
from models.routing import service


class _AlwaysFeasibleEvaluator:
    def is_feasible(self, solution):
        return True

    def total_cost(self, solution):
        return solution.cost


class _CombinedEvaluator:
    def __init__(self):
        self.combined_calls = 0
        self.legacy_cost_calls = 0
        self.legacy_feasibility_calls = 0

    def evaluate(self, solution):
        self.combined_calls += 1
        return solution.cost, True

    def total_cost(self, solution):
        self.legacy_cost_calls += 1
        return solution.cost

    def is_feasible(self, solution):
        self.legacy_feasibility_calls += 1
        return True


def _noop_destroy(solution, evaluator, num_remove, echelon="e2"):
    time.sleep(0.001)
    return []


def _noop_repair(solution, removed, evaluator, echelon="e2", k=2):
    return None


def test_time_segment_updates_even_before_one_hundred_iterations():
    solution = Solution()
    solution.e1_routes = [Route("D", ["S1d"])]
    solution.cost = 10.0
    result = run_alns_search(
        solution,
        _AlwaysFeasibleEvaluator(),
        [_noop_destroy],
        [_noop_repair],
        config=ALNSConfig(
            max_iterations=8,
            max_time=1.0,
            segment_size=100,
            segment_time=0.0005,
            min_segment_uses=2,
            max_restarts=0,
            stagnation_fraction=None,
            temperature_calibration_samples=2,
        ),
        verbose=False,
    )
    assert result.diagnostics["weight_updates"] >= 1
    assert result.diagnostics["online_weight_updates"] >= 1
    pair = result.diagnostics["operator_pairs"][0]
    assert pair["attempts"] == 8
    assert pair["feasible_rate"] == 1.0
    assert pair["acceptance_rate"] == 1.0


def test_engine_prefers_combined_candidate_evaluation():
    solution = Solution()
    solution.e1_routes = [Route("D", ["S1d"])]
    solution.cost = 10.0
    evaluator = _CombinedEvaluator()
    result = run_alns_search(
        solution,
        evaluator,
        [_noop_destroy],
        [_noop_repair],
        config=ALNSConfig(
            max_iterations=3,
            max_time=1.0,
            max_restarts=0,
            stagnation_fraction=None,
            intensification_fraction=0.0,
        ),
        verbose=False,
    )
    assert result.iterations == 3
    assert evaluator.combined_calls == 3
    # The initial incumbent check remains feasibility-only. Candidate cost and
    # feasibility must not fall back to the two legacy calls.
    assert evaluator.legacy_cost_calls == 0
    assert evaluator.legacy_feasibility_calls == 1


def test_polishing_skips_non_improving_fixed_assignment_schedule_audits():
    class Evaluator:
        def total_cost(self, solution):
            return 10.0

        def is_feasible(self, solution):
            raise AssertionError("Non-improving neighbor should be rejected first")

    solution = Solution()
    solution.e1_routes = [Route("D", ["S1d", "S2d"])]
    solution.cost = 10.0
    assert _first_improving_route_move(solution, Evaluator(), time.time() + 1) is None


def test_polishing_still_audits_improving_candidates():
    class Evaluator:
        calls = 0

        def total_cost(self, solution):
            return 9.0

        def is_feasible(self, solution):
            self.calls += 1
            return False

    solution = Solution()
    solution.e1_routes = [Route("D", ["S1d", "S2d"])]
    solution.cost = 10.0
    evaluator = Evaluator()
    assert _first_improving_route_move(solution, evaluator, time.time() + 1) is None
    assert evaluator.calls > 0


def test_polishing_keeps_combined_evaluation_for_free_assignment():
    solution = Solution()
    solution.e1_routes = [Route("D", ["S1d", "S2d"])]
    solution.cost = 10.0
    evaluator = _CombinedEvaluator()
    assert _first_improving_route_move(solution, evaluator, time.time() + 1) is None
    assert evaluator.combined_calls > 0
    assert evaluator.legacy_cost_calls == evaluator.legacy_feasibility_calls == 0


def test_service_uses_model_specific_alns_signatures(monkeypatch):
    captured = {}

    def fake_solver(**kwargs):
        captured.clear()
        captured.update(kwargs)
        return (), ()

    monkeypatch.setattr(service, "_load_solver", lambda model, method: fake_solver)
    problem = SimpleNamespace(
        fleet=SimpleNamespace(e2_per_sat_count=3),
    )

    service.solve_routing(
        model=2, method="alns", problem=problem,
        e1_tasks=["e1"], e2_tasks_by_sat={"S": ["e2"]},
        alns_time=2.0,
    )
    assert "e1_tasks" not in captured
    assert "e2_tasks_by_sat" not in captured
    assert "alns_config_overrides" in captured

    service.solve_routing(
        model=1, method="alns", problem=problem,
        e1_tasks=["e1"], e2_tasks_by_sat={"S": ["e2"]},
        alns_time=2.0,
    )
    assert captured["e1_tasks"] == ["e1"]
    assert captured["e2_tasks_by_sat"] == {"S": ["e2"]}
    assert captured["strong_initial_solution"] is True
