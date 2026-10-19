"""Hand-replayed cutoff, inventory, vehicle and periodicity regression cases."""
from copy import deepcopy
from dataclasses import replace
import json
import random
import pytest
from tools.configuration.loader import load_routing_case, resolve_project_path
from models.common.domain.node import Depot, Satellite, Customer
from models.common.domain.instance import ProblemInstance
from models.common.task_builder import build_tasks_for_model
from models.routing.service import solve_routing
from models.common.cycle import MultiBatchInstance, MultiBatchPolicy, Order, load_instance
from models.common.cycle.instance import load_orders
from models.common.cycle.audit import audit_solution
from models.common.cycle.state import empty_result
from models.routing.model_5.alns.evaluator import evaluate, initial_state
from models.routing.model_5.alns.search import solve_alns


def policy_config():
    return dict(policy_version='multi_batch_cutoff_v1', cycle_start=0, cycle_end=125,
                batches=[dict(id='b1', delivery_duration=30, return_duration=30),
                         dict(id='b2', delivery_duration=30, return_duration=30)],
                container_pool_size=1, cleaning_duration=5, e2_max_trips_per_vehicle=2, time_tick_seconds=60)


def fixture(pool=1, cleaning=5, one_batch=False, zero=False, count=1, **changes):
    cfg = policy_config()
    cfg.update(container_pool_size=pool, cleaning_duration=cleaning, **changes)
    if one_batch:
        cfg.update(batches=cfg['batches'][:1], cycle_end=65)
    p = MultiBatchPolicy.from_config(cfg)
    nodes = {'D': Depot('D', 0, 0), 'S': Satellite('S', 0 if zero else 10, 0),
             'C': Customer('C', 0 if zero else 11, 0, 0, 0, (0, 125), (0, 125), 'S')}
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=1, E1_Qk=500, E1_Ck=500,
                 E2_Qv=500, E2_Cv=500, container_weight=1, container_volume=1)
    orders = [Order('o1', 'C', 'S', 'b1', count, 1, (0, 25), (0, 40), 0)]
    if not one_batch:
        orders.append(Order('o2', 'C', 'S', 'b2', count, 1, (60, 85), (60, 89), 0))
    return MultiBatchInstance(ProblemInstance(nodes, fleet), orders, p)


def hand_solution(problem, first_unload=16):
    r = empty_result(problem, 'manual')
    for index, b in enumerate(problem.operating_policy.batches):
        o = problem.orders[index]
        r['e1_trips'].append(dict(trip_id=f'e1_{b.id}', vehicle_id=0, batch_id=b.id,
            departure=b.start, return_complete=b.cutoff+10, events=[
                dict(kind='d', site_id='S', quantity=o.count, start=b.start+10),
                dict(kind='p', site_id='S', quantity=o.count, start=b.cutoff)]))
        r['e2_trips'].append(dict(trip_id=f'e2_{b.id}', vehicle_id=0, satellite_id='S', trip_index=index,
            departure=b.start+10, return_complete=first_unload if not index else b.start+16, events=[
                dict(kind='d', order_id=o.id, start=b.start+11, quantity=o.count),
                dict(kind='p', order_id=o.id, start=b.start+15, quantity=o.count)]))
    if first_unload > 30 and len(r['e1_trips']) == 2:
        r['e1_trips'][0]['events'].pop()
        r['e1_trips'][0]['return_complete'] = 20
        r['e1_trips'][1]['events'][1]['quantity'] += problem.orders[0].count
    return r


def codes(audit):
    return {e['code'] for e in audit['errors']}


def test_hand_cycle_reuses_one_container_and_replays_two_periods():
    p = fixture()
    a = audit_solution(p, hand_solution(p))
    assert a['valid'], a['errors']
    assert a['objective'] == 44
    assert a['container_balance'] == dict(issued=2, e2_unloaded=2, returned_to_depot=2, pool=1)
    assert a['minimum_pool_for_schedule'] == 1 and a['periodic_replay']['valid']
    assert all(sum(e['states'].values()) == 1 for e in a['container_ledger'])
    assert a['time_metrics']['waiting_ticks']['e1'] == 40


