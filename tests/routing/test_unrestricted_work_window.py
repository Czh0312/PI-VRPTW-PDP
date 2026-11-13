"""Customer windows and return precedence drive the three daily schedules."""
from copy import deepcopy
from dataclasses import replace

import pytest

from models.common.cycle import MultiBatchInstance, MultiBatchPolicy, Order
from models.common.cycle.assignment import assigned_problem
from models.common.cycle.audit import audit_solution as audit_m3
from models.common.cycle.calendar import CalendarCycleInstance
from models.common.cycle.single_batch_cost import SingleBatchCostInstance
from models.common.domain.instance import ProblemInstance
from models.common.domain.node import Customer, Depot, Satellite
from models.common.no_batch import NoBatchInstance, audit_solution as audit_m10
from models.routing.service import solve_routing
from tools.configuration.loader import load_json


def daily_problem(model, *, no_work_window=True, latest_pickup=1248):
    sites = {'D': Depot('D', 0, 0), 'S': Satellite('S', 3, 0, 2),
             'T': Satellite('T', 0, 3, 2)}
    orders = []
    for oid, site, x, y, release, delivery, pickup in (
        ('a', 'S', 4, 0, 450, 480, 520),
        ('b', 'S', 5, 0, 460, 490, 550),
        ('c', 'T', 0, 4, 1200, 1230, latest_pickup),
    ):
        sites[oid] = Customer(oid, x, y, 0, 0, (0, 2000), (0, 2000), site)
        orders.append(Order(oid, oid, site, 'b1', 1, 1, (delivery, delivery+10),
                            (pickup, pickup), 5, 2, release))
    fleet = dict(E1_vehicle_count=2, vehicles_per_satellite=2, E1_Qk=50, E1_Ck=20,
                 E2_Qv=20, E2_Cv=1, container_weight=1, container_volume=1, speed=1)
    network = ProblemInstance(sites, fleet, speed=1)
    durations = dict(time_unit='minutes', time_tick_seconds=60, cycle_start=450, cycle_end=1260,
                     cleaning_duration=15, e1_load_time=2, e2_dispatch_prep_time=1,
                     e2_return_unload_time=1, depot_return_unload_time=1,
                     e1_turnaround_time=0, e2_turnaround_time=1, e2_max_trips_per_vehicle=2)
    if model == 6:
        return NoBatchInstance(network, orders, {**durations, 'policy_version': 'no_batch_container_cycle_v1'},
                               {'objective': 'total_cost'}, no_work_window=no_work_window)
    policy = MultiBatchPolicy.from_config({
        **durations, 'policy_version': 'multi_batch_cutoff_v1', 'container_pool_size': 3,
        'batches': [dict(id='b1', delivery_duration=740, return_duration=70)],
    })
    if model == 4:
        return MultiBatchInstance(network, orders, policy, model=4, no_work_window=no_work_window)
    profile = load_json('data/benchmark27_models_1_6_v1/shared/medium/M01/economic.json')
    return SingleBatchCostInstance(network, orders, policy, profile, no_work_window=no_work_window)


def solve(problem, method):
    return solve_routing(model=problem.model_number, method=method, problem=problem,
                         exact_backend='gurobi' if problem.model_number == 6 else None,
                         exact_time=12, alns_time=2, alns_iter=150, seed=42)


