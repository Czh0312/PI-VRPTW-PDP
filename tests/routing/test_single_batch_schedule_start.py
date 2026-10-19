"""Independent fixed-route integer timing and actual MIP-start feasibility."""
from collections import defaultdict
from copy import deepcopy
from dataclasses import replace
import itertools
import math
import time

import pytest

from models.common.cycle import MultiBatchInstance
from models.common.cycle.audit import audit_solution
from models.common.cycle.state import empty_result
from models.routing.model_1.exact.bounds.route_cg.columns import DeliveryMaster, solve_with_route_bound
from models.routing.model_1.exact.primal.pool_master import DeliveryPoolMaster
from models.routing.model_1.exact.primal.route_pool import route_pool_incumbent
from models.routing.model_1.exact.primal.schedule_start import canonical_delivery_start
from tests.routing.test_single_batch_route_pool import full_pool, physical_case
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny


def delayed_three_trip_start():
    base = physical_case(vehicles=2, service=1, prep=2, turn=1)
    p = MultiBatchInstance(base.network, list(base.orders)+[replace(base.orders[0], id='c')],
                           base.operating_policy, model=1)
    for key in p._travel:
        p._travel[key] = 0
    result = empty_result(p, 'test_oracle')
    result['e1_trips'] = [dict(trip_id='inbound', vehicle_id=0, batch_id='b1', departure=0,
        return_complete=6, events=[dict(site_id='S', kind='d', quantity=3, start=5)])]
    for key, vehicle, slot, depart in [('a', 0, 0, 9), ('b', 1, 0, 10), ('c', 1, 1, 18)]:
        result['e2_trips'].append(dict(trip_id=key, satellite_id='S', vehicle_id=vehicle,
            trip_index=slot, departure=depart, return_complete=depart+1,
            events=[dict(order_id=key, kind='d', quantity=1, start=depart)]))
    audited = audit_solution(p, result)
    assert audited['valid']
    result.update(audit=audited)
    result['info'].update(status='FEASIBLE', objective=audited['objective'])
    return p, result


def integer_departure_oracle(p, result):
    """No production normalization, graph, profile or replay helpers.

    Replay earliest E1 operations; exhaust integer E2 departures one by one
    with actual raw travel, windows, preparation and previous vehicle return.
    """
    policy, batch = p.operating_policy, p.operating_policy.batches[0]
    expected, supply = {}, {}
    for trip in result['e1_trips']:
        now, previous, times = batch.start+policy.e1_load_time, p.depot, []
        for event in trip['events']:
            now += p.travel(previous, event['site_id'])
            times.append(now)
            now += p.service(event['site_id'])
            supply[event['site_id']] = now
            previous = event['site_id']
        expected[trip['trip_id']] = (trip['departure'], now+p.travel(previous, p.depot)+policy.depot_return_unload_time, times)
    groups = defaultdict(list)
    for trip in result['e2_trips']:
        groups[trip['satellite_id'], trip['vehicle_id']].append(trip)
    for (site, vehicle), trips in groups.items():
        ready = policy.cycle_start
        for trip in sorted(trips, key=lambda t:t['trip_index']):
            for depart in range(policy.cycle_start, trip['departure']+1):
                if depart-policy.e2_dispatch_prep_time < max(ready, supply[site]):
                    continue
                now, previous, times = depart, site, []
                feasible = True
                for event in trip['events']:
                    order = p.order_map[event['order_id']]
                    now = max(now+p.travel(previous, order.customer), order.delivery_window[0])
                    if now > order.delivery_window[1] or now+order.service_time > batch.cutoff:
                        feasible = False
                        break
                    times.append(now)
                    now, previous = now+order.service_time, order.customer
                end = now+p.travel(previous, site)+policy.e2_return_unload_time
                if feasible and end+policy.e2_turnaround_time <= policy.cycle_end:
                    expected[trip['trip_id']] = depart, end, times
                    ready = end+policy.e2_turnaround_time
                    break
            else:
                raise AssertionError('Audited fixed trip had no independently feasible departure')
    return expected


def assert_matches_integer_oracle(p, original, normalized):
    expected = integer_departure_oracle(p, original)
    for trip in normalized['e1_trips']+normalized['e2_trips']:
        assert (trip['departure'],trip['return_complete'],[e['start'] for e in trip['events']]) == expected[trip['trip_id']]
    assert audit_solution(p, normalized)['valid']
    assert normalized['audit']['periodic_replay']['valid']
    assert normalized['info']['objective'] == original['info']['objective']
    assert normalized['info']['proven_optimal'] == original['info']['proven_optimal']
    assert normalized['info']['best_bound'] == original['info']['best_bound']


