"""Cached route schedules must invalidate on path or departure changes."""
import pytest
from models.routing.baselines.alns import model_3, model_4
from models.routing.alns.types import Route
from models.routing.common.task_builder import build_tasks_for_model
from tests.routing.test_alns_fast_neighborhoods import problem


def test_schedule_cache_key_includes_route_contents_and_start(problem):
    e1,e2 = build_tasks_for_model(problem,3)
    evaluator = model_3.Evaluator(problem,e1,e2,vehicles_per_satellite=4)
    sat = next(s for s,ts in e2.items() if ts)
    route = Route(sat,[t.id for t in e2[sat]][:4])
    for nodes,start in [(route.nodes,0),(route.nodes[::-1],0),(route.nodes,10000)]:
        changed = Route(sat,list(nodes))
        actual = evaluator.simulate_e2_route(changed,sat,start)
        reference = model_3.Evaluator.simulate_e2_route(evaluator,changed,sat,start)
        assert actual == reference


def test_free_geometry_is_valid_before_and_after_assignment_rebuild(problem):
    evaluator = model_4.Evaluator(problem,vehicles_per_satellite=4)
    customer = problem.customers[0].id
    satellite = problem.satellites[0].id
    expected = problem.distance(customer,satellite)
    assert evaluator.dist(f'{customer}d',f'{satellite}d') == expected
    sol = model_4.build_initial_solution(problem,evaluator)
    evaluator.evaluate(sol)
    assert evaluator.dist(f'{customer}d',f'{satellite}d') == expected
