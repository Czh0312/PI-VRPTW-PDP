"""Unrestricted positive K, rational equal partitions, and physical cost replay."""
from copy import deepcopy
import json

import pytest

from models.routing.common.cycle.calendar import CalendarCycleInstance
from models.routing.common.cycle.uniform import UniformCycleInstance
from models.routing.common.cycle.audit import audit_solution
from models.cost.comparison import evaluate_comparison_cost
from models.routing.service import solve_routing
from tests.routing.test_model6_joint import joint_fixture, exact_available


def instance(model, counts='all_positive_integers'):
    source = joint_fixture(pool_rate=30)
    options = dict(batch_count_candidates=counts)
    if model == 6:
        options['schedule_mode'] = 'uniform_cycle'
        return UniformCycleInstance(source.network, source.orders, source.operating_policy,
                                    options, source.cost_profile.config)
    return CalendarCycleInstance(source.network, source.orders, source.operating_policy,
                                 options, source.cost_profile.config, model=model)


@pytest.mark.parametrize('model', [6, 7, 8])
def test_default_domain_uses_phase_constraint_instead_of_source_calendar(model):
    source = joint_fixture()
    options = {'schedule_mode': 'uniform_cycle'} if model == 6 else {}
    cls = UniformCycleInstance if model == 6 else CalendarCycleInstance
    p = cls(source.network, source.orders, source.operating_policy, options,
            source.cost_profile.config, **({} if model == 6 else {'model': model}))
    assert p.batch_count_candidates == tuple(range(1, 66))
    assert len(p.for_count(4).operating_policy.batches) == 4
    assert len(p.for_count(65).operating_policy.batches) == 65
    with pytest.raises(ValueError, match='outside'):
        p.for_count(66)


@pytest.mark.parametrize('model', [6, 7])
def test_equal_partition_refines_clock_without_changing_physical_times(model):
    p = instance(model, [3, 7])
    child = p.for_count(3)
    clock = child.operating_policy
    assert clock.time_tick_subdivision == 3
    assert {clock.minutes(b.end-b.start) for b in clock.batches} == {130/3}
    assert clock.minutes(clock.cycle_end) == 130
    assert clock.minutes(clock.cleaning_duration) == p.operating_policy.minutes(p.operating_policy.cleaning_duration)
    for o, new in zip(p.orders, child.orders):
        assert tuple(clock.minutes(t) for t in new.delivery_window) == o.delivery_window
        assert clock.minutes(new.min_use_time) == o.min_use_time
    for key, ticks in p._travel.items():
        assert child.travel(*key) == 3*ticks
    assert child.service('S') == 3*p.service('S')


@pytest.mark.parametrize('model', [6, 7])
@pytest.mark.parametrize('method', ['alns', 'exact'])
def test_nondivisible_count_solves_and_independently_replays_cost(model, method, request):
    if method == 'exact':
        request.getfixturevalue('exact_available')
    p = instance(model, [3])
    r = solve_routing(model=model, method=method, problem=p, exact_time=10,
                      alns_time=.5, alns_iter=200)
    r = json.loads(json.dumps(r))
    assert r['cycle_batch_count'] == 3
    assert r['time_tick_subdivision'] == 3
    assert r['audit']['valid'] and r['audit']['periodic_replay']['valid']
    cost = evaluate_comparison_cost(p, r, p.cost_profile.config)
    assert cost['total'] == pytest.approx(r['info']['objective'])
    if method == 'exact':
        assert r['info']['milp_objective'] == pytest.approx(cost['total'])
    forged = deepcopy(r)
    forged['time_tick_subdivision'] = 1
    assert not audit_solution(p, forged)['valid']


@pytest.mark.parametrize('model', [6, 7, 8])
def test_unrestricted_search_keeps_all_counts_in_proof_scope(model):
    p = instance(model)
    r = solve_routing(model=model, method='alns', problem=p, alns_time=.1, alns_iter=20)
    assert r['info']['batch_count_candidates'] == list(range(1, 66))
    assert [row['batch_count'] for row in r['count_search']] == list(range(1, 66))
    assert not r['info']['proven_optimal'] and not r['info']['proven_infeasible']


def test_cli_all_counts_overrides_an_existing_restricted_case(tmp_path):
    from cli.route import main
    r = main(['--case', 'config/cases/model7_demo.json', '--all-batch-counts',
              '--solver', 'alns', '--time', '1', '--iterations', '200',
              '--output', str(tmp_path/'all_counts')])
    assert r['info']['batch_count_candidates'] == list(range(1, 66))
    assert [row['batch_count'] for row in r['count_search']] == list(range(1, 66))
    assert r['audit']['valid'] and r['audit']['periodic_replay']['valid']


@pytest.mark.parametrize('model', [6, 7, 8])
def test_four_batch_solution_is_admissible_and_audited(model):
    p = instance(model, [4])
    r = solve_routing(model=model, method='alns', problem=p, alns_time=.5, alns_iter=200)
    assert r['cycle_batch_count'] == 4
    assert r['audit']['valid'] and r['audit']['periodic_replay']['valid']