def test_delayed_supply_and_multi_trip_preparation_match_every_integer_departure():
    p, original = delayed_three_trip_start()
    before = deepcopy(original)
    normalized, stats = canonical_delivery_start(p, original)
    assert_matches_integer_oracle(p, original, normalized)
    assert original == before
    assert stats['departures_advanced'] == 3 and stats['vehicle_labels_changed'] == 3
    assert not stats['global_proof']
    groups = defaultdict(list)
    for trip in normalized['e2_trips']:
        groups[trip['vehicle_id']].append(trip)
    assert [len(groups[v]) for v in sorted(groups)] == [2,1]
    assert [t['departure'] for t in groups[0]] == [2,6]
    twice, repeated = canonical_delivery_start(p, normalized)
    assert twice['e1_trips'] == normalized['e1_trips'] and twice['e2_trips'] == normalized['e2_trips']
    assert repeated['departures_advanced'] == repeated['vehicle_labels_changed'] == 0


@pytest.mark.parametrize('compact', [False,True])
@pytest.mark.parametrize('canonical', [False,True])
def test_pin_actual_pool_start_and_check_symmetry_feasibility(monkeypatch, compact, canonical):
    gp = pytest.importorskip('gurobipy')
    p, original = delayed_three_trip_start()
    klass = DeliveryPoolMaster if compact else DeliveryMaster
    captured = []
    monkeypatch.setattr(klass, 'dispose', lambda master: captured.append(master))
    try:
        _, stats = route_pool_incumbent(p, full_pool(p), initial_result=original,
            deadline=time.perf_counter()+2, compact=compact, canonical=canonical)
        assert len(captured) == 1 and not stats['global_proof']
        master = captured[0]
        # Check the actual starts loaded by route_pool_incumbent, not a second
        # implementation of its loading logic. Unspecified cargo is completed.
        for variable in master.model.getVars():
            if abs(variable.Start) < 1e80:
                variable.LB = variable.UB = variable.Start
        master.model.optimize()
        assert master.model.Status == (gp.GRB.OPTIMAL if canonical else gp.GRB.INFEASIBLE)
    finally:
        for master in captured:
            master.model.dispose()


@pytest.mark.parametrize('model',[1,2])
@pytest.mark.parametrize('seed',range(8))
def test_directed_zero_and_duplicate_task_routes_match_integer_timing_oracle(model, seed):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model, seed)
    original, _ = route_pool_incumbent(p, full_pool(p), deadline=time.perf_counter()+2)
    if original is None:
        assert math.isinf(enumerate_tiny(p))
        return
    normalized, stats = canonical_delivery_start(p, original)
    assert_matches_integer_oracle(p, original, normalized)
    assert not stats['global_proof']


def test_vehicle_permutation_and_sparse_slots_produce_same_canonical_routes():
    p, original = delayed_three_trip_start()
    p.fleet.e2_per_sat_count = 4
    p = MultiBatchInstance(p.network, p.orders, replace(p.operating_policy, e2_max_trips_per_vehicle=3), model=1)
    for key in p._travel:
        p._travel[key] = 0
    original['operating_policy'] = p.operating_policy.as_dict()
    baseline, _ = canonical_delivery_start(p, original)
    for first, second in itertools.permutations(range(4),2):
        permuted = deepcopy(original)
        for t in permuted['e2_trips']:
            t['vehicle_id'] = first if t['vehicle_id']==0 else second
            if t['trip_index']==1:
                t['trip_index']=2
        assert audit_solution(p,permuted)['valid']
        normalized, _ = canonical_delivery_start(p,permuted)
        assert normalized['e2_trips']==baseline['e2_trips']


def test_expired_normalization_budget_keeps_input_untouched():
    p, original = delayed_three_trip_start()
    before = deepcopy(original)
    result, stats = canonical_delivery_start(p, original, deadline=time.perf_counter()-1)
    assert result is None and stats['status']=='TIME_LIMIT' and original==before
    assert not stats['global_proof']


def test_expiry_after_dependency_graph_returns_no_partial_start(monkeypatch):
    from models.routing.model_1.exact.primal import schedule_start as schedule
    p,initial=delayed_three_trip_start()
    before=deepcopy(initial)
    now=[0.0]
    actual=schedule.normalize_schedule
    def expired_after_replay(*args):
        transformed=actual(*args)
        now[0]=2
        return transformed
    monkeypatch.setattr(schedule.time,'perf_counter',lambda:now[0])
    monkeypatch.setattr(schedule,'normalize_schedule',expired_after_replay)
    transformed,stats=canonical_delivery_start(p,initial,deadline=1)
    assert transformed is None and stats['status']=='TIME_LIMIT'
    assert initial==before and not stats['global_proof']