@pytest.mark.parametrize('when,expected', [(29, [1, 1]), (30, [1, 1]), (31, [0, 2])])
def test_cutoff_before_exactly_and_after(when, expected):
    p = fixture(pool=2)
    a = audit_solution(p, hand_solution(p, when))
    assert a['valid'], a['errors']
    assert [r['quantity'] for r in a['cutoff_snapshots']] == expected


def test_later_e1_arrival_does_not_expand_snapshot():
    p = fixture(pool=2)
    r = hand_solution(p, 31)
    r['e1_trips'][0]['events'].append(dict(kind='p', site_id='S', quantity=1, start=35))
    r['e1_trips'][0]['return_complete'] = 45
    r['e1_trips'][1]['events'][1]['quantity'] = 1
    assert 'PICKUP_AMOUNT_MISMATCH' in codes(audit_solution(p, r))


@pytest.mark.parametrize('picked', [1, 3])
def test_cutoff_quantity_is_equality(picked):
    p = fixture(pool=4, count=2)
    r = hand_solution(p)
    r['e1_trips'][0]['events'][1]['quantity'] = picked
    assert codes(audit_solution(p, r)) & {'PICKUP_AMOUNT_MISMATCH', 'CUTOFF_ELIGIBLE_LEFT_BEHIND'}


def test_customer_pickup_and_arrival_are_not_unloaded_inventory():
    p = fixture(pool=2, e2_return_unload_time=2)
    r = hand_solution(p, 31)
    r['e2_trips'][0]['events'][1]['start'] = 28  # arrival 29, unload 31
    r['e2_trips'][1]['return_complete'] = 78
    a = audit_solution(p, r)
    assert a['valid'], a['errors']
    assert [s['quantity'] for s in a['cutoff_snapshots']] == [0, 2]


def test_dirty_containers_cannot_supply_next_batch():
    p = fixture(cleaning=25)
    a = audit_solution(p, hand_solution(p))
    shortage = next(e for e in a['errors'] if e['code'] == 'CLEAN_STOCK_SHORTAGE')
    assert shortage['batch'] == 'b2' and shortage['shortage'] == 1 and shortage['next_release'] == 65


def test_clean_release_exactly_at_loading_is_available():
    p = fixture(cleaning=20)
    a = audit_solution(p, hand_solution(p))
    assert a['valid'], a['errors']
    assert a['clean_stock'][1]['available'] == 1


def test_final_late_return_cannot_be_hidden_by_cycle_tail():
    p = fixture(pool=2)
    r = hand_solution(p)
    r['e2_trips'][1]['return_complete'] = 91
    assert 'TERMINAL_OUTSTANDING' in codes(audit_solution(p, r))


def test_returned_but_dirty_is_not_periodic():
    p = fixture(pool=2, cleaning=30)
    assert 'TERMINAL_DIRTY' in codes(audit_solution(p, hand_solution(p)))


def test_old_stock_collected_without_current_delivery():
    original = fixture(pool=2)
    p = MultiBatchInstance(original.network, original.orders[:1], original.operating_policy)
    r = hand_solution(original, 31)
    r['e2_trips'].pop()
    r['e1_trips'][1].update(departure=90, return_complete=110)
    r['e1_trips'][1]['events'] = [dict(kind='p', site_id='S', quantity=1, start=100)]
    r['batches'][1]['container_demand'] = 0
    a = audit_solution(p, r)
    assert a['valid'], a['errors']
    assert [e['kind'] for e in r['e1_trips'][0]['events']] == ['d']


@pytest.mark.parametrize('dimension', ['e1_weight_cap', 'e1_volume_cap'])
def test_snapshot_must_fit_one_vehicle(dimension):
    p = fixture(pool=4, count=2)
    setattr(p.fleet, dimension, 1)
    assert codes(audit_solution(p, hand_solution(p))) & {'WEIGHT_CAPACITY', 'VOLUME_CAPACITY'}