@pytest.mark.parametrize('method', ['exact', 'alns'])
@pytest.mark.parametrize('model', [3, 4, 6])
def test_late_cleaning_is_allowed_beyond_former_closing_time(model, method):
    problem = daily_problem(model)
    policy = problem.operating_policy
    result = solve(problem, method)
    assert result.get('audit', {}).get('valid'), result
    assert result['audit']['periodic_replay']['valid']
    assert policy.cycle_start == 450
    assert policy.cycle_end == 450 + 1440
    assert max(t['return_complete'] for t in result['e1_trips']
               if any(e['kind'] == 'p' for e in t['events'])) + policy.cleaning_duration > 1260
    if method == 'exact':
        assert result['info']['proven_optimal'], result['info']
    if model == 4:
        assignment = result['assignment']
        for row in result['audit']['consolidated_handoffs']:
            assert row['quantity'] == sum(site == row['satellite_id'] for site in assignment.values())
            assert row['pickup_time'] >= row['last_unload']
        late_site = assignment['c']
        supply = next(trip for trip in result['e1_trips']
                      if any(e['kind'] == 'd' and e['site_id'] == late_site for e in trip['events']))
        assert supply['departure'] - policy.e1_load_time >= problem.order_map['c'].release_time
    elif model == 3:
        handoffs = {row['satellite_id']: row for row in result['audit']['consolidated_handoffs']}
        assert handoffs['S']['quantity'] == 2
        assert handoffs['T']['quantity'] == 1
        assert handoffs['S']['last_unload'] < handoffs['T']['last_unload']
        assert all(row['pickup_time'] >= row['last_unload'] for row in handoffs.values())
        assert result['batches'][0]['cutoff'] is None
    else:
        assert not audit_m10(daily_problem(6, no_work_window=False), result)['valid']
        objective = result['audit']['transport_cost'] + 5*result['container_pool_size'] + 0.02*problem.total
        assert result['info']['objective'] == pytest.approx(objective)
        if method == 'exact':
            assert result['info']['bound'] == pytest.approx(objective)


def test_model6_highs_total_cost_includes_the_constant_in_its_bound():
    from tests.routing.test_model6_no_batch import tiny

    problem = tiny().with_options(objective='total_cost')
    result = solve_routing(model=6, method='exact', problem=problem, exact_backend='highs', exact_time=10)
    assert result['info']['proven_optimal'], result
    assert result['container_pool_size'] == 1
    assert result['info']['objective'] == pytest.approx(5.64)
    assert result['info']['bound'] == pytest.approx(5.64)


def test_m3_audit_requires_one_collection_after_last_unload():
    problem = daily_problem(3)
    result = solve(problem, 'alns')
    good = result['audit']['consolidated_handoffs']
    handoff = next(row for row in good if row['satellite_id'] == 'S')
    bad = deepcopy(result)
    trip = next(trip for trip in bad['e1_trips'] if trip['trip_id'] == handoff['pickup_trip'])
    event = next(e for e in trip['events'] if (e['site_id'], e['kind']) == ('S', 'p'))
    event['start'] = handoff['last_unload'] - 1
    assert 'PICKUP_BEFORE_SATELLITE_READY' in {e['code'] for e in audit_m3(problem, bad)['errors']}
    event['start'] = handoff['pickup_time']
    event['quantity'] = 1
    trip['events'].append(dict(event))
    assert 'CONSOLIDATED_PICKUP_COVERAGE' in {e['code'] for e in audit_m3(problem, bad)['errors']}


@pytest.mark.parametrize('model', [3, 4, 6])
def test_cleanup_still_has_to_finish_before_the_next_day(model):
    result = solve(daily_problem(model, latest_pickup=1885), 'exact')
    assert result['info']['proven_infeasible'], result['info']


def test_model3_window_can_be_omitted_from_policy():
    base = daily_problem(3)
    minimal = dict(policy_version='multi_batch_cutoff_v1', time_tick_seconds=60,
                   cleaning_duration=15, container_pool_size=3, e2_max_trips_per_vehicle=1)
    problem = MultiBatchInstance(base.network, base.orders, minimal, model=3, no_work_window=True)
    assert problem.operating_policy.consolidated_handoff
    assert problem.operating_policy.cycle_start == 450


