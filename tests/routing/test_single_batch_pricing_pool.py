"""Pricing solution pools are discovery; only corrected pricing bounds certify."""
import math
import random
import time
from copy import deepcopy

import pytest

from models.common.cycle.audit import audit_solution
from models.routing.model_1.exact.accelerators.single_batch.columns import root_route_bound, solve_with_route_bound
from models.routing.model_1.exact.accelerators.single_batch.delivery_pricer import DeliveryMipPricer
from tests.routing.test_single_batch_columns import contexts, independently_enumerated_trips
from tests.routing.test_single_batch_time_budget import manual_work
from tests.routing.test_single_batch_dual_cuts import check_all_partitions
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny


@pytest.mark.parametrize('seed', range(8))
@pytest.mark.parametrize('phase_one', [False, True])
@pytest.mark.parametrize('early', [False, True])
def test_every_pool_column_and_bound_match_independent_permutations(seed, phase_one, early):
    p = tiny_problem(2, seed)
    for site, ctx in contexts(p)[2].items():
        rng = random.Random(seed+7)
        prices = {o.id: rng.uniform(5, 30) for o in p.orders}
        weights = [((0, 30), .2), ((8, 65), .3)]
        oracle = {c['tasks']: c for c in independently_enumerated_trips(p, ctx)}
        def cost(c):
            return (0 if phase_one else c['cost'])+sum(v*manual_work(p, site, c['tasks'], b) for b, v in weights) \
                -sum(prices[k] for k in c['tasks'])+2
        expected = min(map(cost, oracle.values()), default=math.inf)
        pricer = DeliveryMipPricer(p, ctx)
        try:
            r = pricer.price(prices, -2, phase_one=phase_one, work_prices=weights,
                             column_limit=16, stop_after_negative=early)
            assert r['lower_bound'] <= expected+1e-8
            if r['complete'] and math.isfinite(expected):
                assert r['minimum_reduced_cost'] == pytest.approx(expected)
            if r['stopped_on_negative']:
                assert not r['complete'] and not r['infeasible']
            assert len(r['columns']) == len({c['tasks'] for c in r['columns']})
            for c in r['columns']:
                assert c['tasks'] in oracle
                assert c['cost'] == pytest.approx(oracle[c['tasks']]['cost'])
                assert c['starts'] == oracle[c['tasks']]['starts']
                assert cost(c) < -1e-7
            assert pricer.model.Params.SolutionNumber == 0
            # Reuse after early stopping and a changed objective. The stop
            # parameter must reset, and all prices/columns must be refreshed.
            r = pricer.price({o.id: -2 for o in p.orders}, column_limit=1)
            assert r['complete'] and not r['columns']
            assert pricer.model.Params.BestObjStop < -1e90
        finally:
            pricer.dispose()


def test_suboptimal_solutions_supply_multiple_distinct_valid_columns():
    p = tiny_problem(2, 1)
    ctx = contexts(p)[2]['S']
    pricer = DeliveryMipPricer(p, ctx)
    try:
        # Exhaustive pool search only in this tiny verification. The runtime
        # algorithm retains the default opportunistic solution pool search.
        pricer.model.Params.PoolSearchMode = 2
        r = pricer.price({o.id: 100 for o in p.orders}, column_limit=16)
        assert len(r['columns']) > 1
        assert len({c['tasks'] for c in r['columns']}) == len(r['columns'])
    finally:
        pricer.dispose()


@pytest.mark.parametrize('mode', ['pool', 'negative'])
@pytest.mark.parametrize('model', [1, 2])
@pytest.mark.parametrize('seed', range(8))
def test_root_still_solves_the_same_relaxation_and_bounds_the_original(mode, model, seed):
    p = tiny_problem(model, seed)
    expected = enumerate_tiny(p)
    r = root_route_bound(p, time_limit=3, pricing_method='mip', mip_strategy=mode, dual_cuts=True)
    old = root_route_bound(p, time_limit=3, pricing_method='mip')
    assert not r['proven_optimal']
    if math.isfinite(expected):
        assert not r['proven_infeasible'] and r['relaxation_complete']
        assert r['bound'] <= expected+1e-8
        assert r['restricted_objective'] == pytest.approx(old['restricted_objective'])
        assert check_all_partitions(p, r) > 0
    else:
        assert r['proven_infeasible'] == old['proven_infeasible']


