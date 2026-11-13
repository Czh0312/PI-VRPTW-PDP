"""M9 free-assignment equivalence, calendar/stock physics and proof boundaries."""
from copy import deepcopy
from dataclasses import replace
from importlib import import_module
import itertools
import json
import math

import gurobipy as gp
import pytest

from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import CalendarCycleInstance
from models.common.cycle.instance import load_instance
from models.common.cycle.assignment import input_assignment
from models.cost.comparison import evaluate_comparison_cost
from models.routing.model_9.exact import solve_exact
from models.routing.service import solve_routing
from tools.configuration.loader import load_routing_case


CASE = 'config/experiments/examples/model9_demo.json'


def fixture(number=9, **options):
    config, fleet = load_routing_case(CASE)
    config['model'] = number
    config['joint_batch_options'].update(options)
    return load_instance(config, fleet)


@pytest.mark.parametrize('formulation', ['expanded', 'compact'])
def test_free_choice_reuses_containers_and_beats_fixed_assignment(formulation):
    problem = fixture()
    result = solve_routing(model=9, method='exact', problem=problem, exact_time=20,
                           seed=42, milp_options=dict(formulation=formulation))
    assert result['model'] == 9
    assert result['model_module'] == 'models.routing.model_9.exact'
    assert result['info']['proven_optimal'], result['info']
    assert result['info']['mip_gap'] == pytest.approx(0)
    assert result['assignment'] == {'C1': 'S1', 'C2': 'S2'}
    assert result['cycle_batch_count'] == result['delivery_batch_count'] == 2
    assert result['container_pool_size'] == 1
    assert result['order_batch_assignment'] == {'o1': 'b1', 'o2': 'b2'}
    # 30 pool + .1*20 E1 distance + 4 E2 distance + 1 use/return costs.
    assert result['info']['objective'] == pytest.approx(37)
    assert result['info']['best_bound'] == pytest.approx(37)
    audited = audit_solution(problem, result)
    assert audited['valid'] and audited['periodic_replay']['valid'], audited
    priced = evaluate_comparison_cost(problem, result, problem.cost_profile.config)
    assert priced['total'] == pytest.approx(37) and priced['cost_is_optimized']
    assert [row['demand'] for row in audited['clean_stock']] == [1, 1]
    json.dumps(result, allow_nan=False)


def test_fixed_customer_assignment_recovers_model8_exactly():
    m8 = fixture(8)
    m9 = fixture(fixed_customer_assignment=input_assignment(m8))
    left = import_module('models.routing.model_8.exact').solve_exact(m8, time_limit=20, seed=42)
    right = solve_exact(m9, time_limit=20, seed=42)
    assert left['info']['proven_optimal'] and right['info']['proven_optimal']
    assert left['info']['objective'] == pytest.approx(right['info']['objective'])
    assert left['container_pool_size'] == right['container_pool_size']
    assert right['assignment'] == input_assignment(m8)
    assert audit_solution(m9, right)['valid']


def test_free_optimum_matches_exhaustive_model8_assignment_oracle():
    source = fixture()
    costs = []
    for sites in itertools.product(source.satellites, repeat=2):
        mapping = dict(zip(('C1', 'C2'), sites))
        orders = [replace(order, satellite=mapping[order.customer]) for order in source.orders]
        fixed = CalendarCycleInstance(source.network, orders, source.operating_policy,
            source.options, source.cost_profile.config, model=8)
        result = import_module('models.routing.model_8.exact').solve_exact(fixed, time_limit=20, seed=42)
        assert result['info']['proven_optimal'] or result['info']['proven_infeasible']
        if result['info']['proven_optimal']:
            costs.append(result['info']['objective'])
    free = solve_exact(source, time_limit=20, seed=42)
    assert free['info']['proven_optimal']
    assert free['info']['objective'] == pytest.approx(min(costs))


def test_all_counts_and_customer_choices_use_one_optimize(monkeypatch):
    original, calls = gp.Model.optimize, []

    def optimize(model, *args, **kwargs):
        assert not args and not kwargs and model.NumStart == 0
        assert model.getVarByName('batch_count') is not None
        assert model.getVarByName('K2_customer_site_C1_S1') is not None
        calls.append(model.ModelName)
        return original(model)

    monkeypatch.setattr(gp.Model, 'optimize', optimize)
    result = solve_exact(fixture(), time_limit=20, seed=42)
    assert len(calls) == 1
    assert result['info']['proven_optimal'] and result['info']['complete_count_domain']
    assert result['info']['batch_count_candidates'] == [1, 2, 3]
    assert result['info']['proof_scope'] == 'full_model_9_all_K_joint_cost_integer_time_MILP'
    assert 'count_search' not in result


