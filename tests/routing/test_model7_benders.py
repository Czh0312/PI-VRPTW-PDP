"""Benders formulation, route equivalence, and termination regressions."""
import copy

import pytest

gp = pytest.importorskip("gurobipy")
try:
    with gp.Env(empty=True) as env:
        env.setParam("OutputFlag", 0)
        env.start()
except gp.GurobiError as exc:
    pytest.skip(f"Gurobi license unavailable: {exc}", allow_module_level=True)

from models.routing.common.solution_audit import audit_exact_solution
from models.routing.variants.model_7 import benders, exact_solver
from models.routing.variants.model_7.model import iter_assignments
from tests.routing.test_model7_exact import _problem


@pytest.fixture(scope="module")
def comparison():
    problem = _problem()
    options = dict(time_limit=30, verbose=False, return_info=True,
                   return_incumbent_on_time_limit=True, gurobi_params={"Threads": 1})
    baseline = exact_solver.solve_exact(
        problem, use_global_relaxation_certificate=False,
        partition_relaxation_screen=False, **options)
    decomposed = exact_solver.solve_exact(problem, algorithm="benders", **options)
    return problem, baseline, decomposed


def test_benders_matches_exhaustive_partitioning_and_audit(comparison):
    problem, baseline, result = comparison
    assert baseline[-1]["proven_optimal"]
    info = result[-1]
    assert info["proven_optimal"]
    assert info["objective"] == pytest.approx(baseline[-1]["objective"], abs=1e-6)
    assert info["best_bound"] == pytest.approx(info["objective"], abs=1e-6)
    assert info["assignment_partitions_evaluated"] < info["assignment_partitions_total"]
    audit = audit_exact_solution(problem, 4, result[0], result[1],
                                  assignment=result[2], objective=info["objective"])
    assert audit["valid"], audit
    assert {c.id: c.assigned_sat for c in problem.customers} == {"C0": "S0", "C1": "S1"}


def test_master_is_lower_bound_for_every_fixed_assignment():
    problem = _problem()
    with gp.Env(empty=True) as env:
        env.setParam("OutputFlag", 0)
        env.start()
        master, w, _ = benders.build_master(problem, env=env)
        try:
            for assignment in iter_assignments(problem):
                for (c, s), var in w.items():
                    var.LB = var.UB = float(assignment[c] == s)
                master.optimize()
                assert master.ObjVal >= 24.0 - 1e-6
                _, _, info = benders._solve_subproblem(
                    problem, assignment, 2, TFp=9999.0, time_limit=10,
                    verbose=False, return_info=True, mip_gap=0.0,
                    gurobi_params={"Threads": 1}, compute_iis_on_infeasible=False)
                assert info["proven_optimal"]
                assert master.ObjVal <= info["objective"] + 1e-6
        finally:
            master.dispose()


def test_time_window_conflicts_prove_infeasibility_without_subproblems(monkeypatch):
    problem = _problem()
    for c in problem.customers:
        c.tw_del = (0.0, 0.1)
    monkeypatch.setattr(benders, "_solve_subproblem", lambda *a, **kw: pytest.fail("unnecessary subproblem"))
    *routes, info = benders.solve_benders(problem, verbose=False, return_info=True)
    assert routes == [None, None, None]
    assert info["status"] == "INFEASIBLE"
    assert not info["proven_optimal"]
    assert info["iterations"] == 0


def test_zero_budget_has_no_false_certificate(monkeypatch):
    monkeypatch.setattr(benders, "build_master", lambda *a, **kw: pytest.fail("zero budget built model"))
    *routes, info = benders.solve_benders(_problem(), time_limit=0, return_info=True, verbose=False)
    assert routes == [None, None, None]
    assert info["status"] == "TIME_LIMIT"
    assert info["best_bound"] == 0.0
    assert not info["proven_optimal"]


