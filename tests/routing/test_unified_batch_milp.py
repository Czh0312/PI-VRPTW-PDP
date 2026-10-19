"""Equivalence and one-call proof boundaries of the unified batch-count MILP."""
from dataclasses import replace
from importlib import import_module
import json

import gurobipy as gp
import pytest

from models.common.cycle.audit import audit_solution
from models.common.gurobi_disjunction import append_block
from tests.routing.test_model6_uniform import uniform_fixture
from tests.routing.test_model78_calendar import calendar_fixture
from tests.routing.test_model6_joint import joint_fixture


def instance(number, **kwargs):
    return uniform_fixture(**kwargs) if number == 6 else calendar_fixture(number, **kwargs)


@pytest.mark.parametrize('number', [6, 7, 8])
def test_all_counts_use_one_optimize_without_external_search(number, monkeypatch):
    package = f'models.routing.model_{number}.exact'
    def forbidden(*args, **kwargs):
        raise AssertionError('Direct solve must not use a K scheduler or lower certificate')
    bounds = import_module('models.common.cycle.pool_bound')
    monkeypatch.setattr(bounds, 'necessary_pool_lower_bound', forbidden)
    monkeypatch.setattr(bounds, 'necessary_cost_lower_bound', forbidden)
    original = gp.Model.optimize
    calls = []
    def optimize(model, *args, **kwargs):
        assert not args and not kwargs
        assert model.NumStart == 0
        assert model.Params.Presolve == -1 and model.Params.Cuts == -1
        assert model.Params.Heuristics == pytest.approx(.05)
        assert model.Params.MIPFocus == 0
        assert model.getVarByName('batch_count') is not None
        calls.append(model.ModelName)
        return original(model)
    monkeypatch.setattr(gp.Model, 'optimize', optimize)
    p = instance(number, counts=(1, 2, 3))  # K=3 cannot serve three nonempty batches with two orders.
    result = import_module(package).solve_exact(p, time_limit=15)
    assert len(calls) == 1
    assert result['info']['proven_optimal'] and audit_solution(p, result)['valid']
    assert result['info']['batch_count_candidates'] == [1, 2, 3]
    assert result['info']['best_bound'] == pytest.approx(result['info']['objective'])
    assert result['info']['gurobi_best_bound'] == pytest.approx(result['info']['best_bound'])
    assert result['info']['solver_config']['external_certificate'] is False
    assert result['info']['solver_config']['retained_model_bounds'] is False
    assert 'count_search' not in result
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('number', [6, 7, 8])
def test_unified_objective_equals_minimum_of_fixed_count_milps(number):
    p = instance(number, counts=(1, 2, 3))
    base = f'models.routing.model_{number}.exact'
    costs = []
    for k in p.batch_count_candidates:
        result = import_module(base+'.milp_solver').solve_milp(p.for_count(k), time_limit=10)
        assert result['info']['proven_optimal'] or result['info']['proven_infeasible']
        if result['info']['proven_optimal']:
            costs.append(result['info']['objective'])
    unified = import_module(base).solve_exact(p, time_limit=15)
    assert unified['info']['proven_optimal']
    assert unified['info']['objective'] == pytest.approx(min(costs))


@pytest.mark.parametrize('number', [6, 7, 8])
def test_all_infeasible_counts_prove_the_full_problem_infeasible(number):
    result = import_module(f'models.routing.model_{number}.exact').solve_exact(
        instance(number, counts=(3, 4)), time_limit=10)
    assert result['info']['proven_infeasible'] and not result['info']['proven_optimal']
    assert result['info']['complete_count_domain']
    assert result['info']['objective'] is None


@pytest.mark.parametrize('number', [6, 7])
def test_nondivisible_clocks_retain_physical_cost_and_audit(number):
    from models.common.cycle.uniform import UniformCycleInstance
    from models.common.cycle.calendar import CalendarCycleInstance
    source = joint_fixture(pool_rate=30)
    policy = replace(source.operating_policy, cycle_end=131)
    if number == 6:
        p = UniformCycleInstance(source.network, source.orders, policy,
            dict(schedule_mode='uniform_cycle', batch_count_candidates=[1, 2]), source.cost_profile.config)
    else:
        p = CalendarCycleInstance(source.network, source.orders, policy,
            dict(batch_count_candidates=[1, 2]), source.cost_profile.config, model=7)
    assert p.for_count(2).operating_policy.time_tick_subdivision == 2
    package = f'models.routing.model_{number}.exact'
    expected = [import_module(package+'.milp_solver').solve_milp(p.for_count(k), time_limit=10)
                for k in (1, 2)]
    assert all(r['info']['proven_optimal'] for r in expected)
    result = import_module(package).solve_exact(p, time_limit=15)
    assert result['info']['proven_optimal'] and audit_solution(p, result)['valid']
    assert result['info']['objective'] == pytest.approx(min(r['info']['objective'] for r in expected))


def test_public_builder_exposes_one_hot_count_and_pool_decisions():
    p = calendar_fixture(8)
    model, ctx = import_module('models.routing.model_8.exact.model').build_model(p)
    try:
        assert set(ctx['selectors']) == set(p.batch_count_candidates)
        assert ctx['batch_count'].VarName == 'batch_count'
        assert ctx['pool'].VarName == 'container_pool'
        model.Params.OutputFlag = 0
        model.optimize()
        chosen = round(ctx['batch_count'].X)
        assert sum(v.X for v in ctx['selectors'].values()) == pytest.approx(1)
        for k, block in ctx['blocks'].items():
            assert block['context']['variables'].model is model
            if k != chosen:
                assert block['context']['pool'].X == pytest.approx(0)
                assert all(v.X == pytest.approx(0) for v in block['context']['assignments'].values())
    finally:
        model.dispose()


def test_disjunction_scales_constants_signed_bounds_and_zero_trigger_indicators():
    master = gp.Model(); master.Params.OutputFlag = 0
    selectors = master.addVars(2, vtype=gp.GRB.BINARY)
    master.addConstr(selectors.sum() == 1)
    costs = []
    try:
        for k in range(2):
            block = gp.Model(); block.Params.OutputFlag = 0
            try:
                x = block.addVar(lb=-3, ub=-1)
                trigger = block.addVar(vtype=gp.GRB.BINARY)
                block.addConstr(trigger == 0)
                block.addGenConstrIndicator(trigger, 0, x >= -2)
                block.addGenConstrIndicator(trigger, 1, x <= -3)
                # The second block is infeasible when selected, but must be harmless when off.
                if k == 1:
                    block.addConstr(x >= 0)
                block.setObjective(x + 7 + k)
                _, cost = append_block(master, block, {'x': x}, selectors[k], prefix=f'k{k}_')
                costs.append(cost)
            finally:
                block.dispose()
        master.setObjective(gp.quicksum(costs))
        master.optimize()
        assert master.Status == gp.GRB.OPTIMAL
        assert selectors[0].X == pytest.approx(1)
        assert master.ObjVal == pytest.approx(5)
    finally:
        master.dispose()


@pytest.mark.parametrize('number', range(1, 9))
def test_auto_alias_now_uses_direct_gurobi(number, monkeypatch):
    from tests.routing.test_milp_baseline import instance as basic_instance
    package = f'models.routing.model_{number}.exact'
    result = import_module(package).solve_exact(basic_instance(number), strategy='auto', time_limit=15)
    assert result['info']['proven_optimal']
    assert result['info']['exact_strategy_requested'] == 'auto'
    assert result['info']['exact_strategy'].startswith('gurobi_')
