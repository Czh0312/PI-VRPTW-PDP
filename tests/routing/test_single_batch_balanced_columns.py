"""Independent route universes and original-integer checks for guided columns."""
from dataclasses import replace
import math
import random
import time

import pytest

from models.common.cycle import MultiBatchInstance
from models.common.cycle.audit import audit_solution
from models.routing.model_4.exact.single_batch_columns import problem_fingerprint
from models.routing.model_4.exact.single_batch_pickup_bound import PickupCoverMaster, balanced_order_prices, pickup_contexts, project_original_schedule, root_pickup_route_bound, supply_balanced_columns
from models.routing.model_4.exact.pickup_route_pricing import RoutePricer, evaluate_column
from models.routing.model_4.exact.engine import solve_exact
from tests.routing.test_single_batch_pickup_bound import independent_trips, fully_enumerated_joint_lp
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny


def instance(seed, duplicate):
    base = tiny_problem(4, seed)
    if not duplicate:
        return base
    return MultiBatchInstance(base.network, [base.orders[0], replace(base.orders[1],
        customer=base.orders[0].customer)], base.operating_policy, model=4)


@pytest.mark.parametrize('seed', [0, 1, 4, 5])
@pytest.mark.parametrize('duplicate', [False, True])
def test_both_price_vectors_reprice_against_independent_complete_routes(seed, duplicate):
    p = instance(seed, duplicate)
    _, contexts, _ = pickup_contexts(p)
    rng = random.Random(7405 + seed)
    for site, context in contexts.items():
        oracle = independent_trips(p, site, context)
        prices = {n['key']: rng.uniform(-100, 150) for n in context['nodes']}
        before = dict(prices)
        balanced = balanced_order_prices(context, prices)
        assert before == prices and set(balanced) == set(prices)
        for order in p.orders:
            assert math.fsum(balanced[order.id, k] for k in ('d', 'p')) == pytest.approx(
                math.fsum(prices[order.id, k] for k in ('d', 'p')))
        pricer = RoutePricer(p, context, seed=42)
        try:
            # Same model: balanced -> original must restore every objective term.
            for vector in (balanced, prices):
                minimum = min(cost-math.fsum(vector[k] for k in seq)+2.5 for seq, cost in oracle)
                result = pricer.price(vector, -2.5, time_limit=3)
                assert result['optimal']
                assert result['reduced_cost'] == pytest.approx(minimum, abs=1e-6)
                assert result['lower_bound'] <= minimum + 1e-7
        finally:
            pricer.dispose()


@pytest.mark.parametrize('seed', [0, 1, 4, 5])
@pytest.mark.parametrize('duplicate', [False, True])
def test_guided_root_matches_independent_full_lp_and_original_integer(seed, duplicate):
    p = instance(seed, duplicate)
    fp = problem_fingerprint(p)
    domains, contexts, _ = pickup_contexts(p)
    expected = fully_enumerated_joint_lp(p, domains, contexts)
    optimum = enumerate_tiny(p)
    original = solve_exact(p, time_limit=3)
    assert original['info']['proven_optimal']
    assert original['info']['objective'] == pytest.approx(optimum)
    audit = audit_solution(p, original)
    assert audit['valid'] and audit['periodic_replay']['valid']
    project_original_schedule(p, original, contexts)
    result = root_pickup_route_bound(p, time_limit=5, initial_result=original, balanced_columns=True)
    assert problem_fingerprint(p) == fp
    assert result['relaxation_complete']
    assert result['restricted_e2_objective'] == pytest.approx(expected, abs=1e-6)
    assert result['e2_bound'] <= expected + 1e-7
    assert result['bound'] <= optimum + 1e-7
    assert not result['proven_optimal'] and not result['proven_infeasible']
    for discovery in result['balanced_history']:
        assert discovery['proof_scope'] == 'discovery_only'
        assert discovery['corrected_e2_bound'] is None
        for site, data in discovery['sites'].items():
            oracle = dict(independent_trips(p, site, contexts[site]))
            prices = {tuple(k): v for k, v in data['original_prices']}
            for column in data['columns']:
                sequence = tuple(tuple(k) for k in column['tasks'])
                cost = oracle[sequence]
                assert column['cost'] == pytest.approx(cost)
                rc = cost - math.fsum(prices[k] for k in sequence) - data['fleet_price']
                assert column['original_reduced_cost'] == pytest.approx(rc)
                if column['added']:
                    assert rc < -1e-7
    # Every original certificate is independently checked with the full universe.
    for history in result['history']:
        for site, values in history['prices'].items():
            prices = {tuple(k): v for k, v in values['tasks']}
            minimum = min((0 if history['phase'] == 1 else cost)
                - math.fsum(prices[k] for k in seq) - values['fleet']
                for seq, cost in independent_trips(p, site, contexts[site]))
            assert history['pricing_lower_bounds'][site] <= minimum + 1e-7