def test_benders_can_use_complete_compact_m6_without_certificates(comparison):
    problem, baseline, _ = comparison
    _, _, _, info = benders.solve_benders(
        problem, time_limit=10, return_info=True, verbose=False,
        use_relaxation_certificate=False, partition_relaxation_screen=False,
        gurobi_params={"Threads": 1})
    assert info["proven_optimal"]
    assert info["objective"] == pytest.approx(baseline[-1]["objective"], abs=1e-6)
    assert info["route_vehicle_indices"]


def test_unresolved_subproblem_does_not_generate_feasibility_cut(monkeypatch):
    monkeypatch.setattr(benders, "_solve_subproblem", lambda *a, **kw: (
        None, None, {"status": "TIME_LIMIT", "objective": None, "best_bound": 12.0}))
    *_, info = benders.solve_benders(_problem(), max_iterations=2, return_info=True, verbose=False)
    assert info["status"] == "ITERATION_LIMIT"
    assert not info["proven_optimal"]
    assert info["objective"] is None
    assert info["feasibility_cuts"] == 0
    assert info["optimality_cuts"] == 2


def test_infeasible_subproblem_cuts_exhaust_assignment_space(monkeypatch):
    monkeypatch.setattr(benders, "_solve_subproblem", lambda *a, **kw: (
        None, None, {"status": "INFEASIBLE", "objective": None, "best_bound": None}))
    *_, info = benders.solve_benders(_problem(), return_info=True, verbose=False)
    assert info["status"] == "INFEASIBLE"
    assert info["feasibility_cuts"] == 4
    assert info["objective"] is None


def test_warm_start_is_audited_and_retained_when_time_expires(comparison):
    problem, _, result = comparison
    r1, r2, assignment, source = result
    _, _, _, info = benders.solve_benders(
        problem, time_limit=0, verbose=False, return_info=True,
        warm_start_routes=(r1, r2), warm_start_assignment=assignment,
        warm_start_objective=source["objective"])
    assert info["objective"] == source["objective"]
    assert not info["proven_optimal"]
    corrupted = copy.deepcopy(r2)
    corrupted["S0"][0].pop(1)
    with pytest.raises(ValueError, match="audit"):
        benders.solve_benders(
            problem, time_limit=0, verbose=False,
            warm_start_routes=(r1, corrupted), warm_start_assignment=assignment,
            warm_start_objective=source["objective"])


def test_metric_closure_handles_directed_nonmetric_distances():
    problem = _problem()
    a, b = problem.node_index["D0"], problem.node_index["C0"]
    problem.dist[a][b] = 100.0
    closure = benders._shortest_paths(problem, problem.distance)
    assert closure["D0", "C0"] == pytest.approx(6.0)
    assert closure["C0", "D0"] == pytest.approx(6.0)
    assert problem.distance("D0", "C0") == 100.0


@pytest.mark.parametrize("options", [
    {"time_limit": -1}, {"subproblem_time_limit": 0}, {"max_iterations": 0},
    {"absolute_tolerance": -1}, {"gurobi_params": {"Cutoff": 1}},
    {"gurobi_params": {"MIPGap": 0.1}},
])
def test_invalid_controls_rejected(options):
    with pytest.raises(ValueError):
        benders.solve_benders(_problem(), verbose=False, **options)


def test_service_dispatches_benders_and_rejects_unsupported_models(monkeypatch):
    from models.routing import service

    captured = {}

    def fake_solver(**kwargs):
        captured.update(kwargs)
        return "result"

    monkeypatch.setattr(service, "_load_solver", lambda *args: fake_solver)
    args = dict(model=7, method="exact", problem=_problem(), e1_tasks=[], e2_tasks_by_sat={},
                exact_algorithm="benders", exact_time=2.0,
                benders_options={"subproblem_time_limit": 0.5})
    assert service.solve_routing(**args) == "result"
    assert captured["algorithm"] == "benders"
    assert captured["time_limit"] == 2.0
    assert captured["benders_options"] == {"subproblem_time_limit": 0.5}
    with pytest.raises(ValueError, match="Model 7"):
        service.solve_routing(**{**args, "model": 6})
