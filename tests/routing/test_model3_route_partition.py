"""Route-bound safety, complete-model proof scope and fresh start correctness."""
from dataclasses import replace
from itertools import permutations
import math
from pathlib import Path
import subprocess
import sys
import time

import pytest

from models.common.cycle.audit import audit_solution
from models.common.cycle.instance import MultiBatchInstance
from models.common.cycle.single_batch_cost import SingleBatchCostInstance
from models.routing.model_3.exact import solve_exact
from models.routing.model_3.exact.model import build_model
from models.routing.model_3.exact import route_partition as rp
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny
from tools.data.build_model6_demo_suite import synthetic_profile


@pytest.mark.parametrize('seed', [0, 2, 3, 4])
@pytest.mark.parametrize('cost', [False, True])
def test_strengthened_complete_model_matches_unstrengthened_and_independent_oracle(seed, cost):
    base = tiny_problem(3, seed)
    p = SingleBatchCostInstance(base.network, base.orders, base.operating_policy,
                               synthetic_profile()) if cost else base
    plain = solve_exact(p, time_limit=10, route_strengthening=False)
    result = solve_exact(p, time_limit=10)
    if not cost:
        expected = enumerate_tiny(base)
        if math.isinf(expected):
            assert result['info']['proven_infeasible'] and plain['info']['proven_infeasible']
            return
        assert result['info']['objective'] == pytest.approx(expected)
    assert result['info']['status'] == plain['info']['status']
    if result['info']['proven_optimal']:
        assert result['info']['objective'] == pytest.approx(plain['info']['objective'])
        assert result['info']['proof_scope'] == 'full_model_3_integer_time_MILP'
        audit = audit_solution(p, result)
        assert audit['valid'] and audit['periodic_replay']['valid']
        bound = result['info']['route_strengthening']
        assert bound['complete_library'] and not bound['historical_information_used']
        assert bound['lower_bound'] <= audit['objective'] + 1e-6
        assert result['info']['solver_config']['mip_start'] == result['info']['mip_start_supplied']


def test_complete_task_masks_match_independent_permutation_enumeration():
    base = tiny_problem(3, 2)
    # Put both orders at one warehouse to exercise mixed delivery/pickup paths.
    p = MultiBatchInstance(base.network, [replace(o, satellite='S') for o in base.orders], base.operating_policy, model=3)
    m, context = build_model(p)
    try:
        params = context['parameters']
        columns, _ = rp.enumerate_trips(p, params, deadline=time.perf_counter()+5)
        actual = {col['mask']: col['distance'] for col in columns}
        oracle = {}
        n = len(p.orders)
        policy = p.operating_policy
        for size in range(1, 2*n+1):
            for seq in permutations(range(2*n), size):
                deliveries = {task for task in seq if task < n}
                weight = sum(p.task(p.orders[i].id, 'd')[1] for i in deliveries)
                volume = sum(p.task(p.orders[i].id, 'd')[2] for i in deliveries)
                if weight > p.fleet.e2_weight_cap or volume > p.fleet.e2_volume_cap:
                    continue
                clock, at, distance, done = policy.cycle_start, 'S', 0., {}
                if deliveries:
                    clock = max(clock, params.bounds['supply']['S'][0]+policy.e2_dispatch_prep_time)
                feasible = True
                for task in seq:
                    i, kind = task % n, 'd' if task < n else 'p'
                    order = p.orders[i]
                    lo, hi = params.task_windows[order.id, kind]
                    if kind == 'p':
                        if i in deliveries and i not in done:
                            feasible = False
                            break
                        lo = max(lo, done.get(i, params.task_windows[order.id, 'd'][0])
                                 + order.service_time + order.min_use_time)
                    clock = max(clock+p.travel(at, order.customer), lo)
                    if clock > hi:
                        feasible = False
                        break
                    if kind == 'd':
                        done[i] = clock
                    _, dw, dv = p.task(order.id, kind)
                    sign = -1 if kind == 'd' else 1
                    weight, volume = weight+sign*dw, volume+sign*dv
                    if not (-1e-8 <= weight <= p.fleet.e2_weight_cap+1e-8
                            and -1e-8 <= volume <= p.fleet.e2_volume_cap+1e-8):
                        feasible = False
                        break
                    distance += p.distance(at, order.customer)
                    at, clock = order.customer, clock+order.service_time
                returned = clock+p.travel(at, 'S')+policy.e2_return_unload_time
                if (not feasible or returned+policy.e2_turnaround_time > policy.cycle_end
                        or (any(task >= n for task in seq) and returned > policy.batches[0].cutoff)):
                    continue
                mask = sum(1 << task for task in seq)
                oracle[mask] = min(oracle.get(mask, math.inf), distance+p.distance(at, 'S'))
        assert actual == pytest.approx(oracle)
    finally:
        m.dispose()