def test_repeated_orders_and_recovery_stay_at_one_customer_site():
    source = fixture()
    orders = [replace(source.orders[0], customer='C2'), source.orders[1]]
    problem = CalendarCycleInstance(source.network, orders, source.operating_policy,
                                    source.options, source.cost_profile.config, model=9)
    result = solve_exact(problem, time_limit=20, seed=42)
    assert result['info']['proven_optimal'] and audit_solution(problem, result)['valid']
    assert set(result['assignment']) == {'C2'}
    assert {trip['satellite_id'] for trip in result['e2_trips']} == {result['assignment']['C2']}
    assert len({event['order_id'] for trip in result['e2_trips'] for event in trip['events']}) == 2


@pytest.mark.parametrize('tamper', ['unknown', 'other', 'missing', 'fixed_domain'])
def test_independent_audit_rejects_assignment_tampering(tamper):
    problem = fixture()
    result = solve_exact(problem, time_limit=20, seed=42)
    assert result['info']['proven_optimal']
    bad = deepcopy(result)
    if tamper == 'unknown':
        bad['assignment']['C1'] = 'UNKNOWN'
    elif tamper == 'other':
        bad['assignment']['C1'] = 'S2'
    elif tamper == 'missing':
        del bad['assignment']['C1']
    else:
        problem = fixture(fixed_customer_assignment={'C1': 'S2', 'C2': 'S1'})
        bad['calendar_config'] = problem.calendar_contract
    assert not audit_solution(problem, bad)['valid']


def test_unreachable_reference_satellite_does_not_remove_a_feasible_choice():
    source = fixture()
    orders = [replace(source.orders[0], delivery_window=(5, 7)), source.orders[1]]
    problem = CalendarCycleInstance(source.network, orders, source.operating_policy,
                                    source.options, source.cost_profile.config, model=9)
    result = solve_exact(problem, time_limit=20, seed=42)
    assert result['info']['proven_optimal'] and result['assignment']['C1'] == 'S1'
    assert audit_solution(problem, result)['valid']


@pytest.mark.parametrize('strengthening', ['none', 'physical', 'physical_time'])
def test_strengthening_preserves_the_free_assignment_optimum(strengthening):
    result = solve_exact(fixture(), time_limit=20, seed=42, strengthening=strengthening)
    assert result['info']['proven_optimal']
    assert result['info']['objective'] == pytest.approx(37)


def test_full_infeasible_count_domain_is_reported():
    result = solve_exact(fixture(batch_count_candidates=[3]), time_limit=10, seed=42)
    assert result['info']['complete_count_domain']
    assert result['info']['proven_infeasible'] and not result['info']['proven_optimal']
    assert result['info']['objective'] is None


def test_nondivisible_clock_preserves_physical_cost_and_replay():
    source = fixture()
    policy = replace(source.operating_policy, cycle_end=91)
    problem = CalendarCycleInstance(source.network, source.orders, policy,
                                    source.options, source.cost_profile.config, model=9)
    result = solve_exact(problem, time_limit=20, seed=42)
    assert problem.for_count(2).operating_policy.time_tick_subdivision == 2
    assert result['info']['proven_optimal']
    assert result['info']['objective'] == pytest.approx(37)
    assert audit_solution(problem, result)['periodic_replay']['valid']


def test_model9_rejects_alns_until_it_has_its_own_implementation():
    with pytest.raises(ValueError, match='supports only: exact'):
        solve_routing(model=9, method='alns', problem=fixture())


@pytest.mark.parametrize('variant', [0, 1, 3, 6])
def test_single_order_matches_independent_time_and_route_enumeration(variant):
    from tests.routing.test_model78_independent_oracle import one_order_problem, one_order_oracle
    problem = one_order_problem(9, variant)
    expected = one_order_oracle(problem, 9)
    result = solve_exact(problem, time_limit=15, seed=42)
    if not math.isfinite(expected):
        assert result['info']['proven_infeasible']
    else:
        assert result['info']['proven_optimal']
        assert result['info']['objective'] == pytest.approx(expected)
        assert audit_solution(problem, result)['periodic_replay']['valid']


def test_build_timeout_does_not_claim_a_partial_domain_proof():
    result = solve_exact(fixture(), time_limit=1e-8, seed=42)
    assert result['info']['status'] == 'TIME_LIMIT_BUILD'
    assert not result['info']['complete_count_domain']
    assert not result['info']['proven_optimal'] and not result['info']['proven_infeasible']
    assert result['info']['objective'] is result['info']['best_bound'] is None


def test_model9_components_do_not_import_other_numbered_solvers():
    from pathlib import Path
    root = Path(import_module('models.routing.model_9.exact').__file__).parent
    for path in root.glob('*.py'):
        text = path.read_text(encoding='utf-8')
        assert 'models.routing.model_8' not in text and 'models.routing.model_4' not in text
