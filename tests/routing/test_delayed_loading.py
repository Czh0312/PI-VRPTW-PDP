"""Physical release, inventory and vehicle accounting for the E3 baseline."""
from copy import deepcopy
from dataclasses import replace

import pytest

from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import CalendarCycleInstance
from models.routing.service import solve_routing
from tests.routing.test_model6_joint import joint_fixture, exact_available
from tests.routing.test_model78_calendar import calendar_fixture


def solve(problem):
    return solve_routing(model=7, method='exact', problem=problem, exact_time=20)


@pytest.fixture(scope='module')
def released_example():
    problem = calendar_fixture(7, counts=(2,), late_release=True, loading_mode='optimized')
    result = solve(problem)
    assert result['info']['proven_optimal'] and result['audit']['valid']
    return problem, result


def test_late_goods_require_real_loading_delay(released_example, exact_available):
    problem, result = released_example
    standard = calendar_fixture(7, counts=(2,), late_release=True)
    assert solve(standard)['info']['proven_infeasible']
    assert result['batch_boundaries'] == [0, 65, 130]
    assert result['batch_loading_starts']['b2'] >= 70
    assert result['audit']['periodic_replay']['valid']
    assert not audit_solution(standard, result)['valid']
    # Occupation is counted from the actual loading operation, independently
    # of the selected calendar boundary and cached cost components.
    expected = sum(t['return_complete'] - (
        result['batch_loading_starts'][t['batch_id']]
        if any(e['kind'] == 'd' for e in t['events']) else t['departure'])
        for t in result['e1_trips'])
    assert result['audit']['time_metrics']['working_ticks']['e1'] == expected


def test_cleaning_release_can_enable_later_loading(exact_available):
    source = joint_fixture(zero=True, cleaning=6, pool_rate=30)
    orders = [replace(source.orders[0], delivery_window=(0, 5), pickup_window=(64, 64), min_use_time=0),
              replace(source.orders[1], delivery_window=(70, 80), pickup_window=(82, 82), min_use_time=0)]
    options = dict(batch_count_candidates=[2], pool_min=1, pool_max=1)
    standard = CalendarCycleInstance(source.network, orders, source.operating_policy, options,
                                     source.cost_profile.config, model=7)
    delayed = CalendarCycleInstance(source.network, orders, source.operating_policy,
                                    dict(options, loading_mode='optimized'), source.cost_profile.config, model=7)
    assert solve(standard)['info']['proven_infeasible']
    result = solve(delayed)
    assert result['info']['proven_optimal'] and result['audit']['valid']
    assert result['batch_loading_starts']['b2'] >= 70
    assert result['audit']['minimum_pool_for_schedule'] == 1
    ledger = result['audit']['container_ledger']
    assert next(e['time'] for e in ledger if e['source'] == 'issue:b2') == result['batch_loading_starts']['b2']
    assert result['audit']['periodic_replay']['valid']


@pytest.mark.parametrize('mutate', [
    lambda r: r.pop('batch_loading_starts'),
    lambda r: r['batch_loading_starts'].update(b2=65),
    lambda r: r['batch_loading_starts'].update(b2=130),
    lambda r: r['batch_loading_starts'].update(b2=True),
    lambda r: r.update(loading_mode='batch_start'),
    lambda r: r['calendar_config'].pop('loading_mode'),
    lambda r: r['e1_trips'][0].update(departure=r['e1_trips'][0]['departure'] + 1),
])
def test_loading_decisions_cannot_rewrite_contract(released_example, mutate):
    problem, result = released_example
    changed = deepcopy(result)
    mutate(changed)
    assert not audit_solution(problem, changed)['valid']


def test_unsupported_loading_modes_are_rejected():
    with pytest.raises(ValueError, match='M7 extended'):
        calendar_fixture(8, loading_mode='optimized')
    problem = calendar_fixture(7, loading_mode='optimized')
    with pytest.raises(ValueError, match='exact'):
        solve_routing(model=7, method='alns', problem=problem, alns_time=1)
