"""Exact-decomposition contract tests for Model 7."""

from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Customer, Depot, Satellite
from models.routing.variants.model_7 import exact_solver


def _problem():
    nodes = {
        "D0": Depot("D0", 0.0, 0.0),
        "S0": Satellite("S0", 5.0, 0.0),
        "S1": Satellite("S1", -5.0, 0.0),
        "C0": Customer(
            "C0", 6.0, 0.0, demand=1.0, service_time=0.5,
            tw_del=(0.0, 50.0), tw_pick=(10.0, 80.0),
            assigned_sat="S0",
        ),
        "C1": Customer(
            "C1", -6.0, 0.0, demand=1.0, service_time=0.5,
            tw_del=(0.0, 50.0), tw_pick=(10.0, 80.0),
            assigned_sat="S1",
        ),
    }
    fleet = {
        "E1_vehicle_count": 1,
        "E1_Qk": 20.0,
        "E1_Ck": 20.0,
        "vehicles_per_satellite": 2,
        "E2_Qv": 10.0,
        "E2_Cv": 10.0,
        "container_weight": 1.0,
        "container_volume": 1.0,
        "speed": 1.0,
    }
    problem = ProblemInstance(nodes, fleet, speed=1.0)
    problem.aggregate_demands_from_pre_assignment()
    return problem


def _assignment_from_tasks(e2_tasks):
    return {
        task.cust: sat_id
        for sat_id, tasks in e2_tasks.items()
        for task in tasks
        if task.kind == "d"
    }


def test_model7_exact_closes_every_assignment_partition(monkeypatch):
    def fake_fixed_solver(problem, e1_tasks, e2_tasks, vehicles, **kwargs):
        assignment = _assignment_from_tasks(e2_tasks)
        objective = 10.0 + sum(
            1 for sat_id in assignment.values() if sat_id == "S1"
        )
        routes_e1 = [["D0", "S0d", "S0p", "D0"]]
        routes_e2 = {sat.id: [] for sat in problem.satellites}
        return routes_e1, routes_e2, {
            "status": "OPTIMAL",
            "proven_optimal": True,
            "objective": objective,
            "best_bound": objective,
            "runtime": 0.01,
            "transshipments": [],
            "transshipment_count": 0,
            "transferred_container_count": 0,
        }

    monkeypatch.setattr(
        exact_solver, "_solve_fixed_assignment", fake_fixed_solver)
    problem = _problem()
    _e1, _e2, assignment, info = exact_solver.solve_exact(
        problem,
        vehicles_per_satellite=2,
        time_limit=None,
        verbose=False,
        return_info=True,
        use_global_relaxation_certificate=False,
    )

    assert info["proven_optimal"] is True
    assert info["status"] == "OPTIMAL"
    assert info["assignment_partitions_total"] == 4
    assert info["assignment_partitions_evaluated"] == 4
    assert info["assignment_partitions_resolved"] == 4
    assert assignment == {"C0": "S0", "C1": "S0"}
    assert info["objective"] == info["best_bound"] == 10.0


def test_model7_exact_withholds_unproven_incumbent(monkeypatch):
    def fake_unresolved(problem, e1_tasks, e2_tasks, vehicles, **kwargs):
        return [["D0", "S0d", "D0"]], {"S0": [], "S1": []}, {
            "status": "TIME_LIMIT",
            "proven_optimal": False,
            "objective": 12.0,
            "best_bound": 7.0,
            "runtime": 0.01,
            "transshipments": [],
            "transshipment_count": 0,
        }

    monkeypatch.setattr(exact_solver, "_solve_fixed_assignment", fake_unresolved)
    problem = _problem()
    e1, e2, assignment, info = exact_solver.solve_exact(
        problem,
        vehicles_per_satellite=2,
        time_limit=None,
        verbose=False,
        return_info=True,
        use_global_relaxation_certificate=False,
    )

    assert (e1, e2, assignment) == (None, None, None)
    assert info["proven_optimal"] is False
    assert info["solution_type"] == "feasible_not_proven"
    assert info["assignment_partitions_evaluated"] == 1
