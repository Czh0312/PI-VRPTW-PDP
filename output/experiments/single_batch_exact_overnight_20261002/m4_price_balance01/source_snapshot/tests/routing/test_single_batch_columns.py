"""Independent pricing enumeration, interrupted certificates and B1 scope."""
from dataclasses import replace
import itertools
import math
import random
import time

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.precheck import travel_lower_bounds
from models.routing.solvers.exact.domains import assignment_domains
from models.routing.solvers.exact.single_batch_columns import (
    DeliveryMaster, corrected_master_bound, e1_delivery_tours, root_route_bound, solve_with_route_bound)
from models.routing.solvers.exact.single_batch_route_pricing import (
    delivery_context, evaluate_delivery_column, price_delivery_routes)
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny
from tests.routing.test_single_batch_comparison import problem as one_customer


def contexts(p):
    travel = travel_lower_bounds(p)
    check = assignment_domains(p, travel)
    domains = check['domains'] if p.free_assignment else {o.customer: [o.satellite] for o in p.orders}
    return domains, travel, {s: delivery_context(p, s, domains, check['windows'], travel)
        for s in p.satellites if any(s in choices for choices in domains.values())}


def independently_enumerated_trips(p, context):
    # No pricing labels, insertion algorithm or route evaluator is used here.
    orders = [n['order'] for n in context['nodes']]
    site, policy, fleet = context['satellite'], p.operating_policy, p.fleet
    for size in range(1, len(orders)+1):
        for sequence in itertools.permutations(orders, size):
            if (math.fsum(o.goods_weight+o.count*fleet.container_weight for o in sequence) > fleet.e2_weight_cap+1e-6 or
                    sum(o.count for o in sequence)*fleet.container_volume > fleet.e2_volume_cap+1e-6):
                continue
            clock, previous, cost, starts = context['release'], site, 0.0, []
            valid = True
            for o in sequence:
                low, high = context['nodes'][context['lookup'][o.id]]['window']
                clock = max(clock+p.travel(previous, o.customer), low)
                if clock > high:
                    valid = False
                    break
                starts.append(clock)
                cost += p.distance(previous, o.customer)
                previous, clock = o.customer, clock+o.service_time
            if not valid or clock+p.travel(previous, site)+policy.e2_return_unload_time > context['end_limit']:
                continue
            yield dict(tasks=tuple(o.id for o in sequence), cost=cost+p.distance(previous, site),
                       starts=starts)


@pytest.mark.parametrize('seed', range(8))
@pytest.mark.parametrize('phase_one', [False, True])
def test_pricing_minimum_and_raw_replay_match_independent_permutations(seed, phase_one):
    p = tiny_problem(2, seed)
    _, _, by_site = contexts(p)
    rng = random.Random(17+seed)
    for context in by_site.values():
        prices = {n['key']: rng.uniform(-4, 20) for n in context['nodes']}
        fleet_price = -rng.uniform(0, 3)
        trips = list(independently_enumerated_trips(p, context))
        expected = min(((0 if phase_one else c['cost'])-sum(prices[k] for k in c['tasks'])-fleet_price
                        for c in trips), default=math.inf)
        priced = price_delivery_routes(p, context, prices, fleet_price, phase_one=phase_one)
        assert priced['complete']
        if trips:
            assert priced['minimum_reduced_cost'] == pytest.approx(expected)
            assert priced['lower_bound'] <= expected+1e-9
        else:
            assert priced['infeasible']
        oracle = {c['tasks']: c for c in trips}
        for c in priced['columns']:
            assert c['cost'] == pytest.approx(oracle[c['tasks']]['cost'])
            assert c['starts'] == oracle[c['tasks']]['starts']