def test_truncated_enumeration_is_discarded_and_complete_model_still_solves(monkeypatch):
    def incomplete(*args, **kwargs):
        raise TimeoutError('Deliberately interrupted enumeration')
    monkeypatch.setattr(rp, 'enumerate_trips', incomplete)
    result = solve_exact(tiny_problem(3, 2), time_limit=5)
    assert result['info']['proven_optimal'] and result['audit']['valid']
    info = result['info']['route_strengthening']
    assert info['applied'] and not info['complete_library']
    assert all(not site['partition_bound_used'] for site in info['sites'].values())
    assert info['lower_bound'] <= result['info']['objective']+1e-6
    assert not result['info']['mip_start_supplied']


def test_pilot_failure_never_removes_full_model_solutions(monkeypatch):
    base = tiny_problem(3, 2)
    p = SingleBatchCostInstance(base.network, base.orders, base.operating_policy, synthetic_profile())
    monkeypatch.setattr(rp, 'prepare_start', lambda *a, **kw: dict(supplied=False, reason='no_pilot_solution'))
    result = solve_exact(p, time_limit=5)
    assert result['info']['proven_optimal'] and result['audit']['valid']
    assert result['info']['internal_start']['reason'] == 'no_pilot_solution'
    assert not result['info']['mip_start_supplied']


@pytest.mark.parametrize('refined', [False, True])
def test_multiple_boxes_and_refined_integer_clocks_keep_the_original_optimum(refined):
    base = tiny_problem(3, 2)
    scale = 3 if refined else 1
    policy = replace(base.operating_policy, container_pool_size=4,
        cycle_end=base.operating_policy.cycle_end*scale,
        time_tick_subdivision=scale,
        batches=tuple(replace(b, start=b.start*scale, cutoff=b.cutoff*scale, end=b.end*scale)
                      for b in base.operating_policy.batches),
        **{key: getattr(base.operating_policy, key)*scale for key in (
            'cleaning_duration', 'e1_load_time', 'e2_dispatch_prep_time',
            'e2_return_unload_time', 'depot_return_unload_time',
            'e1_turnaround_time', 'e2_turnaround_time')})
    orders = [replace(o, count=2, goods_weight=2*o.goods_weight,
        delivery_window=tuple(t*scale for t in o.delivery_window),
        pickup_window=tuple(t*scale for t in o.pickup_window),
        service_time=o.service_time*scale, min_use_time=o.min_use_time*scale) for o in base.orders]
    p = SingleBatchCostInstance(base.network, orders, policy, synthetic_profile())
    p._travel = {key: value*scale for key, value in base._travel.items()}
    plain = solve_exact(p, time_limit=5, route_strengthening=False)
    result = solve_exact(p, time_limit=5)
    assert plain['info']['proven_optimal'] and result['info']['proven_optimal']
    assert result['info']['objective'] == pytest.approx(plain['info']['objective'])
    assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']


def test_optimal_relaxation_cannot_publish_a_proof_without_a_full_model_solution(monkeypatch):
    import gurobipy as gp
    original = gp.Model.optimize

    def no_main_solution(model, *args, **kwargs):
        if model.ModelName == 'multi_batch_cutoff_v1':
            model.Params.TimeLimit = 0
        return original(model, *args, **kwargs)

    monkeypatch.setattr(gp.Model, 'optimize', no_main_solution)
    result = solve_exact(tiny_problem(3, 2), time_limit=5)
    assert any(site['assignment'].get('native_status') == gp.GRB.OPTIMAL
               for site in result['info']['route_strengthening']['sites'].values())
    assert result['info']['status'] == 'TIME_LIMIT'
    assert not result['info']['proven_optimal'] and result['info']['objective'] is None


def test_failed_final_audit_cancels_native_proof_even_with_route_bound(monkeypatch):
    from models.routing.model_3.exact import milp_solver
    monkeypatch.setattr(milp_solver, 'audit_solution', lambda *a: dict(valid=False, errors=['failed']))
    result = solve_exact(tiny_problem(3, 2), time_limit=5)
    assert result['info']['status'] == 'AUDIT_FAILED'
    assert not result['info']['proven_optimal'] and result['info']['objective'] is None


