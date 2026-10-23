"""Same optimum and physical replay under valid M4 strengthenings."""
from dataclasses import replace
from importlib import import_module

import pytest

from models.common.cycle import MultiBatchInstance
from models.common.cycle.audit import audit_solution
from models.common.cycle.state import empty_result
from tests.routing.test_single_batch_comparison import problem
from tests.routing.test_single_batch_solver_audit import nonmetric_problem


def instance(kind):
    if kind == 'nonmetric':
        source = nonmetric_problem()
        return MultiBatchInstance(source.network, source.orders, source.operating_policy, model=4)
    p = problem(4, duplicate=kind in ('duplicate', 'zero'), pool=2 if kind in ('duplicate', 'zero') else 1)
    if kind == 'unit_capacity':
        p.fleet.e2_volume_cap = 1
    if kind == 'zero':
        p.fleet.container_weight = 0
        return MultiBatchInstance(p.network, [replace(o, goods_weight=0, service_time=0) for o in p.orders],
                                  p.operating_policy, model=4)
    return p


def solve(p, profile):
    from models.routing.model_4.exact.formulation import build_model
    from models.routing.model_4.exact.solution import extract_solution
    model, context = build_model(p, compress_routes=True, strengthening=profile)
    try:
        model.Params.TimeLimit = 8
        model.optimize()
        assert model.Status == 2
        result = empty_result(p, 'test')
        result['info']['objective'] = model.ObjVal
        extract_solution(p, model, context, result)
        assert audit_solution(p, result)['valid']
        assert audit_solution(p, result)['periodic_replay']['valid']
        return model.ObjVal, context['strengthening']
    finally:
        model.dispose()


@pytest.mark.parametrize('kind', ['single', 'duplicate', 'zero', 'nonmetric', 'unit_capacity'])
@pytest.mark.parametrize('profile', ['bounds', 'capacity', 'combined', 'activation', 'capacity_activation'])
def test_strengthening_preserves_optimum_and_independent_replay(kind, profile):
    expected, _ = solve(instance(kind), 'none')
    actual, statistics = solve(instance(kind), profile)
    assert actual == pytest.approx(expected)
    if profile in ('capacity', 'combined', 'capacity_activation'):
        assert statistics['rounded_capacity_constraints'] > 0


def test_public_solver_records_requested_strengthening_and_proves_tiny_optimum():
    from models.routing.model_4.exact import solve_exact
    result = solve_exact(instance('unit_capacity'), time_limit=8, strengthening='capacity_activation',
                         milp_options={'formulation':'compact'})
    assert result['info']['proven_optimal'] and result['audit']['valid']
    profile = result['info']['solver_config']['strengthening']
    assert profile['profile'] == 'capacity_activation' and profile['activation_capacity'] > 0


def test_routing_service_passes_the_scoped_m4_option():
    from models.routing.service import solve_routing
    result = solve_routing(model=4, method='exact', problem=instance('unit_capacity'), exact_time=8,
        milp_options={'formulation':'compact'}, m4_strengthening='capacity_activation')
    assert result['info']['proven_optimal'] and result['audit']['valid']
    assert result['info']['solver_config']['strengthening']['profile'] == 'capacity_activation'
    with pytest.raises(ValueError, match='requires the current M4 exact'):
        solve_routing(model=3, method='exact', problem=problem(3), m4_strengthening='capacity')
    with pytest.raises(ValueError, match='requires the current M4 exact'):
        solve_routing(model=4, method='alns', problem=problem(4), m4_strengthening='capacity')


def test_cli_forwards_strengthening_only_for_exact_m4(monkeypatch):
    from cli import route, multibatch
    monkeypatch.setattr(route, 'load_config', lambda *a, **kw: (
        dict(model=3, solver='alns'), dict(operating_policy=dict(policy_version='multi_batch_cutoff_v1'))))
    monkeypatch.setattr(route, 'apply_pool_config', lambda config,fleet,path: (config,fleet))
    monkeypatch.setattr(multibatch, 'run_case', lambda config,fleet,**kw: config)
    config = route.main(['--model','4','--solver','exact','--m4-strengthening','capacity_activation'])
    assert config['m4_strengthening'] == 'capacity_activation'
    with pytest.raises(ValueError, match='requires M4'):
        route.main(['--model','3','--solver','exact','--m4-strengthening','capacity'])