def test_busy_e1_vehicle_cannot_be_reused():
    p = fixture()
    r = hand_solution(p)
    r['e1_trips'][0]['return_complete'] = 61
    assert {'RETURN_DEADLINE', 'VEHICLE_OVERLAP'} <= codes(audit_solution(p, r))


def test_e2_cross_wave_vehicle_conflict():
    p = fixture(pool=2)
    assert 'VEHICLE_OVERLAP' in codes(audit_solution(p, hand_solution(p, 71)))


def test_recovery_leg_cannot_start_before_cutoff():
    p = fixture(pool=2)
    r = hand_solution(p)
    r['e1_trips'][0]['events'][1]['start'] = 29
    assert 'TRAVEL_TIME' in codes(audit_solution(p, r))


def test_zero_time_still_requires_causal_precedence():
    p = fixture(one_batch=True, zero=True, cleaning=0)
    r = empty_result(p, 'manual')
    r['e1_trips'] = [dict(trip_id='e1', vehicle_id=0, batch_id='b1', departure=0, return_complete=30,
        events=[dict(kind='d', site_id='S', quantity=1, start=0), dict(kind='p', site_id='S', quantity=1, start=30)])]
    r['e2_trips'] = [dict(trip_id='e2', vehicle_id=0, satellite_id='S', trip_index=0, departure=0, return_complete=0,
        events=[dict(kind='p', order_id='o1', quantity=1, start=0), dict(kind='d', order_id='o1', quantity=1, start=0)])]
    assert 'CAUSAL_CYCLE' in codes(audit_solution(p, r))
    r['e2_trips'][0]['events'].reverse()
    assert audit_solution(p, r)['valid']


def test_duplicate_tasks_and_insufficient_pool():
    p = fixture(count=2, pool=1)
    r = hand_solution(p)
    assert 'CLEAN_STOCK_SHORTAGE' in codes(audit_solution(p, r))
    r['e2_trips'][0]['events'].append(deepcopy(r['e2_trips'][0]['events'][1]))
    assert 'DUPLICATE_TASK' in codes(audit_solution(p, r))


def test_json_roundtrip_and_snapshot_tampering():
    p = fixture()
    r = json.loads(json.dumps(hand_solution(p)))
    a = audit_solution(p, r)
    assert a['valid']
    r.update(cutoff_snapshots=a['cutoff_snapshots'], depot_releases=a['depot_releases'])
    assert audit_solution(p, r)['valid']
    r['cutoff_snapshots'][0]['quantity'] = 100
    assert 'DERIVED_STATE_MISMATCH' in codes(audit_solution(p, r))


def test_more_containers_preserve_schedule():
    p = fixture(pool=4)
    a = audit_solution(p, hand_solution(p))
    assert a['valid'] and a['terminal_state']['clean'] == 4


def test_actual_wait_is_preserved():
    p = fixture(pool=2)
    r = hand_solution(p, 31)
    a = audit_solution(p, r)
    assert a['valid'] and a['cutoff_snapshots'][0]['quantity'] == 0
    assert r['e2_trips'][0]['return_complete'] == 31


def test_decoder_rebuilds_quantity_after_wait_move():
    p = fixture(pool=2)
    state = initial_state(p, random.Random(42))
    _, a, issues = evaluate(p, state)
    assert not issues and audit_solution(p, a)['valid']
    state['e2'][0]['return_after'] = 31
    _, b, issues = evaluate(p, state)
    assert not issues
    a = audit_solution(p, b)
    assert a['valid'] and [s['quantity'] for s in a['cutoff_snapshots']] == [0, 2]


@pytest.mark.parametrize('field,value', [('container_pool_size', None), ('container_pool_size', -1),
    ('container_pool_size', 1.5), ('time_tick_seconds', 0), ('cleaning_duration', float('nan')),
    ('cycle_end', 50), ('e2_max_trips_per_vehicle', 0), ('terminal_policy', 'ignore'), ('time_unit', 'seconds')])
def test_invalid_policy(field, value):
    cfg = policy_config()
    cfg[field] = value
    with pytest.raises(ValueError):
        MultiBatchPolicy.from_config(cfg)