@pytest.mark.parametrize('swap',[False,True])
def test_already_legal_equal_trip_counts_keep_original_vehicle_labels(swap):
    base, raw=delayed_three_trip_start()
    p=MultiBatchInstance(base.network,base.orders[:2],base.operating_policy,model=1)
    for key in p._travel:
        p._travel[key]=0
    initial=empty_result(p,'test_oracle')
    initial['e1_trips']=deepcopy(raw['e1_trips'])
    initial['e1_trips'][0]['events'][0]['quantity']=2
    initial['e2_trips']=deepcopy(raw['e2_trips'][:2])
    for trip in initial['e2_trips']:
        if swap:
            trip['vehicle_id']=1-trip['vehicle_id']
    audited=audit_solution(p,initial)
    assert audited['valid']
    initial['info']['objective']=audited['objective']
    expected={trip['trip_id']:trip['vehicle_id'] for trip in initial['e2_trips']}
    normalized,stats=canonical_delivery_start(p,initial)
    assert {trip['trip_id']:trip['vehicle_id'] for trip in normalized['e2_trips']}==expected
    assert stats['vehicle_labels_changed']==0
    assert_matches_integer_oracle(p,initial,normalized)


@pytest.mark.parametrize('model',[3,4])
def test_delivery_start_cannot_drop_pickup_dependencies(model):
    with pytest.raises(ValueError,match='M1/M2'):
        canonical_delivery_start(tiny_problem(model,1), {})


def test_invalid_start_is_rejected_before_retiming():
    p, original = delayed_three_trip_start()
    original['e2_trips'][0]['events'][0]['quantity']=2
    with pytest.raises(ValueError,match='independent audit'):
        canonical_delivery_start(p, original)


@pytest.mark.parametrize('model',[1,2])
def test_canonical_hybrid_preserves_complete_original_proof(model):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model,1)
    result=solve_with_route_bound(p,time_limit=3,root_time=1,pool_time=1,pool_compact=True,
        pool_canonical=True,pricing_method='mip',mip_strategy='pool',dual_cuts=True)
    assert audit_solution(p,result)['valid'] and result['info']['proven_optimal']
    assert not result['info']['route_pool_primal']['global_proof']
    assert not result['info']['delivery_start_normalization']['global_proof']


@pytest.mark.parametrize('enabled',[False,True])
@pytest.mark.parametrize('normalization_cost',[0,4,20])
def test_normalization_shares_total_deadline_and_preserves_start_on_expiry(monkeypatch, enabled, normalization_cost):
    from models.routing.model_1.exact.bounds.route_cg import columns as cg
    from models.routing.model_1.exact.primal import schedule_start as schedule
    from models.routing.model_1.exact import enhanced_solver as solver
    p, initial=delayed_three_trip_start()
    normalized,_=canonical_delivery_start(p,initial)
    now,seen=[0.0],{}
    def root(*args,**kwargs):
        now[0]=10
        return dict(bound=0,proven_infeasible=False,proof_scope='B1_delivery_route_relaxation')
    def canonical(*args,**kwargs):
        seen['normalization_deadline']=kwargs['deadline']
        assert kwargs['deadline']==30
        now[0]+=normalization_cost
        if now[0]==30:
            return None,dict(status='TIME_LIMIT',global_proof=False)
        return normalized,dict(status='NORMALIZED',global_proof=False)
    def full(*args,**kwargs):
        seen['full_budget']=kwargs['time_limit']
        assert kwargs['initial_result']==(normalized if enabled else initial)
        now[0]+=kwargs['time_limit']
        return deepcopy(kwargs['initial_result'])
    monkeypatch.setattr(cg.time,'perf_counter',lambda:now[0])
    monkeypatch.setattr(cg,'root_route_bound',root)
    monkeypatch.setattr(schedule,'canonical_delivery_start',canonical)
    monkeypatch.setattr(solver,'solve_exact',full)
    result=cg.solve_with_route_bound(p,time_limit=30,root_time=10,pool_canonical=enabled,initial_result=initial)
    assert now[0]==30 and audit_solution(p,result)['valid']
    assert result['info']['objective']==initial['info']['objective']
    assert not result['info']['proven_optimal'] and not result['info']['proven_infeasible']
    if enabled and normalization_cost==20:
        assert 'full_budget' not in seen
        assert result['e2_trips']==initial['e2_trips']
    else:
        assert seen['full_budget']==20-(normalization_cost if enabled else 0)
    assert ('normalization_deadline' in seen)==enabled
