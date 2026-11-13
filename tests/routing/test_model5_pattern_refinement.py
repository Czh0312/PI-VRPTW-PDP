"""Pool-dependent pattern cuts must preserve solutions at every other pool."""
import gurobipy as gp
import pytest

from experiments.runs.model5_pattern_refinement import (
    add_pool_choices, pattern_mismatch, pattern_signature, pool_interval)


def native_model():
    model = gp.Model('pool_pattern_validation')
    model.Params.OutputFlag = 0
    model.Params.Threads, model.Params.Seed = 1, 42
    model.Params.MIPGap = model.Params.MIPGapAbs = 0
    model.Params.TimeLimit = 15
    return model


@pytest.mark.parametrize('pool_value', [2, 3, 4])
def test_exact_pool_encoding_keeps_every_certified_integer(pool_value):
    model = native_model()
    try:
        pool = model.addVar(lb=1, ub=7, vtype=gp.GRB.INTEGER)
        choices = add_pool_choices(model, pool, (2, 4))
        model.addConstr(pool == pool_value)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
        assert [n for n, var in choices.items() if var.X > .5] == [pool_value]
    finally:
        model.dispose()


@pytest.mark.parametrize('pool_value,expected', [(1, 9.), (2, 0.)])
def test_conditional_cost_bound_does_not_leak_to_another_pool(pool_value, expected):
    model = native_model()
    try:
        pool = model.addVar(lb=1, ub=2, vtype=gp.GRB.INTEGER)
        choices = add_pool_choices(model, pool, (1, 2))
        same_route = model.addVar(vtype=gp.GRB.BINARY, lb=1)
        eta = model.addVar(lb=0)
        model.addConstr(pool == pool_value)
        mismatch = pattern_mismatch([same_route], pool_choice=choices[1])
        model.addConstr(eta >= 9 - 9 * mismatch)
        model.setObjective(eta)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL and model.ObjVal == pytest.approx(expected)
    finally:
        model.dispose()


def test_proven_no_good_at_one_pool_keeps_identical_routes_at_another():
    model = native_model()
    try:
        pool = model.addVar(lb=1, ub=2, vtype=gp.GRB.INTEGER)
        choices = add_pool_choices(model, pool, (1, 2))
        same_route = model.addVar(vtype=gp.GRB.BINARY, lb=1)
        model.addConstr(pattern_mismatch([same_route], pool_choice=choices[1]) >= 1)
        model.setObjective(pool)
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL and model.ObjVal == 2
    finally:
        model.dispose()


def test_pattern_cache_identity_includes_pool():
    arguments = dict(k=2, assignments={'o': 0}, recoveries={'o': 1}, masks=[3], order_ids=['o'])
    assert pattern_signature(pool=1, **arguments) != pattern_signature(pool=2, **arguments)


@pytest.mark.parametrize('bounds', [(2, 1), (0, 2), (1, 2.5), True, 1.5])
def test_noninteger_or_empty_pool_bounds_are_rejected(bounds):
    with pytest.raises(ValueError):
        pool_interval(bounds)


def test_fixed_pool_compatibility():
    assert pool_interval(4) == pool_interval((4, 4)) == (4, 4)


@pytest.mark.parametrize('variable_pool', [False, True])
def test_tiny_full_original_problem_matches_independent_native_optimum(variable_pool):
    from experiments.runs.model5_pattern_refinement import solve
    from experiments.runs import model5_phase_bound as phase
    from experiments.runs.model5_route_bounds import add_shared_route_bounds
    from experiments.runs import model5_trip_sync as sync
    from experiments.runs.research_model5_shared_relaxation import enumerate_trip_library
    from models.common.cycle.audit import audit_solution
    from models.routing.model_5.exact.solver import solve_exact
    from tests.routing.test_model5_calendar import calendar_fixture

    initial_problem = calendar_fixture(5, counts=(1, 2), late_release=True, pool_min=2, pool_max=2)
    witness = solve_exact(initial_problem, time_limit=15, seed=42, milp_options=dict(formulation='compact'))
    assert witness['info']['proven_optimal']
    problem = calendar_fixture(5, counts=(1, 2), late_release=True,
                               pool_min=1 if variable_pool else 2, pool_max=2)
    initial_audit = audit_solution(problem, witness)
    assert initial_audit['valid'] and initial_audit['periodic_replay']['valid']
    library, _ = enumerate_trip_library(problem)
    best, info = solve(problem, witness, problem.batch_count_candidates, (1, 2) if variable_pool else 2,
        0., library, phase, 15., seed=42, trip_sync_augment=sync.augment,
        route_bound_augment=add_shared_route_bounds)
    # The independent full MILP is solved AFTER the candidate, never supplied as a cost floor.
    oracle = solve_exact(problem, time_limit=15, seed=42, milp_options=dict(formulation='compact'))
    assert oracle['info']['proven_optimal'] and info['global_optimality_certified']
    audit = audit_solution(problem, best)
    assert audit['valid'] and audit['periodic_replay']['valid']
    assert audit['objective'] == pytest.approx(oracle['info']['objective'], abs=1e-6)
    assert info['global_lower_bound'] == pytest.approx(audit['objective'], abs=1e-6)
    if variable_pool:
        assert audit['objective'] < initial_audit['objective']
        assert info['retained_N'] == [1, 2]
        assert info['events'] and all(event['pool_value_in_pattern'] for event in info['events'])