def test_exact_m3_keeps_solver_and_numbered_model_import_isolation():
    from tests.routing.test_solver_isolation import SCRIPT
    process = subprocess.run([sys.executable, '-c', SCRIPT, 'exact', '3'],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stdout+process.stderr
    assert 'ISOLATED exact 3' in process.stdout


def test_expired_budget_and_prefix_cap_do_not_add_a_partial_bound():
    p = tiny_problem(3, 2)
    m, ctx = build_model(p)
    try:
        original = m.NumConstrs
        info = rp.strengthen(p, m, ctx, deadline=time.perf_counter()-1)
        assert not info['applied'] and m.NumConstrs == original
        with pytest.raises(TimeoutError, match='prefix limit'):
            rp.enumerate_trips(p, ctx['parameters'], deadline=time.perf_counter()+5, max_prefixes=1)
    finally:
        m.dispose()


def test_partial_site_keeps_other_sites_complete_bounds(monkeypatch):
    original = rp.enumerate_trips

    def partial(*args, **kwargs):
        if kwargs.get('sites') == ['T']:
            raise rp.IncompleteEnumeration('Deliberate partial library',
                {1: dict(mask=1, distance=1e12, satellite='T')}, 1)
        return original(*args, **kwargs)

    monkeypatch.setattr(rp, 'enumerate_trips', partial)
    p = tiny_problem(3, 2)
    result = solve_exact(p, time_limit=10)
    info = result['info']['route_strengthening']
    assert result['info']['proven_optimal'] and result['audit']['valid']
    assert info['applied'] and not info['complete_library']
    assert info['sites']['S']['partition_bound_used']
    assert not info['sites']['T']['partition_bound_used']
    assert info['lower_bound'] <= result['info']['objective']+1e-6


def test_partial_library_never_defines_a_partition_lower_bound(monkeypatch):
    import gurobipy as gp

    def forbidden(*args, **kwargs):
        raise AssertionError('A partial partition model was optimized')

    monkeypatch.setattr(gp.Model, 'optimize', forbidden)
    lower, masks, info = rp.partition_distance_bound(tiny_problem(3, 2), 'S',
        [dict(mask=1, distance=1e12)], complete=False, deadline=time.perf_counter()+5)
    assert lower is None and masks == []


@pytest.mark.parametrize('count', [7, 20, 50])
def test_same_strengthening_accepts_all_sizes_without_a_customer_threshold(count):
    import gurobipy as gp
    from models.common.cycle import MultiBatchPolicy, Order
    from models.common.domain.instance import ProblemInstance
    from models.common.domain.node import Depot, Satellite, Customer
    from models.routing.model_3.exact.parameters import prepare_parameters
    from tools.configuration.loader import load_json

    nodes = {'D': Depot('D', 0, 0)}
    orders = []
    for i in range(count):
        site, customer = f'S{i}', f'C{i}'
        nodes[site] = Satellite(site, 0, 0)
        nodes[customer] = Customer(customer, 0, 0, 1, 0, (0, 100), (0, 100), site)
        orders.append(Order(f'o{i}', customer, site, 'b1', 1, 1, (0, 40), (5, 60), 0, 1))
    fleet = dict(E1_vehicle_count=1, vehicles_per_satellite=1, E1_Qk=4*count, E1_Ck=count,
                 E2_Qv=4, E2_Cv=2, container_weight=1, container_volume=1)
    policy = MultiBatchPolicy.from_config(dict(policy_version='multi_batch_cutoff_v1',
        cycle_start=0, cycle_end=100, batches=[dict(id='b1', delivery_duration=65, return_duration=30)],
        container_pool_size=count, cleaning_duration=3, time_tick_seconds=60, e2_max_trips_per_vehicle=1))
    p = SingleBatchCostInstance(ProblemInstance(nodes, fleet), orders, policy,
        load_json('config/experiments/benchmark27_v1/economic.json'))
    parameters = prepare_parameters(None, None, p, True, None)
    model = gp.Model('known_zero_transport_bound')
    try:
        model.Params.OutputFlag = 0
        constant = (p.cost_profile.rates['container_pool_cost_per_period']*count
                    + sum(p.cost_profile.constant_cost(count).values()))
        model.setObjective(gp.LinExpr(constant))
        model.update()
        info = rp.strengthen(p, model, dict(parameters=parameters), deadline=time.perf_counter()+3)
        assert info['applied'] and len(info['sites']) == count
        assert info['lower_bound'] == pytest.approx(constant)
        assert not hasattr(rp, 'MAX_ORDERS')
        model.optimize()
        assert model.Status == gp.GRB.OPTIMAL
    finally:
        model.dispose()


def test_partial_enumeration_can_generate_a_start_without_supplying_its_bound(monkeypatch):
    def partial(*args, **kwargs):
        raise TimeoutError('No exhaustive library this time')
    monkeypatch.setattr(rp, 'enumerate_trips', partial)
    base = tiny_problem(3, 2)
    p = SingleBatchCostInstance(base.network, base.orders, base.operating_policy, synthetic_profile())
    result = solve_exact(p, time_limit=10)
    info = result['info']
    assert info['proven_optimal'] and result['audit']['valid']
    assert not info['route_strengthening']['complete_library']
    assert info['internal_start']['supplied']
    assert info['internal_start']['attempts'][0]['scope'] == 'generated_vehicle_and_path_allocation'
    assert info['route_strengthening']['lower_bound'] <= info['objective']+1e-6


def test_thread_only_options_preserve_the_shared_formulation_default():
    result = solve_exact(tiny_problem(3, 2), time_limit=10, milp_options=dict(threads=1))
    assert result['info']['proven_optimal']
    assert result['info']['solver_config']['formulation'] == 'compact'
    assert result['info']['solver_config']['formulation_options']['capacity_links']


@pytest.mark.parametrize('value', [None, 1, 'auto'])
def test_invalid_acceleration_option_is_rejected(value):
    with pytest.raises(ValueError, match='route_strengthening must be boolean'):
        solve_exact(tiny_problem(3, 2), route_strengthening=value)