@pytest.mark.parametrize('method', ['exact', 'alns'])
def test_model5_first_batch_can_move_and_the_last_batch_ends_after_cleaning(method):
    base = daily_problem(3)
    first = replace(base.order_map['a'], release_time=450, delivery_window=(1010, 1015),
                    pickup_window=(1030, 1030), allowed_batch_ids=('b1',))
    second = replace(base.order_map['c'], release_time=1000, delivery_window=(1010, 1015),
                     pickup_window=(1255, 1255), allowed_batch_ids=('b1',))
    policy = dict(policy_version='multi_batch_cutoff_v1', time_tick_seconds=60,
                  cleaning_duration=15, container_pool_size=2, e2_max_trips_per_vehicle=2,
                  e1_load_time=2, e2_dispatch_prep_time=1, e2_return_unload_time=1,
                  depot_return_unload_time=1, e2_turnaround_time=1, batches=[dict(id='b1')])
    problem = CalendarCycleInstance(base.network, [first, second], policy,
                                    dict(batch_count_candidates=[1]), base.cost_profile.config,
                                    no_work_window=True)
    result = solve(problem, method)
    assert result.get('audit', {}).get('valid'), result
    assert result['audit']['periodic_replay']['valid']
    assert problem.operating_policy.cycle_start == 450
    assert problem.operating_policy.cycle_end == 1890
    start, end = result['batch_boundaries']
    assert 1000 <= start <= 1006
    clean = max(row['clean_ready'] for row in result['audit']['depot_releases'])
    assert 1260 < clean == result['operations_complete'] == end < 1890
    assert result['batch_cutoffs']['b1'] < min(row['unload_complete']
                                             for row in result['audit']['depot_releases'])
    if method == 'exact':
        assert result['info']['proven_optimal']
    changed = deepcopy(result)
    changed['operations_complete'] -= 1
    assert not audit_m3(problem, changed)['valid']


def test_model5_cli_defaults_to_no_window_and_can_load_the_old_policy(monkeypatch):
    from cli import multibatch, route
    from models.common.cycle import load_instance

    monkeypatch.setattr(multibatch, 'run_case', lambda config, fleet, **_: load_instance(config, fleet))
    current = route.main(['--model', '5'])
    legacy = route.main(['--model', '5', '--fixed-work-window'])
    assert current.no_work_window and not current.operating_policy.consolidated_handoff
    assert current.operating_policy.repeat_interval == 86400
    assert not legacy.no_work_window
    assert legacy.operating_policy.cycle_end == 1260 * 60


@pytest.mark.parametrize('method', ['exact', 'alns'])
def test_model5_multi_batch_still_reuses_only_clean_boxes(method):
    from tests.routing.test_model5_calendar import calendar_fixture

    fixed = calendar_fixture(counts=(2,), late_release=True)
    problem = CalendarCycleInstance(fixed.network, fixed.orders, fixed.operating_policy,
                                    fixed.options, fixed.cost_profile.config, no_work_window=True)
    result = solve(problem, method)
    assert result.get('audit', {}).get('valid'), result
    assert result['audit']['periodic_replay']['valid']
    assert result['container_pool_size'] == 1
    first, second = result['batches']
    assert first['end'] == second['start']
    first_return = next(r for r in result['audit']['depot_releases']
                        if r['trip_id'] in {t['trip_id'] for t in result['e1_trips'] if t['batch_id'] == first['id']})
    assert first['cutoff'] < first_return['unload_complete'] < first_return['clean_ready'] <= second['start']
    assert result['audit']['clean_stock'][1]['available'] == 1
    assert second['end'] == result['operations_complete'] < problem.operating_policy.cycle_end
    if method == 'exact':
        assert result['info']['proven_optimal']


def test_free_assignment_moves_the_release_requirement_to_the_new_warehouse():
    problem = daily_problem(4)
    assignment = {'a': 'T', 'b': 'T', 'c': 'S'}
    fixed = assigned_problem(problem, assignment)
    batch = fixed.operating_policy.batches[0]
    assert fixed.e1_loading_start(batch, ['T']) == 460
    assert fixed.e1_loading_start(batch, ['S']) == 1200


@pytest.mark.parametrize(('flags', 'objective'), [([], 'total_cost'), (['--pool-caps', '7'], 'transport')])
def test_m6_cli_uses_total_cost_for_a_regular_run(monkeypatch, flags, objective):
    from cli import no_batch

    monkeypatch.setattr(no_batch, 'run_case', lambda config, fleet, **kwargs: config)
    config = no_batch.main(['--case', 'config/experiments/examples/model5_demo.json', *flags])
    assert config['no_work_window']
    assert config['no_batch_options']['objective'] == objective
