"""The pair insertion cost screen must preserve exhaustive repair choices."""
import json
from pathlib import Path

import pytest

from models.routing.alns.initial_solution import (
    _best_e2_item_insert, _e2_positions, _apply_positions, _remove_inserted,
)
from models.routing.alns.types import Route, FEAS_TOL
from models.routing.alns.variants.model_4 import Evaluator, build_initial_solution, _remove_customer
from models.routing.common.data_loader import parse_dataset
from models.routing.common.domain.instance import ProblemInstance

ROOT = Path(__file__).resolve().parents[2]


def exhaustive(routes, task_ids, evaluator, satellite, start):
    best = None
    for route in routes:
        old = evaluator.route_distance(route)
        for positions in _e2_positions(route, task_ids):
            _apply_positions(route, task_ids, positions)
            feasible = evaluator.check_e2_route_feasible(route, satellite, start)
            cost = evaluator.route_distance(route)
            _remove_inserted(route, positions)
            if feasible and (best is None or cost - old < best[0] - FEAS_TOL):
                best = (cost - old, route, positions)
    return best


@pytest.mark.parametrize("customer", ["C0", "C3", "C6"])
def test_pair_screen_matches_exhaustive_across_satellites(customer, monkeypatch):
    fleet = json.loads((ROOT / "config/routing/validation/zhou_small.json").read_text(encoding="utf-8"))
    nodes, _ = parse_dataset(str(ROOT / "data/zhou_later_pickup/small/standard_same_day/Set1_7-2-1b.csv"))
    problem = ProblemInstance(nodes, fleet, speed=1.0)
    problem.aggregate_demands_from_pre_assignment()
    evaluator = Evaluator(problem, vehicles_per_satellite=problem.fleet.e2_per_sat_count)
    solution = build_initial_solution(problem, evaluator)
    _remove_customer(solution, customer)
    evaluator.rebuild_from_solution(solution)
    starts = evaluator.e2_start_times(solution)
    count = [0]
    original = evaluator.check_e2_route_feasible

    def checked(*args):
        count[0] += 1
        return original(*args)

    monkeypatch.setattr(evaluator, "check_e2_route_feasible", checked)
    for satellite, routes in solution.e2_routes.items():
        for task_ids in ([customer + "d"], [customer + "d", customer + "p"]):
            before = [list(route.nodes) for route in routes]
            count[0] = 0
            expected = exhaustive(routes, task_ids, evaluator, satellite, starts.get(satellite, 0.0))
            old_calls = count[0]
            count[0] = 0
            actual = _best_e2_item_insert(routes, task_ids, evaluator, satellite, starts.get(satellite, 0.0))
            assert actual == expected
            assert count[0] <= old_calls
            assert [route.nodes for route in routes] == before


def test_nonimproving_positions_skip_feasibility_but_keep_first_tie():
    class FakeEvaluator:
        calls = 0

        def route_distance(self, route):
            return float(len(route.nodes))

        def check_e2_route_feasible(self, *args):
            self.calls += 1
            return True

    evaluator = FakeEvaluator()
    route = Route("S", ["A", "B", "C"])
    result = _best_e2_item_insert([route], ["D", "P"], evaluator, "S", 0.0)
    assert result == (2.0, route, (0, 1))
    assert evaluator.calls == 1
    assert route.nodes == ["A", "B", "C"]


def test_pair_screen_restores_route_when_evaluation_raises():
    class BrokenEvaluator:
        def route_distance(self, route):
            return float(len(route.nodes))

        def check_e2_route_feasible(self, *args):
            raise ValueError("simulation failed")

    route = Route("S", ["A", "B"])
    with pytest.raises(ValueError, match="simulation failed"):
        _best_e2_item_insert([route], ["D", "P"], BrokenEvaluator(), "S", 0.0)
    assert route.nodes == ["A", "B"]
