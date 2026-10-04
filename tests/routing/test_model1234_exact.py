"""Certified exact and independent route-audit regressions for Models 1--4."""

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

gp = pytest.importorskip("gurobipy", reason="exact tests require Gurobi")
try:
    _license_probe = gp.Env(empty=True)
    _license_probe.setParam("OutputFlag", 0)
    _license_probe.start()
    _license_probe.dispose()
except gp.GurobiError as exc:
    pytest.skip(f"Gurobi license unavailable: {exc}", allow_module_level=True)

from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Customer, Depot, Satellite
from models.routing.common.solution_audit import audit_exact_solution
from models.routing.common.task_builder import build_tasks_for_model
from models.routing.baselines.variants.model_1.exact_solver import solve_exact as solve_m1
from models.routing.baselines.variants.model_2.exact_solver import solve_exact as solve_m2
from models.routing.baselines.variants.model_3.exact_solver import solve_exact as solve_m3
from models.routing.baselines.variants.model_4.exact_solver import solve_exact as solve_m4


TOL = 1e-6


def _problem():
    nodes = {
        "D0": Depot("D0", 0.0, 0.0),
        "S0": Satellite("S0", 8.0, 0.0, service_time=0.5),
        "S1": Satellite("S1", -8.0, 0.0, service_time=0.5),
        "C0": Customer(
            "C0", 9.0, 1.0, demand=3.0, service_time=0.5,
            tw_del=(10.0, 70.0), tw_pick=(25.0, 120.0),
            assigned_sat="S0",
        ),
        "C1": Customer(
            "C1", -9.0, -1.0, demand=4.0, service_time=0.5,
            tw_del=(10.0, 70.0), tw_pick=(25.0, 120.0),
            assigned_sat="S1",
        ),
    }
    fleet = {
        "E1_vehicle_count": 1,
        "E1_Qk": 50.0,
        "E1_Ck": 20.0,
        "vehicles_per_satellite": 2,
        "E2_Qv": 20.0,
        "E2_Cv": 10.0,
        "container_weight": 2.0,
        "container_volume": 1.0,
        "speed": 1.0,
    }
    problem = ProblemInstance(nodes, fleet, speed=1.0)
    problem.aggregate_demands_from_pre_assignment()
    return problem


@pytest.fixture(scope="module")
def exact_results():
    problem = _problem()
    e1_m1, e2_m1 = build_tasks_for_model(problem, 1)
    routes1, routes2_1, info1 = solve_m1(
        problem, e1_m1, e2_m1, problem.fleet.e2_per_sat_count,
        time_limit=30, verbose=False, return_info=True,
    )

    routes2_e1, routes2_e2, assignment2, info2 = solve_m2(
        problem, problem.fleet.e2_per_sat_count,
        time_limit=30, verbose=False, return_info=True,
    )

    e1_m3, e2_m3 = build_tasks_for_model(problem, 3)
    routes3_e1, routes3_e2, info3 = solve_m3(
        problem, e1_m3, e2_m3, problem.fleet.e2_per_sat_count,
        TFp=9999.0, time_limit=30, verbose=False, return_info=True,
    )

    routes4_e1, routes4_e2, assignment4, info4 = solve_m4(
        problem, problem.fleet.e2_per_sat_count, TFp=9999.0,
        time_limit=30, verbose=False, return_info=True,
        warm_start_routes=(routes3_e1, routes3_e2),
    )
    return problem, {
        1: (routes1, routes2_1, None, info1),
        2: (routes2_e1, routes2_e2, assignment2, info2),
        3: (routes3_e1, routes3_e2, None, info3),
        4: (routes4_e1, routes4_e2, assignment4, info4),
    }


def test_models_1_to_4_are_certified_and_route_audited(exact_results):
    problem, results = exact_results
    for model, (e1_routes, e2_routes, assignment, info) in results.items():
        assert info["status"] == "OPTIMAL", (model, info)
        assert info["proven_optimal"] is True, (model, info)
        assert abs(info["mip_gap"]) <= TOL, (model, info)
        audit = audit_exact_solution(
            problem, model, e1_routes, e2_routes,
            assignment=assignment, objective=info["objective"], tfp=9999.0,
        )
        assert audit["valid"] is True, (model, audit)
        assert abs(audit["objective_delta"]) <= TOL, (model, audit)


def test_free_assignment_models_dominate_fixed_assignment(exact_results):
    _problem_instance, results = exact_results
    objectives = {model: result[3]["objective"]
                  for model, result in results.items()}
    assert objectives[2] <= objectives[1] + TOL, objectives
    assert objectives[4] <= objectives[3] + TOL, objectives


def test_route_audit_rejects_corrupted_extraction(exact_results):
    problem, results = exact_results
    e1_routes, e2_routes, _assignment, info = results[1]
    corrupted_e2 = {
        satellite: [list(route) for route in routes]
        for satellite, routes in e2_routes.items()
    }
    satellite = next(s for s, routes in corrupted_e2.items() if routes)
    corrupted_e2[satellite][0].insert(1, corrupted_e2[satellite][0][1])
    audit = audit_exact_solution(
        problem, 1, e1_routes, corrupted_e2, objective=info["objective"],
    )
    assert audit["valid"] is False, audit
    assert any("repeats tasks" in error or "exactly once" in error
               for error in audit["errors"]), audit