@pytest.mark.parametrize('limit', [1, 3, 7, 20])
@pytest.mark.parametrize('phase_one', [False, True])
def test_interrupted_frontier_bound_never_exceeds_true_pricing_minimum(limit, phase_one):
    p = tiny_problem(2, 1)
    _, _, by_site = contexts(p)
    for context in by_site.values():
        prices = {n['key']: 11+i*5 for i, n in enumerate(context['nodes'])}
        expected = min((0 if phase_one else c['cost'])-sum(prices[k] for k in c['tasks'])+2
                       for c in independently_enumerated_trips(p, context))
        r = price_delivery_routes(p, context, prices, -2, phase_one=phase_one, label_limit=limit)
        assert r['lower_bound'] <= expected+1e-9
        expired = price_delivery_routes(p, context, prices, -2, phase_one=phase_one, deadline=time.perf_counter()-1)
        assert not expired['complete'] and expired['lower_bound'] <= expected+1e-9


@pytest.mark.parametrize('model', [1, 2])
@pytest.mark.parametrize('seed', range(8))
def test_root_certificate_does_not_exceed_independent_original_integer_optimum(model, seed):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model, seed)
    optimum = enumerate_tiny(p)
    r = root_route_bound(p, time_limit=3)
    assert r['proven_optimal'] is False
    if math.isfinite(optimum):
        assert not r['proven_infeasible']
        assert r['bound'] <= optimum+1e-8
        assert r['relaxation_complete']


@pytest.mark.parametrize('seed', [0, 1, 4, 5])
def test_column_generation_matches_fully_enumerated_relaxed_lp(seed):
    gp = pytest.importorskip('gurobipy')
    p = tiny_problem(2, seed)
    domains, travel, by_site = contexts(p)
    tours = e1_delivery_tours(p, domains, travel)
    master = DeliveryMaster(p, domains, by_site, tours)
    try:
        for site, context in by_site.items():
            for c in independently_enumerated_trips(p, context):
                master.add(site, c)
        master.phase_two()
        master.model.optimize()
        assert master.model.Status == gp.GRB.OPTIMAL
        r = root_route_bound(p, time_limit=3)
        assert r['relaxation_complete']
        assert r['restricted_objective'] == pytest.approx(master.model.ObjVal)
    finally:
        master.dispose()


def test_pricing_extends_nonmetric_prefix_with_infeasible_direct_return():
    p = tiny_problem(2, 0)
    site, a, b = 'S', p.orders[0].customer, p.orders[1].customer
    # Singleton a cannot return; a -> b -> S is feasible.
    p._travel[a, site] = 1000
    p.network.dist[p.network.node_index[a]][p.network.node_index[site]] = 1000
    _, _, by_site = contexts(p)
    context = by_site[site]
    assert evaluate_delivery_column(p, context, (p.orders[0].id,)) is None
    expected = evaluate_delivery_column(p, context, tuple(o.id for o in p.orders))
    assert expected is not None
    r = price_delivery_routes(p, context, {o.id: 100 for o in p.orders})
    assert r['complete']
    assert any(c['tasks'] == expected['tasks'] for c in r['columns'])


def test_duplicate_location_is_not_a_duplicate_task_and_assignment_is_shared():
    pytest.importorskip('gurobipy')
    p = one_customer(2, duplicate=True, window=(0, 5), pool=2)
    _, _, by_site = contexts(p)
    c = evaluate_delivery_column(p, by_site['T'], ('a', 'b'))
    assert c is not None
    r = root_route_bound(p, time_limit=2)
    assert r['relaxation_complete'] and 3.99 < r['bound'] <= 4


@pytest.mark.parametrize('zero', ['container_weight', 'container_volume'])
def test_zero_resource_dimension_preserves_pricing_bound(zero):
    p = tiny_problem(2, 1)
    setattr(p.fleet, zero, 0)
    _, _, by_site = contexts(p)
    for context in by_site.values():
        prices = {o.id: 20 for o in p.orders}
        expected = min(c['cost']-sum(prices[k] for k in c['tasks']) for c in independently_enumerated_trips(p, context))
        r = price_delivery_routes(p, context, prices)
        assert r['minimum_reduced_cost'] == pytest.approx(expected)
        assert r['lower_bound'] <= expected+1e-9