def test_time_rounding_is_explicit():
    cfg = policy_config()
    cfg['batches'][0]['delivery_duration'] = 30.001
    with pytest.raises(ValueError, match='multiples'):
        MultiBatchPolicy.from_config(cfg)
    cfg = policy_config()
    cfg.update(time_tick_seconds=1, cleaning_duration=.001)
    p = MultiBatchPolicy.from_config(cfg)
    assert p.cleaning_duration == 1 and p.batches[1].start == 3600


def test_order_identity_and_balanced_counts(tmp_path):
    cfg, fleet = load_routing_case('config/experiments/examples/multibatch_demo.json')
    p = load_instance(cfg, fleet)
    assert len(p.orders) == 4 and len({o.customer for o in p.orders}) == 2
    with resolve_project_path(cfg['orders']).open() as f:
        data = json.load(f)
    data[0]['pickup_container_count'] = 0
    path = tmp_path/'orders.json'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='UNBALANCED'):
        load_orders(path, p.operating_policy)


def test_refuse_static_tasks_legacy_backend_and_other_model():
    p = fixture()
    with pytest.raises(ValueError, match='static'):
        build_tasks_for_model(p, 5)
    with pytest.raises(ValueError, match='legacy backend'):
        solve_routing(model=5, method='alns', problem=p, exact_backend='commodity_flow')
    with pytest.raises(ValueError, match='Model 5'):
        solve_routing(model=4, method='alns', problem=p)


def test_alns_without_gurobi(monkeypatch):
    import builtins
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.startswith('gurobipy'):
            raise ImportError('Gurobi intentionally unavailable')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    r = solve_alns(fixture(), max_time=.1, max_iterations=30)
    assert r['audit']['valid'] and not r['info']['proven_optimal']
    assert r['info']['objective'] == 44


@pytest.fixture
def exact():
    gp = pytest.importorskip('gurobipy')
    try:
        m = gp.Model()
        m.dispose()
    except gp.GurobiError as exc:
        pytest.skip(f'Gurobi unavailable: {exc}')
    def direct(problem, **kwargs):
        from importlib import import_module
        return import_module(f'models.routing.model_{problem.model_number}.exact').solve_exact(problem, **kwargs)
    return direct


@pytest.mark.parametrize('single,zero,expected', [(False, False, 44), (True, False, 22), (True, True, 0)])
def test_exact_hand_distance_and_single_batch_limit(exact, single, zero, expected):
    r = exact(fixture(one_batch=single, zero=zero), time_limit=10)
    assert r['info']['proven_optimal'], r['info']
    assert r['audit']['valid'], r['audit']['errors']
    assert r['info']['objective'] == expected


@pytest.mark.parametrize('case', ['pool', 'last_cutoff', 'dirty'])
def test_exact_proves_infeasible_cases(exact, case):
    p = fixture(pool=1, count=2) if case == 'pool' else fixture(cleaning=30) if case == 'dirty' else fixture(one_batch=True)
    if case == 'last_cutoff':
        p = MultiBatchInstance(p.network, [replace(p.orders[0], pickup_window=(31, 40))], p.operating_policy)
    r = exact(p, time_limit=10)
    assert r['info']['status'] == 'INFEASIBLE' and not r['info']['proven_optimal']


def test_cli_roundtrip_and_no_overwrite(tmp_path):
    from cli.route import main
    out = tmp_path/'run'
    r = main(['--case', 'config/experiments/examples/multibatch_demo.json', '--time', '.1', '--iterations', '0', '--output', str(out)])
    assert r['audit']['valid']
    saved = json.loads((out/'result.json').read_text())
    cfg, fleet = load_routing_case('config/experiments/examples/multibatch_demo.json')
    assert audit_solution(load_instance(cfg, fleet), saved)['valid']
    assert all((out/name).exists() for name in ('timeline.png', 'routes_1.png', 'manifest.json'))
    with pytest.raises(ValueError, match='overwrite'):
        main(['--case', 'config/experiments/examples/multibatch_demo.json', '--output', str(out)])


