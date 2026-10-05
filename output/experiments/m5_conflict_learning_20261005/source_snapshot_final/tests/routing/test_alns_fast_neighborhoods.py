"""Behavioral checks for route/assignment isolation and no-relay equivalence."""
import json
import random
from pathlib import Path

import pytest

from models.routing.baselines.alns import model_2, model_3, model_4
from models.routing.common.data_loader import parse_dataset
from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.task_builder import build_tasks_for_model

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def problem():
    nodes, _ = parse_dataset(str(
        ROOT / "data/zhou_later_pickup/small/standard_same_day/Set1_7-2-1b.csv"))
    fleet = json.loads((ROOT / "config/routing/validation/zhou_small.json").read_text())
    result = ProblemInstance(nodes, fleet, speed=fleet.get("speed", 1.0))
    result.aggregate_demands_from_pre_assignment()
    return result


@pytest.mark.parametrize("module", [model_2, model_4])
def test_fixed_moves_preserve_current_assignment_after_rejected_partial_state(problem, module):
    evaluator = module.Evaluator(problem, vehicles_per_satellite=problem.fleet.e2_per_sat_count)
    solution = module.build_initial_solution(problem, evaluator)
    assignment = evaluator.assignment(solution)
    original_paths = [r.full_path() for rs in solution.e2_routes.values() for r in rs]
    hooks = (model_2.M2Hooks if module is model_2 else model_4.M4Hooks)(1.0)
    for destroy_idx in range(6):
        for repair_idx in range(4):
            random.seed(100 * destroy_idx + repair_idx)
            rejected = solution.copy()
            module._remove_customer(rejected, next(iter(assignment)))
            evaluator.rebuild_from_solution(rejected)
            candidate = solution.copy()
            hooks.apply_operators(candidate, evaluator, None, None,
                                  destroy_idx, repair_idx, 3, "e2")
            assert evaluator.assignment(candidate) == assignment
            cost, feasible = evaluator.evaluate(candidate)
            fresh = module.Evaluator(problem, vehicles_per_satellite=problem.fleet.e2_per_sat_count)
            fresh_cost, fresh_feasible = fresh.evaluate(candidate.copy())
            assert cost == pytest.approx(fresh_cost)
            assert feasible == fresh_feasible
    assert hooks.neighborhood_attempts == {"fixed_routes": 24, "assignment": 0}
    assert original_paths == [r.full_path() for rs in solution.e2_routes.values() for r in rs]


def test_assignment_cache_reuses_tasks_but_rechecks_route_schedule(problem):
    evaluator = model_4.Evaluator(problem, vehicles_per_satellite=problem.fleet.e2_per_sat_count)
    solution = model_4.build_initial_solution(problem, evaluator)
    evaluator.evaluate(solution)
    tasks = evaluator.e1_task_map
    changed = solution.copy()
    for routes in changed.e2_routes.values():
        for route in routes:
            route.nodes.reverse()
    cost, feasible = evaluator.evaluate(changed)
    assert evaluator.e1_task_map is tasks
    fresh = model_4.Evaluator(problem, vehicles_per_satellite=problem.fleet.e2_per_sat_count)
    reference = changed.copy()
    reference_cost, reference_feasible = fresh.evaluate(reference)
    assert cost == pytest.approx(reference_cost)
    assert feasible == reference_feasible
    assert [r.nodes for r in changed.e1_routes] == [r.nodes for r in reference.e1_routes]


def test_m2_route_seed_keeps_incumbent_and_shares_iteration_budget(problem):
    from models.routing.common.solution_audit import audit_exact_solution
    r1,r2,assignment,info = model_2.solve_alns(problem,
        vehicles_per_satellite=problem.fleet.e2_per_sat_count,
        max_iterations=120,max_time=5,seed=42,verbose=False,return_info=True,
        route_seed_fraction=.2)
    assert info['feasible']
    assert 0 < info['route_seed_iterations'] <= info['iterations'] <= 120
    assert info['objective'] <= info['route_seed_objective']+1e-6
    assert info['route_seed_objective'] <= info['initial_objective']+1e-6
    assert audit_exact_solution(problem,2,r1,r2,assignment=assignment,
                                objective=info['objective'])['valid']


@pytest.mark.parametrize('module',[model_4])
def test_free_seed_search_respects_total_iteration_cap(problem,module):
    from models.routing.common.solution_audit import audit_exact_solution
    r1,r2,assignment,info = module.solve_alns(problem,
        vehicles_per_satellite=problem.fleet.e2_per_sat_count,
        max_iterations=120,max_time=5,seed=42,verbose=False,return_info=True,
        seed_search_fraction=.2)
    assert 0 < info['initial_seed_iterations'] <= 24
    assert info['iterations'] <= 120
    assert info['objective'] <= info['initial_objective']+1e-6
    assert audit_exact_solution(problem,4,r1,r2,assignment=assignment,
                                objective=info['objective'])['valid']
