"""Regret repair must retain exhaustive choices when options are reused."""
from functools import partial
from tools.configuration.loader import load_json
from pathlib import Path
import random

import pytest

from archive.routing_baselines.alns.common.fixed_repair import FixedRouteInsertionCache
from archive.routing_baselines.alns.common.initial_solution import build_strong_initial_solution
from archive.routing_baselines.alns.common.types import Route
from archive.routing_baselines.alns import model_3
from models.common.data_loader import parse_dataset
from models.common.domain.instance import ProblemInstance
from models.common.task_builder import build_tasks_for_model

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def problem():
    nodes, _ = parse_dataset(str(ROOT / "data/zhou_later_pickup/large/standard_same_day/Set2_100-5-1.csv"))
    fleet = load_json(ROOT / "config/vehicles/zhou_large.json")
    result = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    result.aggregate_demands_from_pre_assignment()
    return result


@pytest.mark.parametrize("module,model", [(model_3, 3)])
@pytest.mark.parametrize("seed", [11, 23, 42])
@pytest.mark.parametrize("destroy_name", ["random_removal", "paired_customer_removal"])
def test_cached_regret_matches_exhaustive_repair(problem, module, model, seed, destroy_name, monkeypatch):
    e1, e2 = build_tasks_for_model(problem, model)
    evaluator = module.Evaluator(problem, e1, e2, vehicles_per_satellite=problem.fleet.e2_per_sat_count)
    random.seed(seed)
    solution = build_strong_initial_solution(problem, e1, e2, evaluator)
    removed = getattr(module, destroy_name)(solution, evaluator, 20, "e2")
    cached, exhaustive = solution.copy(), solution.copy()
    calls = []
    original_check = evaluator.check_e2_route_feasible

    def counted_check(*args):
        calls.append(1)
        return original_check(*args)

    monkeypatch.setattr(evaluator, "check_e2_route_feasible", counted_check)
    module.regret_insertion(cached, removed, evaluator)
    cached_calls = len(calls)
    calls.clear()
    monkeypatch.setattr(module, "FixedRouteInsertionCache", partial(FixedRouteInsertionCache, enabled=False))
    module.regret_insertion(exhaustive, removed, evaluator)
    assert cached_calls < len(calls)
    assert {sat: [r.nodes for r in routes] for sat, routes in cached.e2_routes.items()} == {
        sat: [r.nodes for r in routes] for sat, routes in exhaustive.e2_routes.items()}
    assert evaluator.total_cost(cached) == evaluator.total_cost(exhaustive)
    assert evaluator.is_feasible(cached) == evaluator.is_feasible(exhaustive)


class CountingEvaluator:
    def __init__(self):
        self.calls = 0

    def route_distance(self, route):
        return float(len(route.nodes))

    def check_e2_route_feasible(self, route, sat, start):
        self.calls += 1
        return start < 2


def test_cache_invalidation_start_times_and_empty_results():
    evaluator = CountingEvaluator()
    route = Route("S", ["A", "B"])
    original_nodes = route.nodes
    cache = FixedRouteInsertionCache(evaluator)
    assert [option[2] for option in cache.options(route, "C", "S", 0)] == [0, 1, 2]
    assert route.nodes is original_nodes
    assert len(cache.options(route, "C", "S", 0)) == 3
    assert evaluator.calls == 3
    assert cache.options(route, "C", "S", 2) == []
    assert cache.options(route, "C", "S", 2) == []
    assert evaluator.calls == 6
    route.nodes.append("D")
    cache.invalidate(route)
    assert len(cache.options(route, "C", "S", 0)) == 4
    assert evaluator.calls == 10


def test_cache_restores_route_when_evaluator_raises():
    class BrokenEvaluator(CountingEvaluator):
        def check_e2_route_feasible(self, *args):
            raise ValueError("failed evaluation")

    route = Route("S", ["A", "B"])
    original_nodes = route.nodes
    with pytest.raises(ValueError, match="failed evaluation"):
        FixedRouteInsertionCache(BrokenEvaluator()).options(route, "C", "S", 0)
    assert route.nodes is original_nodes
    assert route.nodes == ["A", "B"]