def test_day_end_override_is_not_ambiguous():
    from cli.route import main
    with pytest.raises(ValueError, match='single-day only'):
        main(['--case', 'config/experiments/examples/multibatch_demo.json', '--day-end', '999'])


@pytest.mark.parametrize('pickup_time,pool,expected', [(29, 1, [1, 1]), (30, 2, [0, 2])])
def test_exact_cutoff_membership_is_two_way(exact, pickup_time, pool, expected):
    p = fixture(pool=pool)
    orders = [replace(p.orders[0], pickup_window=(pickup_time, pickup_time)), p.orders[1]]
    p = MultiBatchInstance(p.network, orders, p.operating_policy)
    r = exact(p, time_limit=10)
    assert r['info']['proven_optimal'] and r['audit']['valid']
    assert [s['quantity'] for s in r['audit']['cutoff_snapshots']] == expected


def test_cost_counts_uses_and_separates_investment():
    from tools.configuration.loader import load_json
    from models.cost.multibatch import evaluate_cycle_cost
    p = fixture()
    config = load_json('config/cost/synthetic/multibatch_synthetic.json')
    cost = evaluate_cycle_cost(p, hand_solution(p), config)
    assert cost['period_operating_cost'] == pytest.approx(68.4)
    assert cost['initial_pool_investment'] == 10
    assert cost['components']['cleaning'] == 2 and cost['container_uses'] == 2
    assert cost['period_cost_with_amortization'] == pytest.approx(69.4)
    assert not cost['cost_is_optimized']
    bad = hand_solution(p)
    bad['e1_trips'][0]['events'][1]['quantity'] = 2
    with pytest.raises(ValueError, match='invalid route'):
        evaluate_cycle_cost(p, bad, config)


def test_box_wear_tracks_two_uses_without_counting_idle_pool_as_wear():
    from tools.configuration.loader import load_json
    from models.cost.multibatch import evaluate_cycle_cost
    config = load_json('config/cost/synthetic/multibatch_synthetic.json')
    config.pop('pool_amortization_cycles')
    config['container_lifetime_uses'] = 10
    for pool in (1, 2):
        p = fixture(pool=pool)
        cost = evaluate_cycle_cost(p, hand_solution(p), config)
        assert cost['container_uses'] == 2
        assert cost['initial_pool_investment'] == 10*pool
        assert cost['container_use_depreciation_per_cycle'] == 2
        assert cost['period_cost_with_use_depreciation'] == pytest.approx(70.4)
        assert cost['pool_amortization_per_cycle'] is None
        assert cost['period_cost_with_amortization'] is None
    config['pool_amortization_cycles'] = 10
    with pytest.raises(ValueError, match='Choose either'):
        evaluate_cycle_cost(p, hand_solution(p), config)


@pytest.mark.parametrize('lifetime', [0, -1, 1.5, True, float('nan')])
def test_box_lifetime_uses_requires_a_positive_integer(lifetime):
    from tools.configuration.loader import load_json
    from models.cost.multibatch import evaluate_cycle_cost
    config = load_json('config/cost/synthetic/multibatch_synthetic.json')
    config.pop('pool_amortization_cycles')
    config['container_lifetime_uses'] = lifetime
    p = fixture()
    with pytest.raises(ValueError):
        evaluate_cycle_cost(p, hand_solution(p), config)