def test_discovery_filters_original_rc_deduplicates_and_ignores_poisoned_certificate():
    p = instance(0, True)
    domains, contexts, _ = pickup_contexts(p)
    site, context = next(iter(contexts.items()))
    order = p.orders[0]
    d = evaluate_column(p, context, [(order.id, 'd')])
    pickup = evaluate_column(p, context, [(order.id, 'p')])
    assert d and pickup
    prices = {n['key']: 0.0 for n in context['nodes']}
    prices[order.id, 'd'], prices[order.id, 'p'] = -100.0, 1000.0

    class PoisonPricer:
        def price(self, *args, **kwargs):
            return dict(optimal=True, infeasible=False, lower_bound=1e99,
                        reduced_cost=1e99, columns=[d, pickup, pickup])

    master = PickupCoverMaster(p, domains, contexts)
    try:
        master.phase_two()
        record = supply_balanced_columns(p, {site: context}, master, {site: PoisonPricer()},
            {site: (prices, 0.0)}, deadline=time.perf_counter()+2, call_cap=1)
        assert record['added'] == 1 and record['returned'] == 3
        assert record['original_negative'] == 2
        assert record['corrected_e2_bound'] is None
        assert len(master.columns) == 1
        cols = record['sites'][site]['columns']
        assert cols[0]['balanced_reduced_cost'] < 0 < cols[0]['original_reduced_cost']
        assert not cols[0]['added'] and cols[1]['added'] and not cols[2]['added']
    finally:
        master.dispose()


def test_cross_trip_original_physical_vehicle_is_not_collapsed():
    from tests.routing.test_single_batch_primal import split_problem
    from models.routing.model_4.exact.initial_solution import construct_incumbent
    from models.common.cycle.assignment import lift_result, input_assignment
    base = split_problem()
    p = MultiBatchInstance(base.network, base.orders, base.operating_policy, model=4)
    fixed_start = construct_incumbent(base, time.perf_counter()+2)
    assert fixed_start
    start = lift_result(p, fixed_start, input_assignment(p))
    assert start and audit_solution(p, start)['valid']
    _, contexts, _ = pickup_contexts(p)
    columns = project_original_schedule(p, start, contexts)
    assert len(columns) > 1
    result = root_pickup_route_bound(p, time_limit=5, initial_result=start, balanced_columns=True)
    assert result['bound'] <= start['info']['objective'] + 1e-7
    assert not result['proven_optimal']


def test_budget_exhaustion_does_not_promote_a_restricted_or_guided_value():
    p = instance(0, False)
    result = root_pickup_route_bound(p, time_limit=1e-6, balanced_columns=True)
    assert not result['relaxation_complete'] and not result['proven_optimal']
    assert result['bound'] <= enumerate_tiny(p) + 1e-7


@pytest.mark.parametrize('value', [1, 'yes', None])
def test_bad_flag(value):
    with pytest.raises(ValueError, match='boolean'):
        root_pickup_route_bound(instance(0, False), balanced_columns=value)


def test_scope_and_strategy_are_explicit():
    with pytest.raises(ValueError, match='M4'):
        root_pickup_route_bound(tiny_problem(3, 0), balanced_columns=True)
    with pytest.raises(ValueError, match='one discovery'):
        root_pickup_route_bound(instance(0, False), balanced_columns=True, heuristic_columns=True)


@pytest.mark.parametrize('bad', ['missing', 'infinite', 'nan'])
def test_price_transform_rejects_missing_or_nonfinite_tasks(bad):
    _, contexts, _ = pickup_contexts(instance(0, True))
    context = next(iter(contexts.values()))
    prices = {key: 1.0 for key in context['lookup']}
    key = next(iter(prices))
    if bad == 'missing':
        del prices[key]
    else:
        prices[key] = math.inf if bad == 'infinite' else math.nan
    with pytest.raises(ValueError):
        balanced_order_prices(context, prices)