def test_missing_columns_require_dual_correction_and_not_restricted_objective():
    assert corrected_master_bound(100, {'S': -10, 'T': 5}, {'S': 3, 'T': 2}, margin=.1) == pytest.approx(69.9)
    assert corrected_master_bound(100, {'S': -100}, {'S': 3}) == 0


def test_partial_e1_enumeration_cannot_supply_a_new_certificate():
    pytest.importorskip('gurobipy')
    r = root_route_bound(tiny_problem(2, 1), time_limit=2, tour_limit=1)
    assert r['status'] == 'TIME_LIMIT_BUILD'
    assert r['bound'] == r['baseline_bound']
    assert not r['proven_infeasible'] and not r['relaxation_complete']


@pytest.mark.parametrize('model', [3, 4])
def test_pickup_models_are_explicitly_rejected(model):
    with pytest.raises(ValueError, match='M1/M2'):
        root_route_bound(tiny_problem(model, 1))


@pytest.mark.parametrize('model', [1, 2])
def test_hybrid_retains_complete_integer_solver_and_audit(model):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model, 1)
    r = solve_with_route_bound(p, time_limit=3, root_time=.5)
    assert audit_solution(p, r)['valid']
    assert r['info']['proven_optimal']
    assert r['info']['objective'] == pytest.approx(enumerate_tiny(p))
    assert r['info']['route_bound_certificate']['proven_optimal'] is False


def test_root_only_timeout_keeps_audited_start_without_transferring_old_proof(monkeypatch):
    from models.routing.solvers.exact import single_batch_columns as module
    from models.routing.solvers.exact.solver import solve_exact
    p = tiny_problem(2, 1)
    initial = solve_exact(p, time_limit=2)
    assert initial['info']['proven_optimal']
    original = module.root_route_bound
    def consume_budget(*args, **kwargs):
        r = original(*args, **kwargs)
        time.sleep(.02)
        return r
    monkeypatch.setattr(module, 'root_route_bound', consume_budget)
    r = solve_with_route_bound(p, time_limit=.01, root_time=.01, initial_result=initial)
    assert r['audit']['valid']
    assert not r['info']['proven_optimal']
    assert r['info']['status'] == 'TIME_LIMIT_ROOT_COLUMNS'


@pytest.mark.parametrize('seed', range(6))
@pytest.mark.parametrize('phase_one', [False, True])
def test_mip_pricer_matches_independent_route_enumeration(seed, phase_one):
    pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.single_batch_delivery_pricer import DeliveryMipPricer
    p = tiny_problem(2, seed)
    _, _, by_site = contexts(p)
    for context in by_site.values():
        prices = {n['key']: 10+i*3 for i, n in enumerate(context['nodes'])}
        oracle = list(independently_enumerated_trips(p, context))
        pricer = DeliveryMipPricer(p, context)
        try:
            r = pricer.price(prices, -2, phase_one=phase_one)
            assert r['complete']
            if oracle:
                expected = min((0 if phase_one else c['cost'])-sum(prices[k] for k in c['tasks'])+2 for c in oracle)
                assert r['minimum_reduced_cost'] == pytest.approx(expected)
                assert r['lower_bound'] <= expected+1e-9
            else:
                assert r['infeasible']
        finally:
            pricer.dispose()


@pytest.mark.parametrize('seed', [0, 1, 4, 5])
def test_mip_and_label_root_prototypes_match_the_same_relaxation(seed):
    pytest.importorskip('gurobipy')
    p = tiny_problem(2, seed)
    label = root_route_bound(p, time_limit=3)
    mip = root_route_bound(p, time_limit=3, pricing_method='mip')
    assert label['relaxation_complete'] and mip['relaxation_complete']
    assert label['restricted_objective'] == pytest.approx(mip['restricted_objective'])


