"""Exact-decomposition contract tests for Model 7."""

import pytest

from models.routing.common.domain.instance import ProblemInstance
from models.routing.common.domain.node import Customer, Depot, Satellite
from models.routing.variants.model_7 import exact_solver


@pytest.fixture(autouse=True)
def stub_real_relaxation_probe(monkeypatch):
    # Decomposition contract tests do not depend on a licensed optimizer.
    monkeypatch.setattr(exact_solver, "_partition_relaxation_screen", lambda *a, **kw: None)


def test_partition_screen_cutoff_skips_compact_mip(monkeypatch):
    probe = {"status": "CUTOFF", "pruned": True, "best_bound": 10.0, "runtime": 0.01}
    monkeypatch.setattr(exact_solver, "_partition_relaxation_screen", lambda *a, **kw: probe)
    monkeypatch.setattr(exact_solver, "_solve_fixed_assignment",
                        lambda *a, **kw: pytest.fail("certified partition reached compact MIP"))
    e1, e2, info = exact_solver._solve_partition(None, [], {}, 2,
        screen=True, incumbent=10.0, time_limit=5.0)
    assert e1 is e2 is None
    assert info["status"] == "CUTOFF"
    assert not info["proven_optimal"]
    assert info["objective"] is None


def test_unresolved_screen_preserves_bound_and_spends_residual_budget(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(exact_solver.time, "perf_counter", lambda: clock[0])

    def probe(*args, **kwargs):
        assert kwargs["cutoff"] == 10.0 - 1e-9
        clock[0] = 0.7
        return {"status": "UNRESOLVED", "pruned": False, "best_bound": 7.0, "runtime": 0.7}

    def compact(*args, **kwargs):
        assert kwargs["time_limit"] == pytest.approx(4.3)
        assert kwargs["use_relaxation_certificate"] is False
        return None, None, {"status": "TIME_LIMIT", "objective": None, "best_bound": 3.0}

    monkeypatch.setattr(exact_solver, "_partition_relaxation_screen", probe)
    monkeypatch.setattr(exact_solver, "_solve_fixed_assignment", compact)
    _, _, info = exact_solver._solve_partition(None, [], {}, 2,
        screen=True, incumbent=10.0, time_limit=5.0)
    assert info["status"] == "TIME_LIMIT"
    assert info["best_bound"] == 7.0


def test_partition_screen_can_certify_global_incumbent(monkeypatch):
    monkeypatch.setattr(exact_solver, "_partition_relaxation_screen", lambda *a, **kw:
        {"status": "CUTOFF", "pruned": True, "best_bound": 8.0, "runtime": 0.01})
    monkeypatch.setattr(exact_solver, "_solve_fixed_assignment",
                        lambda *a, **kw: pytest.fail("unexpected full MIP"))
    _, _, _, info = exact_solver.solve_exact(_problem(), vehicles_per_satellite=2,
        time_limit=None, verbose=False, return_info=True,
        use_global_relaxation_certificate=False,
        warm_start_routes=([["D0", "S0d", "S0p", "D0"]], {"S0": [], "S1": []}),
        warm_start_assignment={"C0": "S0", "C1": "S1"}, warm_start_objective=8.0)
    assert info["proven_optimal"]
    assert info["partition_relaxation_pruned"] == 4


def test_external_incumbent_prefers_small_partition_sweep(monkeypatch):
    monkeypatch.setattr(
        exact_solver,
        "_global_relaxation_certificate",
        lambda *a, **kw: pytest.fail("small warm-started sweep used global certificate"),
    )
    monkeypatch.setattr(exact_solver, "_partition_relaxation_screen", lambda *a, **kw:
        {"status": "CUTOFF", "pruned": True, "best_bound": 8.0, "runtime": 0.01})
    warm = ([['D0', 'S0d', 'S0p', 'D0']], {"S0": [], "S1": []})
    _, _, _, info = exact_solver.solve_exact(
        _problem(), vehicles_per_satellite=2, time_limit=1.0, verbose=False,
        return_info=True, warm_start_routes=warm,
        warm_start_assignment={"C0": "S0", "C1": "S1"},
        warm_start_objective=8.0,
    )
    assert info["proven_optimal"]
    assert info["global_relaxation_certificate_attempted"] is False
    assert info["proof_time_strategy"] == "fixed_assignment_relaxation_sweep"


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


def test_model7_exact_preserves_direct_m4_incumbent(monkeypatch):
    direct_routes = [["D0", "S0d", "S0p", "D0"]]
    direct_e2 = {"S0": [], "S1": []}
    direct_assignment = {"C0": "S0", "C1": "S1"}

    def fake_certificate(*args, **kwargs):
        return {
            "routes_e1": direct_routes,
            "routes_e2": direct_e2,
            "assignment": direct_assignment,
            "objective": 9.0,
            "lower_bound": 4.0,
            "certified": False,
            "direct_info": {"status": "TIME_LIMIT", "objective": 9.0},
            "lower_info": {"status": "TIME_LIMIT", "best_bound": 4.0},
        }

    def fake_unresolved(problem, e1_tasks, e2_tasks, vehicles, **kwargs):
        return [["D0", "S1d", "D0"]], {"S0": [], "S1": []}, {
            "status": "TIME_LIMIT",
            "proven_optimal": False,
            "objective": 12.0,
            "best_bound": 7.0,
            "runtime": 0.01,
            "transshipments": [],
            "transshipment_count": 0,
        }

    monkeypatch.setattr(
        exact_solver, "_global_relaxation_certificate", fake_certificate)
    monkeypatch.setattr(
        exact_solver, "_solve_fixed_assignment", fake_unresolved)
    problem = _problem()
    e1, e2, assignment, info = exact_solver.solve_exact(
        problem,
        vehicles_per_satellite=2,
        time_limit=1.0,
        verbose=False,
        return_info=True,
        return_incumbent_on_time_limit=True,
    )

    assert e1 == direct_routes
    assert e2 == direct_e2
    assert assignment == direct_assignment
    assert info["objective"] == 9.0
    assert info["best_bound"] == 7.0
    assert info["proven_optimal"] is False
    # Finite per-partition slices continue after an unresolved partition.
    assert info["assignment_partitions_evaluated"] == 4


def test_model7_exact_preserves_external_m4_incumbent(monkeypatch):
    warm_e1 = [["D0", "S0d", "S0p", "D0"]]
    warm_e2 = {"S0": [], "S1": []}
    warm_assignment = {"C0": "S0", "C1": "S1"}

    def fake_unresolved(problem, e1_tasks, e2_tasks, vehicles, **kwargs):
        return [["D0", "S1d", "D0"]], {"S0": [], "S1": []}, {
            "status": "TIME_LIMIT",
            "proven_optimal": False,
            "objective": 12.0,
            "best_bound": 7.0,
            "runtime": 0.01,
            "transshipments": [],
            "transshipment_count": 0,
        }

    monkeypatch.setattr(
        exact_solver, "_solve_fixed_assignment", fake_unresolved)
    problem = _problem()
    e1, e2, assignment, info = exact_solver.solve_exact(
        problem,
        vehicles_per_satellite=2,
        time_limit=None,
        verbose=False,
        return_info=True,
        return_incumbent_on_time_limit=True,
        use_global_relaxation_certificate=False,
        warm_start_routes=(warm_e1, warm_e2),
        warm_start_assignment=warm_assignment,
        warm_start_objective=8.0,
    )

    assert e1 == warm_e1
    assert e2 == warm_e2
    assert assignment == warm_assignment
    assert info["objective"] == 8.0
    assert info["external_m4_incumbent"] is True
    assert info["incumbent_source"] == "external_m4"


def test_model7_exact_rejects_incomplete_external_warm_start():
    problem = _problem()
    try:
        exact_solver.solve_exact(
            problem,
            vehicles_per_satellite=2,
            time_limit=None,
            verbose=False,
            warm_start_routes=([], {}),
            use_global_relaxation_certificate=False,
        )
    except ValueError as exc:
        assert "requires routes, assignment, and objective" in str(exc)
    else:
        raise AssertionError("incomplete Model 7 warm start was accepted")


def test_model7_exact_treats_cutoff_as_resolved_nonimproving_partition(
        monkeypatch):
    seen_cutoffs = []

    def fake_cutoff(problem, e1_tasks, e2_tasks, vehicles, **kwargs):
        seen_cutoffs.append(kwargs["gurobi_params"]["Cutoff"])
        return None, None, {
            "status": "CUTOFF",
            "proven_optimal": False,
            "objective": None,
            "best_bound": 8.0,
            "runtime": 0.01,
            "transshipment_count": 0,
        }

    monkeypatch.setattr(exact_solver, "_solve_fixed_assignment", fake_cutoff)
    problem = _problem()
    warm_e1 = [["D0", "S0d", "S0p", "D0"]]
    warm_e2 = {"S0": [], "S1": []}
    _e1, _e2, _assignment, info = exact_solver.solve_exact(
        problem,
        vehicles_per_satellite=2,
        time_limit=None,
        verbose=False,
        return_info=True,
        return_incumbent_on_time_limit=True,
        use_global_relaxation_certificate=False,
        warm_start_routes=(warm_e1, warm_e2),
        warm_start_assignment={"C0": "S0", "C1": "S1"},
        warm_start_objective=8.0,
    )

    assert info["proven_optimal"] is True
    assert info["assignment_partitions_resolved"] == 4
    assert seen_cutoffs == [8.0 - 1e-9] * 4


def test_model7_exact_prunes_direct_delivery_due_conflicts(monkeypatch):
    problem = _problem()
    for customer in problem.customers:
        customer.tw_del = (0.0, 0.1)

    def unexpected_solver(*args, **kwargs):
        raise AssertionError("presolve-conflicting partition reached Gurobi")

    monkeypatch.setattr(
        exact_solver, "_solve_fixed_assignment", unexpected_solver)
    _e1, _e2, _assignment, info = exact_solver.solve_exact(
        problem,
        vehicles_per_satellite=2,
        time_limit=None,
        verbose=False,
        return_info=True,
        use_global_relaxation_certificate=False,
    )

    assert info["status"] == "INFEASIBLE"
    assert info["assignment_partitions_evaluated"] == 4
    assert info["assignment_partitions_proven_infeasible"] == 4
    assert all("presolve_certificate" in item
               for item in info["partition_summaries"])


def test_model7_exact_retries_unresolved_partitions_with_residual_budget(
        monkeypatch):
    calls = {}

    def fake_then_cutoff(problem, e1_tasks, e2_tasks, vehicles, **kwargs):
        assignment = _assignment_from_tasks(e2_tasks)
        key = tuple(sorted(assignment.items()))
        calls[key] = calls.get(key, 0) + 1
        needs_third_attempt = all(
            sat_id == "S0" for _customer, sat_id in key)
        if calls[key] == 1 or (needs_third_attempt and calls[key] == 2):
            return None, None, {
                "status": "TIME_LIMIT",
                "proven_optimal": False,
                "objective": None,
                "best_bound": 6.0,
                "runtime": 0.01,
                "transshipment_count": 0,
            }
        return None, None, {
            "status": "CUTOFF",
            "proven_optimal": False,
            "objective": None,
            "best_bound": 8.0,
            "runtime": 0.01,
            "transshipment_count": 0,
        }

    monkeypatch.setattr(
        exact_solver, "_solve_fixed_assignment", fake_then_cutoff)
    problem = _problem()
    warm_e1 = [["D0", "S0d", "S0p", "D0"]]
    warm_e2 = {"S0": [], "S1": []}
    _e1, _e2, _assignment, info = exact_solver.solve_exact(
        problem,
        vehicles_per_satellite=2,
        time_limit=1.0,
        verbose=False,
        return_info=True,
        return_incumbent_on_time_limit=True,
        use_global_relaxation_certificate=False,
        warm_start_routes=(warm_e1, warm_e2),
        warm_start_assignment={"C0": "S0", "C1": "S1"},
        warm_start_objective=8.0,
    )

    assert info["status"] == "OPTIMAL"
    assert info["assignment_partitions_evaluated"] == 4
    assert info["assignment_partitions_resolved"] == 4
    assert info["assignment_partition_retries"] == 5
    assert sorted(calls.values()) == [2, 2, 2, 3]
