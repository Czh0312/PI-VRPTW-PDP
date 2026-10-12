"""Verify complete native pricing against independently enumerated task paths."""
import gzip
import json
import math
import random
import time

import pytest

from models.common.cycle import MultiBatchInstance
from models.routing.model_5.exact.multibatch_route_cg import route_cover_certificate
from models.routing.model_5.exact.multibatch_route_enumeration import enumerate_route_universe, EnumeratedRoutePricer
from models.routing.model_5.exact.pickup_route_pricing import evaluate_column, route_context
from tests.routing.test_multibatch_cycle import fixture, hand_solution
from tests.routing.test_multibatch_departure_loads import nonmetric_problem
from tests.routing.test_multibatch_route_cg import enumerate_trips, enumerated_lp, detour_problem


@pytest.mark.parametrize('seed', range(6))
@pytest.mark.parametrize('phase_one', [False, True])
def test_complete_task_sets_and_pricing_match_independent_all_path_oracle(seed, phase_one):
    p = nonmetric_problem(seed)
    rng = random.Random(712+seed)
    for site in p.satellites:
        ctx = route_context(p, site)
        if not ctx['nodes']:
            continue
        oracle = enumerate_trips(p, site)
        expected = {}
        for sequence, cost in oracle:
            key = frozenset(sequence)
            expected[key] = min(expected.get(key, math.inf), cost)
        universe = enumerate_route_universe(p, ctx)
        assert universe['complete']
        actual = {frozenset(c['tasks']): c['cost'] for g in universe['groups'] for c in g['columns']}
        assert set(actual) == set(expected)
        for key in expected:
            assert actual[key] == pytest.approx(expected[key], abs=1e-8)
        pi = {node['key']: rng.uniform(-10, 30) for node in ctx['nodes']}
        nu = -3.5
        priced = EnumeratedRoutePricer(universe).price(pi, nu, phase_one=phase_one)
        value = min((0 if phase_one else cost)-sum(pi[k] for k in seq)-nu for seq, cost in oracle)
        assert priced['optimal'] and priced['raw_lower_bound'] == pytest.approx(value, abs=1e-8)
        assert priced['lower_bound'] <= value+1e-8
        c = priced['column']
        replayed = evaluate_column(p, ctx, c['tasks'])
        assert replayed is not None and replayed['cost'] == pytest.approx(c['cost'])
        assert replayed['starts'] == c['starts'] and replayed['return_complete'] == c['return_complete']


@pytest.mark.parametrize('seed', range(6))
def test_complete_cover_lp_matches_oracle_and_never_claims_integer_optimality(seed):
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.engine import solve_exact
    p = nonmetric_problem(seed)
    cert = route_cover_certificate(p, pricing_backend='hybrid', e1_time_limit=1, time_limit=5)
    assert cert['status'] == 'LP_CONVERGED'
    for row in cert['sites']:
        if row['status'] == 'EMPTY':
            continue
        opt = enumerated_lp(p, row['satellite'], enumerate_trips(p, row['satellite']))
        assert opt-1e-3 <= row['bound'] <= opt+1e-6
        assert row['enumeration']['complete'] and row['pricing_backend'] == 'complete_enumeration'
    full = solve_exact(p, time_limit=5)
    assert full['info']['proven_optimal'] and cert['bound'] <= full['info']['objective']+1e-6
    assert not cert['proven_optimal'] and not cert['proven_infeasible']


def test_nonmetric_return_detours_are_not_pruned_by_direct_return_time():
    p = detour_problem()
    i = p.network.node_index
    p.network.time[i['S']][i['A']] = 1
    p.network.time[i['A']][i['S']] = 110
    p = MultiBatchInstance(p.network, p.orders, p.operating_policy)
    ctx = route_context(p, 'S')
    assert evaluate_column(p, ctx, [('a', 'p')]) is None
    assert evaluate_column(p, ctx, [('a', 'p'), ('b', 'p')]) is not None
    universe = enumerate_route_universe(p, ctx)
    oracle = enumerate_trips(p, 'S')
    expected = {frozenset(seq) for seq, _ in oracle}
    actual = {frozenset(c['tasks']) for g in universe['groups'] for c in g['columns']}
    assert universe['complete'] and actual == expected


