"""Same-domain direct MILP comparisons and fail-closed global proof handling."""
from importlib import import_module
import json
import math

import pytest

from models.common.solver_config import MilpOptions, accept_milp_audit
from models.common.cycle.audit import audit_solution
from models.routing.service import solve_routing
from tests.routing.test_exact_model_layout import instance_for
from tests.routing.test_model6_uniform import uniform_fixture
from tests.routing.test_model78_calendar import calendar_fixture
from tests.routing.test_single_batch_comparison import problem


def instance(number):
    return uniform_fixture() if number == 6 else instance_for(number)


@pytest.mark.parametrize('number', range(1, 9))
@pytest.mark.parametrize('formulation', ['expanded', 'compact'])
def test_baseline_matches_default_optimum_on_the_same_complete_problem(number, formulation):
    entry = import_module(f'models.routing.model_{number}.exact')
    p = instance(number)
    baseline = solve_routing(model=number, method='exact', problem=p, exact_time=15,
                             exact_strategy='milp', milp_options={'formulation': formulation})
    reference = entry.solve_exact(instance(number), time_limit=15)
    assert baseline['info']['proven_optimal'] and reference['info']['proven_optimal']
    assert baseline['info']['objective'] == pytest.approx(reference['info']['objective'])
    assert baseline['info']['best_bound'] == pytest.approx(baseline['info']['objective'])
    assert audit_solution(p, baseline)['valid']
    assert baseline['model'] == number
    assert baseline['model_module'] == f'models.routing.model_{number}.exact'
    assert baseline['info']['solver_config']['formulation'] == formulation
    assert baseline['info']['solver_config']['custom_callback'] is False
    json.dumps(baseline, allow_nan=False)
    if number >= 6:
        assert baseline['info']['batch_count_candidates'] == list(p.batch_count_candidates)
        assert baseline['info']['complete_count_domain']
        assert baseline['info']['exact_strategy'] == 'gurobi_unified_milp'
        assert baseline['info']['solver_config']['k_search'] is None
        assert 'count_search' not in baseline and 'count_search_visits' not in baseline
    if number in (2, 4):
        assert baseline['assignment'] == {'C': 'T'}  # Input assignment is S.




def test_baseline_passes_no_callback_and_leaves_gurobi_builtin_search_enabled(monkeypatch):
    import gurobipy as gp
    original = gp.Model.optimize
    seen = []
    def optimize(m, *args, **kwargs):
        assert not args and not kwargs
        assert m.Params.Presolve == -1 and m.Params.Cuts == -1 and m.Params.MIPFocus == 0
        assert m.NumStart == 0
        seen.append(True)
        return original(m)
    monkeypatch.setattr(gp.Model, 'optimize', optimize)
    result = import_module('models.routing.model_3.exact').solve_exact(problem(3), strategy='milp')
    assert seen and result['audit']['valid']




@pytest.mark.parametrize('number', [6, 7, 8])
@pytest.mark.parametrize('variant', [0, 3, 6])
def test_direct_baseline_matches_independent_calendar_oracle(number, variant):
    from tests.routing.test_model78_independent_oracle import one_order_problem, one_order_oracle
    p = one_order_problem(number, variant)
    expected = one_order_oracle(p, number)
    result = solve_routing(model=number, method='exact', problem=p,
                           exact_time=10, exact_strategy='milp')
    if math.isinf(expected):
        assert result['info']['proven_infeasible']
        assert result['info']['objective'] is None
    else:
        assert result['info']['proven_optimal'] and result['audit']['valid']
        assert result['info']['objective'] == pytest.approx(expected)