def test_wrong_instance_certificate_cannot_add_a_distance_floor():
    gp = pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.single_batch_columns import apply_route_bound
    p = tiny_problem(2, 1)
    r = root_route_bound(p, time_limit=2)
    model = gp.Model()
    model.Params.OutputFlag = 0
    try:
        p._travel[p.depot, p.satellites[0]] += 1
        with pytest.raises(ValueError, match='does not match'):
            apply_route_bound(model, p, r)
    finally:
        model.dispose()


def test_fixed_orders_with_conflicting_customer_owners_are_explicitly_unsupported():
    p = one_customer(1, duplicate=True, pool=2)
    p = MultiBatchInstance(p.network, [p.orders[0], replace(p.orders[1], satellite='T')], p.operating_policy, model=1)
    with pytest.raises(ValueError, match='one input warehouse'):
        root_route_bound(p)


def test_mip_pricer_timeout_retains_an_analytic_bound_and_no_completion_claim():
    pytest.importorskip('gurobipy')
    from models.routing.solvers.exact.single_batch_delivery_pricer import DeliveryMipPricer
    p = tiny_problem(2, 1)
    _, _, by_site = contexts(p)
    context = next(iter(by_site.values()))
    prices = {o.id: 20 for o in p.orders}
    expected = min(c['cost']-sum(prices[k] for k in c['tasks']) for c in independently_enumerated_trips(p, context))
    pricer = DeliveryMipPricer(p, context)
    try:
        r = pricer.price(prices, deadline=time.perf_counter()-1)
        assert not r['complete']
        assert r['lower_bound'] <= expected+1e-9
    finally:
        pricer.dispose()


def test_root_timeout_never_imports_fixed_partition_warm_start_bound(monkeypatch):
    from models.routing.solvers.exact import single_batch_columns as module
    from models.routing.solvers.exact.solver import solve_exact
    from models.routing.common.cycle.assignment import assigned_problem, lift_result
    p = one_customer(2)
    assignment = {'C': 'S'}
    warm = lift_result(p, solve_exact(assigned_problem(p, assignment), time_limit=2), assignment)
    assert warm['audit']['valid'] and warm['info']['best_bound'] > 4
    original = module.root_route_bound
    def consume_budget(*args, **kwargs):
        r = original(*args, **kwargs)
        time.sleep(.02)
        return r
    monkeypatch.setattr(module, 'root_route_bound', consume_budget)
    r = solve_with_route_bound(p, time_limit=.01, root_time=.01, initial_result=warm)
    assert r['audit']['valid'] and not r['info']['proven_optimal']
    assert r['info']['best_bound'] <= 4 < r['info']['objective']


@pytest.mark.parametrize('seed', range(6))
@pytest.mark.parametrize('phase_one', [False, True])
def test_task_count_correction_bounds_every_independently_enumerated_column(seed, phase_one):
    from models.routing.solvers.exact.single_batch_columns import short_route_catalog, task_price_floor
    p = tiny_problem(2, seed)
    _, _, by_site = contexts(p)
    for context in by_site.values():
        prices = {n['key']: 7+i*4 for i, n in enumerate(context['nodes'])}
        fleet = -3
        columns = list(independently_enumerated_trips(p, context))
        reduced = [(0 if phase_one else c['cost'])-sum(prices[k] for k in c['tasks'])-fleet for c in columns]
        lower = min(reduced, default=0)-1
        floor = task_price_floor(prices, fleet, lower, short_route_catalog(p, context), phase_one=phase_one)
        for c, rc in zip(columns, reduced):
            assert floor*len(c['tasks']) <= rc+1e-9


def test_short_route_certificate_enumeration_follows_its_deadline():
    from models.routing.solvers.exact.single_batch_columns import short_route_catalog
    p = tiny_problem(2, 1)
    _, _, by_site = contexts(p)
    with pytest.raises(TimeoutError, match='Short-route'):
        short_route_catalog(p, next(iter(by_site.values())), deadline=time.perf_counter()-1)