def test_delivery_only_return_after_pickup_cutoff_is_retained():
    p = detour_problem()
    i = p.network.node_index
    p.network.time[i['S']][i['A']] = 1
    p.network.time[i['A']][i['S']] = 110
    p = MultiBatchInstance(p.network, p.orders, p.operating_policy)
    universe = enumerate_route_universe(p, route_context(p, 'S'))
    column = next(c for g in universe['groups'] for c in g['columns'] if c['tasks'] == (('a', 'd'),))
    assert p.operating_policy.batches[-1].cutoff < column['return_complete'] <= p.operating_policy.cycle_end


def test_initial_delivery_load_counts_future_deliveries_at_earlier_pickups():
    p = detour_problem()
    p.fleet.e2_volume_cap = 1
    ctx = route_context(p, 'S')
    assert evaluate_column(p, ctx, [('b', 'p'), ('a', 'd')]) is None
    universe = enumerate_route_universe(p, ctx)
    assert universe['complete']
    for g in universe['groups']:
        for c in g['columns']:
            assert evaluate_column(p, ctx, c['tasks']) is not None


def test_incomplete_universe_never_certifies_and_full_milp_fallback_remains():
    pytest.importorskip('gurobipy')
    p = fixture(pool=2)
    universe = enumerate_route_universe(p, route_context(p, 'S'), max_states=1)
    assert not universe['complete']
    with pytest.raises(ValueError, match='partial'):
        EnumeratedRoutePricer(universe)
    cert = route_cover_certificate(p, time_limit=5, initial_result=hand_solution(p),
        pricing_backend='hybrid', enumeration_max_states=1)
    row = cert['sites'][0]
    assert not row['enumeration']['complete'] and row['pricing_backend'] == 'milp'
    assert row['lp_converged'] and row['bound'] <= enumerated_lp(p, 'S', enumerate_trips(p, 'S'))+1e-6
    assert not cert['proven_optimal']


def test_deadline_and_parameter_validation():
    p = fixture(pool=2)
    universe = enumerate_route_universe(p, route_context(p, 'S'), deadline=0)
    assert not universe['complete'] and universe['states'] == 0
    for v in (0, True, 1.5):
        with pytest.raises(ValueError):
            enumerate_route_universe(p, route_context(p, 'S'), max_states=v)
        with pytest.raises(ValueError):
            route_cover_certificate(p, enumeration_max_states=v)
    for v in (0, -1, math.inf, math.nan):
        with pytest.raises(ValueError):
            route_cover_certificate(p, enumeration_time_limit=v)
    with pytest.raises(ValueError, match='pricing_backend'):
        route_cover_certificate(p, pricing_backend='partial')
    cert = route_cover_certificate(p, pricing_backend='hybrid', time_limit=1e-12)
    assert cert['bound'] == 0 and not cert['proven_optimal']


def test_full_route_cover_exports_complete_universe_and_replayable_lp(tmp_path):
    gp = pytest.importorskip('gurobipy')
    from models.routing.model_5.exact import route_cover_certificate as public
    p = fixture(pool=2)
    cert = public(p, time_limit=5, initial_result=hand_solution(p), pricing_backend='hybrid', diagnostics_dir=tmp_path)
    universes = list(tmp_path.glob('*_best_pricing.json.gz'))
    masters = list(tmp_path.glob('*_best_pricing_master.mps.gz'))
    assert cert['status'] == 'LP_CONVERGED' and len(universes) == len(masters) == 1
    with gzip.open(universes[0], 'rt') as stream:
        universe = json.load(stream)
    assert universe['complete']
    m = gp.read(str(masters[0]))
    try:
        m.Params.OutputFlag = 0
        m.optimize()
        assert m.Status == gp.GRB.OPTIMAL
        assert m.ObjVal-1e-3 <= cert['sites'][0]['bound'] <= m.ObjVal+1e-6
    finally:
        m.dispose()