@pytest.mark.parametrize('number', [6, 7, 8])
def test_incomplete_unified_build_is_never_optimized_or_certified(number, monkeypatch):
    import gurobipy as gp
    import models.common.cycle.unified_milp as unified
    original = unified.append_block
    calls = []
    def incomplete(*args, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise TimeoutError('Second K construction interrupted')
        return original(*args, **kwargs)
    def forbidden(*args, **kwargs):
        raise AssertionError('A partial count domain was optimized')
    monkeypatch.setattr(unified, 'append_block', incomplete)
    monkeypatch.setattr(gp.Model, 'optimize', forbidden)
    result = import_module(f'models.routing.model_{number}.exact').solve_exact(instance(number), time_limit=10)
    assert len(calls) == 2
    assert result['info']['status'] == 'TIME_LIMIT_BUILD'
    assert not result['info']['proven_optimal'] and not result['info']['proven_infeasible']
    assert result['info']['best_bound'] is None and result['info']['objective'] is None
    assert not result['info']['complete_count_domain']


@pytest.mark.parametrize('number', range(1, 9))
def test_tiny_budget_preserves_no_solution_and_unknown_proof(number):
    result = import_module(f'models.routing.model_{number}.exact').solve_exact(
        instance(number), time_limit=1e-12, strategy='milp')
    assert not result['info']['proven_optimal']
    assert not result['info']['proven_infeasible']
    assert result['info']['objective'] is None
    assert result['info']['solver_config']['time_limit'] == 1e-12


def test_failed_audit_cannot_publish_an_upper_bound_or_proof(monkeypatch):
    kernel = import_module('models.routing.model_1.exact.milp_solver')
    monkeypatch.setattr(kernel, 'audit_solution', lambda *args: {'valid': False, 'errors': ['corrupted route']})
    result = import_module('models.routing.model_1.exact').solve_exact(problem(1), strategy='milp')
    assert result['info']['status'] == 'AUDIT_FAILED'
    assert result['info']['objective'] is None
    assert not result['info']['proven_optimal']


def test_cost_disagreement_is_rejected():
    with pytest.raises(RuntimeError, match='objective disagrees'):
        accept_milp_audit({'info': {}, 'audit': {'valid': True, 'objective': 10}}, 9)


@pytest.mark.parametrize('options', [{'threads': True}, {'threads': 0}, {'formulation': 'unknown'}])
def test_invalid_baseline_configuration_is_rejected(options):
    with pytest.raises(ValueError):
        MilpOptions.resolve(options)


def test_strategy_and_initial_solution_mismatch_is_rejected():
    with pytest.raises(ValueError, match='require method'):
        solve_routing(model=1, method='alns', problem=problem(1), exact_strategy='milp')
    with pytest.raises(ValueError, match='milp_options requires'):
        solve_routing(model=1, method='exact', problem=problem(1), exact_strategy='enhanced', milp_options={'threads': 2})
    with pytest.raises(TypeError, match='initial_result'):
        import_module('models.routing.model_7.exact').solve_exact(calendar_fixture(7),
            strategy='milp', initial_result={})


def test_current_cli_records_direct_baseline_configuration(tmp_path, monkeypatch):
    from cli.route import main
    from cli import multibatch
    monkeypatch.setattr(multibatch, 'plot_result', lambda *args: None)
    out = tmp_path/'milp'
    main(['--model', '1', '--solver', 'exact', '--exact-strategy', 'milp',
          '--milp-threads', '1', '--milp-formulation', 'compact', '--exact-time', '10',
          '--output', str(out)])
    result = json.loads((out/'result.json').read_text())
    assert result['info']['exact_strategy'] == 'gurobi_milp'
    assert result['info']['solver_config']['formulation'] == 'compact'
    assert result['audit']['valid']


@pytest.mark.parametrize('number', range(1, 9))
@pytest.mark.parametrize('strategy', ['enhanced', 'joint', 'domain_tree', 'enumeration'])
def test_retired_strategies_are_rejected_before_any_optimization(number, strategy, monkeypatch):
    import gurobipy as gp

    def forbidden(*args, **kwargs):
        raise AssertionError('A retired strategy reached Gurobi')

    monkeypatch.setattr(gp.Model, 'optimize', forbidden)
    with pytest.raises(ValueError, match='Only direct Gurobi'):
        import_module(f'models.routing.model_{number}.exact').solve_exact(
            instance(number), strategy=strategy)