@pytest.mark.parametrize('early', [False, True])
def test_zero_remaining_pricing_budget_does_not_claim_pool_completion(early):
    p = tiny_problem(2, 1)
    ctx = contexts(p)[2]['S']
    pricer = DeliveryMipPricer(p, ctx)
    try:
        pricer.price({o.id: 30 for o in p.orders}, column_limit=16, stop_after_negative=early)
        prices = {o.id: 40 for o in p.orders}
        expected = min(c['cost']-sum(prices[k] for k in c['tasks']) for c in independently_enumerated_trips(p, ctx))
        r = pricer.price(prices, deadline=time.perf_counter()-1, column_limit=16, stop_after_negative=early)
        assert not r['complete'] and r['lower_bound'] <= expected+1e-8
    finally:
        pricer.dispose()


@pytest.mark.parametrize('mode', ['pool', 'negative'])
def test_full_hybrid_retains_the_original_integer_optimality_contract(mode):
    p = tiny_problem(2, 1)
    r = solve_with_route_bound(p, time_limit=4, root_time=1, pool_time=.5,
        pricing_method='mip', mip_strategy=mode, dual_cuts=True)
    assert audit_solution(p, r)['valid'] and r['info']['proven_optimal']
    assert r['info']['objective'] == pytest.approx(enumerate_tiny(p))


@pytest.mark.parametrize('mode', ['pool', 'negative', 'bad'])
def test_invalid_or_mismatched_pricing_strategies_are_rejected(mode):
    with pytest.raises(ValueError, match='strategy|strategies'):
        root_route_bound(tiny_problem(2, 1), mip_strategy=mode)


@pytest.mark.parametrize('limit', [0, True, 1.5])
def test_pool_column_limit_is_validated(limit):
    p = tiny_problem(2, 1)
    pricer = DeliveryMipPricer(p, contexts(p)[2]['S'])
    try:
        with pytest.raises(ValueError, match='column limit'):
            pricer.price({}, column_limit=limit)
    finally:
        pricer.dispose()


@pytest.mark.parametrize('root_elapsed', [4, 9, 10])
@pytest.mark.parametrize('reuse', [False, True])
def test_unused_root_time_can_help_the_primal_stage_without_extending_total_budget(monkeypatch, root_elapsed, reuse):
    from models.routing.model_1.exact.accelerators.single_batch import columns as cg
    from models.routing.model_1.exact.accelerators.single_batch import route_pool as primal
    from models.routing.model_1.exact.search import engine as solver
    p = tiny_problem(2, 0)
    initial = solver.solve_exact(p, time_limit=2)
    assert audit_solution(p, initial)['valid']
    now, seen = [0.0], {}
    def root(*args, **kwargs):
        now[0] += root_elapsed
        return dict(bound=0, proven_infeasible=False, proof_scope='B1_delivery_route_relaxation',
                    _route_pool={})
    def pool(*args, **kwargs):
        seen['pool'] = kwargs['deadline']-now[0]
        assert kwargs['deadline'] <= 30
        now[0] = kwargs['deadline']
        return None, {}
    def full(*args, **kwargs):
        seen['full'] = kwargs['time_limit']
        now[0] += kwargs['time_limit']
        r = deepcopy(initial)
        r['info'].update(proven_optimal=False, proven_infeasible=False)
        return r
    monkeypatch.setattr(cg.time, 'perf_counter', lambda: now[0])
    monkeypatch.setattr(cg, 'root_route_bound', root)
    monkeypatch.setattr(primal, 'route_pool_incumbent', pool)
    monkeypatch.setattr(solver, 'solve_exact', full)
    r = cg.solve_with_route_bound(p, time_limit=30, root_time=10, pool_time=3,
                                  initial_result=initial, reuse_root_time=reuse)
    assert seen['pool'] >= 3 and seen['pool'] <= 6
    if root_elapsed < 10 and reuse:
        assert seen['pool'] > 3
    else:
        assert seen['pool'] == 3
    assert seen['full'] > 0 and r['info']['runtime'] == 30
    assert audit_solution(p, r)['valid'] and not r['info']['proven_optimal']