def test_summary_keeps_failures_and_only_uses_proven_reference(tmp_path):
    import csv
    from experiments.analysis.summarize_multibatch_cycle import summarize
    fields = ['run', 'case_variant', 'status', 'valid', 'proven_optimal', 'objective', 'optimal_gap_percent']
    with (tmp_path/'runs.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for name, valid, proven, obj in [('exact', True, True, 44), ('alns', True, False, 44-1e-14), ('failed', False, False, '')]:
            writer.writerow(dict(run=name, case_variant='demo', status=name, valid=valid, proven_optimal=proven, objective=obj))
    rows = summarize(tmp_path)
    assert len(rows) == 3 and rows[1]['optimal_gap_percent'] == '0.000000'
    assert rows[2]['optimal_gap_percent'] == ''


def test_three_waves_two_physical_e1_vehicles(exact):
    cfg = policy_config()
    cfg.update(batches=[dict(id=f'b{i+1}', delivery_duration=60, return_duration=30) for i in range(3)],
               cycle_end=280, container_pool_size=2, e2_max_trips_per_vehicle=3)
    nodes = {'D': Depot('D', 0, 0), 'S': Satellite('S', 10, 0), 'T': Satellite('T', 0, 4),
             'C': Customer('C', 11, 0, 0, 0, (0, 280), (0, 280), 'S'),
             'X': Customer('X', 0, 5, 0, 0, (0, 280), (0, 280), 'T')}
    fleet = dict(E1_vehicle_count=2, vehicles_per_satellite=1, E1_Qk=3, E1_Ck=2,
                 E2_Qv=3, E2_Cv=2, container_weight=1, container_volume=1)
    orders = [Order(f'{s}{i}', c, s, f'b{i+1}', 1, 2, (90*i, 90*i+30),
                    (90*i+35, 90*i+40), 0) for i in range(3) for s, c in [('S', 'C'), ('T', 'X')]]
    p = MultiBatchInstance(ProblemInstance(nodes, fleet), orders, MultiBatchPolicy.from_config(cfg))
    exact_result = exact(p, time_limit=10)
    assert exact_result['info']['proven_optimal']
    heuristic = solve_alns(p, max_time=.2, max_iterations=100)
    for r in (exact_result, heuristic):
        assert r['audit']['valid'], r['audit']['errors']
        assert r['info']['objective'] == 96
        assert r['audit']['e1_vehicle_count'] == 2 and r['audit']['e1_trip_count'] == 6
        assert r['audit']['minimum_pool_for_schedule'] == 2
        assert r['audit']['periodic_replay']['valid']


def test_dispatch_preparation_occupies_the_returning_vehicle():
    p = fixture(pool=2, e2_dispatch_prep_time=2)
    r = hand_solution(p, first_unload=71)
    first, second = r['e2_trips']
    first['departure'] = 12
    first['events'][0]['start'] = 13
    second['departure'] = 72
    second['events'][0]['start'] = 73
    # Goods ready at 70, but the vehicle returns at 71. Preparation is 71..73.
    assert 'VEHICLE_OVERLAP' in codes(audit_solution(p, r))
    second['departure'] = 73
    second['events'][0]['start'] = 74
    assert audit_solution(p, r)['valid']
    state = initial_state(p, random.Random(42))
    state['e2'][0]['return_after'] = 71
    _, decoded, violations = evaluate(p, state)
    assert not violations and audit_solution(p, decoded)['valid']
    assert decoded['e2_trips'][1]['departure'] >= 73


def test_exact_does_not_prepare_a_busy_vehicle(exact):
    p = fixture(pool=2, e2_dispatch_prep_time=2)
    orders = [replace(p.orders[0], pickup_window=(70, 70)),
              replace(p.orders[1], delivery_window=(72, 72))]
    p = MultiBatchInstance(p.network, orders, p.operating_policy)
    assert exact(p, time_limit=10)['info']['status'] == 'INFEASIBLE'


def test_invalid_order_row_and_duplicate_normalized_node(tmp_path):
    cfg, fleet = load_routing_case('config/experiments/examples/multibatch_demo.json')
    orders = tmp_path/'orders.json'
    orders.write_text('[null]')
    with pytest.raises(ValueError, match='object'):
        load_orders(orders, MultiBatchPolicy.from_config(fleet['operating_policy']))
    from pathlib import Path
    lines = resolve_project_path(cfg['data']).read_text().splitlines()
    node = tmp_path/'nodes.csv'
    duplicate = lines[1].split(',')
    duplicate[0] = ' '+duplicate[0]+' '
    node.write_text('\n'.join(lines+[','.join(duplicate)])+'\n')
    with pytest.raises(ValueError, match='Duplicate physical'):
        load_instance({**cfg, 'data': str(node)}, fleet)
