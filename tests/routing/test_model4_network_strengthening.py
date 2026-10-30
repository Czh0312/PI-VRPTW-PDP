"""Free-assignment equivalence and compatible vehicle relabeling for M4."""
from types import SimpleNamespace
import math

import pytest

from models.common.cycle.audit import audit_solution
from models.common.cycle.state import empty_result
from models.routing.model_4.exact.formulation import build_model
from models.routing.model_4.exact.solution import extract_solution
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny


@pytest.mark.parametrize('compressed', [False, True])
@pytest.mark.parametrize('fixture', [0, 2, 4])
def test_network_model_matches_independent_free_assignment_enumeration(compressed, fixture):
    import gurobipy as gp

    problem = tiny_problem(4, fixture)
    expected = enumerate_tiny(problem)
    model, context = build_model(problem, compress_routes=compressed, network_strengthening=True)
    try:
        model.Params.Seed = 42
        model.Params.TimeLimit = 10
        model.optimize()
        assert context['network_strengthening']['resource_flows']['applied']
        if math.isinf(expected):
            assert model.Status == gp.GRB.INFEASIBLE
        else:
            assert model.Status == gp.GRB.OPTIMAL
            assert model.ObjVal == pytest.approx(expected)
            result = empty_result(problem, 'test')
            extract_solution(problem, model, context, result)
            audit = audit_solution(problem, result)
            assert audit['valid'], audit['errors']
            assert audit['periodic_replay']['valid']
    finally:
        model.dispose()


@pytest.mark.parametrize('counts,first_tasks,feasible', [
    ((2, 1), (3, 0), True),   # Primary trip count takes priority over task order.
    ((1, 1), (0, 3), True),
    ((1, 1), (3, 0), False),
])
def test_first_task_symmetry_only_breaks_equal_trip_count_ties(counts, first_tasks, feasible):
    import gurobipy as gp
    from models.routing.model_4.exact.network_strengthening import add_vehicle_symmetry

    model = gp.Model('compatible_vehicle_keys')
    model.Params.OutputFlag = 0
    routes = []
    try:
        for vehicle, (count, first_task) in enumerate(zip(counts, first_tasks)):
            for slot in range(2):
                used = model.addVar(lb=int(slot < count), ub=int(slot < count))
                source = model.addVars([(-1, i) for i in range(4)])
                for (_, task), variable in source.items():
                    variable.LB = variable.UB = int(slot == 0 and task == first_task)
                routes.append(dict(satellite='S', vehicle=vehicle, slot=slot,
                                   used=used, x=source, nodes=[{}]*4))
        add_vehicle_symmetry(SimpleNamespace(model=model, routes={'e2': routes}), check_budget=lambda: None)
        model.optimize()
        assert model.Status == (gp.GRB.OPTIMAL if feasible else gp.GRB.INFEASIBLE)
    finally:
        model.dispose()


def test_public_default_and_original_mode_record_the_actual_formulation():
    from models.routing.model_4.exact import solve_exact
    from tests.routing.test_single_batch_comparison import problem

    original = solve_exact(problem(4), time_limit=10, network_strengthening=False)
    network = solve_exact(problem(4), time_limit=10)
    for result in (original, network):
        assert result['info']['proven_optimal']
        assert result['audit']['valid']
        assert result['info']['objective'] == pytest.approx(4.)
        assert result['assignment'] == {'C': 'T'}
    assert original['info']['solver_config']['formulation'] == 'expanded'
    assert network['info']['solver_config']['formulation'] == 'compact'
    assert not original['info']['solver_config']['network_strengthening']['applied']
    assert network['info']['solver_config']['network_strengthening']['resource_flows']['applied']
