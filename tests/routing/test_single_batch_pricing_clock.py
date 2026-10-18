"""Independent binary-route enumeration for continuous pricing clocks."""
from copy import deepcopy
from dataclasses import replace
import itertools
import math
import random
import time

import pytest

from models.common.cycle import MultiBatchInstance
from models.common.cycle.audit import audit_solution
from models.routing.model_1.exact.accelerators.single_batch.columns import root_route_bound, solve_with_route_bound
from models.routing.model_1.exact.accelerators.single_batch.delivery_pricer import DeliveryMipPricer
from tests.routing.test_single_batch_columns import contexts, independently_enumerated_trips
from tests.routing.test_single_batch_dual_cuts import check_all_partitions
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny
from tests.routing.test_single_batch_time_budget import manual_work


@pytest.mark.parametrize('seed', range(8))
@pytest.mark.parametrize('model', [1, 2])
def test_each_elementary_route_has_same_integer_and_continuous_clock_feasibility(model, seed):
    gp = pytest.importorskip('gurobipy')
    p = tiny_problem(model, seed)
    for ctx in contexts(p)[2].values():
        oracle = {c['tasks'] for c in independently_enumerated_trips(p, ctx)}
        keys = [n['key'] for n in ctx['nodes']]
        pricers = [DeliveryMipPricer(p, ctx, continuous_time=enabled) for enabled in (False, True)]
        try:
            for pricer in pricers:
                pricer.model.setObjective(0)
                assert all(v.VType == ('C' if pricer.continuous_time else 'I') for v in pricer.times.values())
            for count in range(1, len(keys)+1):
                for tasks in itertools.permutations(keys, count):
                    for pricer in pricers:
                        if any(key not in pricer.context['lookup'] for key in tasks):
                            assert tasks not in oracle
                            continue
                        indices = [pricer.context['lookup'][key] for key in tasks]
                        selected = {(-1, indices[0]), (indices[-1], -2), *zip(indices, indices[1:])}
                        if not selected <= set(pricer.arcs):
                            assert tasks not in oracle
                            continue
                        for i, v in pricer.visits.items():
                            v.LB = v.UB = int(i in indices)
                        for arc, v in pricer.arcs.items():
                            v.LB = v.UB = int(arc in selected)
                        pricer.model.optimize()
                        assert (pricer.model.Status == gp.GRB.OPTIMAL) == (tasks in oracle), tasks
        finally:
            for pricer in pricers:
                pricer.dispose()


@pytest.mark.parametrize('seed', range(8))
@pytest.mark.parametrize('phase_one', [False, True])
@pytest.mark.parametrize('weighted', [False, True])
@pytest.mark.parametrize('strategy', ['optimal', 'pool', 'negative'])
def test_reduced_cost_and_all_pool_columns_match_raw_permutations(seed, phase_one, weighted, strategy):
    pytest.importorskip('gurobipy')
    p = tiny_problem(2, seed)
    for site, ctx in contexts(p)[2].items():
        rng = random.Random(seed+19)
        prices = {key: rng.uniform(5, 40) for key in ctx['lookup']}
        weights = [((0,30),.2), ((8,65),.3)] if weighted else []
        oracle = {c['tasks']:c for c in independently_enumerated_trips(p, ctx)}
        def cost(c):
            return (0 if phase_one else c['cost'])+sum(v*manual_work(p,site,c['tasks'],b) for b,v in weights) \
                -sum(prices[k] for k in c['tasks'])+2
        expected = min(map(cost, oracle.values()), default=math.inf)
        for enabled in (False, True):
            pricer = DeliveryMipPricer(p, ctx, continuous_time=enabled)
            try:
                result = pricer.price(prices, -2, phase_one=phase_one, work_prices=weights,
                    column_limit=1 if strategy=='optimal' else 16, stop_after_negative=strategy=='negative')
                assert result['lower_bound'] <= expected+1e-8
                assert result['integer_time_variables'] == (0 if enabled else len(pricer.context['nodes']))
                if math.isfinite(expected) and result['complete']:
                    assert result['minimum_reduced_cost'] == pytest.approx(expected)
                elif math.isinf(expected):
                    assert result['infeasible']
                for c in result['columns']:
                    assert c['tasks'] in oracle and cost(c) < -1e-7
                    assert c['cost'] == pytest.approx(oracle[c['tasks']]['cost'])
                    assert c['starts'] == oracle[c['tasks']]['starts']
                    assert all(type(t) is int for t in c['starts'])
                assert pricer.model.Params.SolutionNumber == 0
                # Reuse with changed prices, including resetting negative-stop
                # and solution-pool schedule-ignore parameters.
                result = pricer.price({key:-2 for key in ctx['lookup']}, column_limit=1)
                assert result['complete'] and not result['columns']
                assert pricer.model.Params.BestObjStop < -1e90
            finally:
                pricer.dispose()


def test_fractional_clocks_and_ceiling_all_clocks_are_both_feasible_with_fixed_route():
    gp = pytest.importorskip('gurobipy')
    p = tiny_problem(2,0)
    ctx = contexts(p)[2]['S']
    pricer = DeliveryMipPricer(p,ctx,continuous_time=True)
    try:
        route = {(-1,0),(0,1),(1,-2)}
        for v in pricer.visits.values():
            v.LB = v.UB = 1
        for key,v in pricer.arcs.items():
            v.LB = v.UB = int(key in route)
        for i,v in pricer.times.items():
            v.LB = v.UB = 2.25+i
        pricer.model.optimize()
        assert pricer.model.Status == gp.GRB.OPTIMAL
        for v in pricer.times.values():
            v.LB = v.UB = math.ceil(v.X)
        pricer.model.optimize()
        assert pricer.model.Status == gp.GRB.OPTIMAL
    finally:
        pricer.dispose()


@pytest.mark.parametrize('enabled',[False,True])
def test_empty_window_task_does_not_destroy_other_routes_and_input_context_is_immutable(enabled):
    pytest.importorskip('gurobipy')
    p = tiny_problem(1,3)
    ctx = contexts(p)[2]['T']
    before = deepcopy(ctx)
    assert any(n['window'][0]>n['window'][1] for n in ctx['nodes'])
    oracle = list(independently_enumerated_trips(p,ctx))
    assert {c['tasks'] for c in oracle} == {('o2',)}
    prices = {key:100 for key in ctx['lookup']}
    pricer = DeliveryMipPricer(p,ctx,continuous_time=enabled)
    try:
        result = pricer.price(prices)
        assert result['complete'] and not result['infeasible'] and result['filtered_window_nodes']==1
        assert result['minimum_reduced_cost']==pytest.approx(oracle[0]['cost']-100)
        assert result['columns'][0]['tasks']==('o2',)
        assert ctx==before
    finally:
        pricer.dispose()


@pytest.mark.parametrize('enabled',[False,True])
def test_all_empty_windows_make_only_the_pricing_route_set_infeasible(enabled):
    pytest.importorskip('gurobipy')
    p = tiny_problem(2,0)
    ctx = deepcopy(contexts(p)[2]['S'])
    for node in ctx['nodes']:
        node['window']=(10,2)
    pricer = DeliveryMipPricer(p,ctx,continuous_time=enabled)
    try:
        result = pricer.price({key:100 for key in ctx['lookup']})
        assert result['complete'] and result['infeasible'] and not result['columns']
        assert result['filtered_window_nodes']==len(ctx['nodes'])
    finally:
        pricer.dispose()


def test_continuous_clocks_do_not_allow_a_zero_duration_disconnected_subtour():
    gp = pytest.importorskip('gurobipy')
    base = tiny_problem(2,1)
    p = MultiBatchInstance(base.network,[replace(o,service_time=0) for o in base.orders],base.operating_policy,model=2)
    for key in p._travel:
        p._travel[key] = 0
    pricer = DeliveryMipPricer(p,contexts(p)[2]['S'],continuous_time=True)
    try:
        route = {(-1,0),(0,-2),(1,2),(2,1)}
        for v in pricer.visits.values():
            v.LB = v.UB = 1
        for key,v in pricer.arcs.items():
            v.LB = v.UB = int(key in route)
        pricer.model.optimize()
        assert pricer.model.Status == gp.GRB.INFEASIBLE
    finally:
        pricer.dispose()


@pytest.mark.parametrize('model',[1,2])
@pytest.mark.parametrize('seed',range(8))
@pytest.mark.parametrize('strategy',['optimal','pool','negative'])
def test_root_bound_and_every_dual_plane_remain_valid_for_original_problem(model,seed,strategy):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model,seed)
    oracle = enumerate_tiny(p)
    new = root_route_bound(p,time_limit=3,pricing_method='mip',pricing_continuous_time=True,
        mip_strategy=strategy,dual_cuts=True)
    old = root_route_bound(p,time_limit=3,pricing_method='mip',mip_strategy=strategy,dual_cuts=True)
    assert new['pricing_continuous_time'] and not new['proven_optimal']
    if math.isfinite(oracle):
        assert new['bound'] <= oracle+1e-8 and not new['proven_infeasible']
        assert check_all_partitions(p,new) > 0
        assert new['relaxation_complete'] and old['relaxation_complete']
        assert new['restricted_objective'] == pytest.approx(old['restricted_objective'])
    else:
        assert new['proven_infeasible'] == old['proven_infeasible']


@pytest.mark.parametrize('weighted',[False,True])
@pytest.mark.parametrize('early',[False,True])
def test_expired_continuous_pricing_retains_only_a_safe_incomplete_bound(weighted,early):
    pytest.importorskip('gurobipy')
    p = tiny_problem(2,1)
    ctx = contexts(p)[2]['S']
    prices = {key:40 for key in ctx['lookup']}
    weights = [((0,30),.2)] if weighted else []
    expected = min(c['cost']+sum(v*manual_work(p,'S',c['tasks'],b) for b,v in weights)-sum(prices[k] for k in c['tasks'])
                   for c in independently_enumerated_trips(p,ctx))
    pricer = DeliveryMipPricer(p,ctx,continuous_time=True)
    try:
        result = pricer.price(prices,work_prices=weights,deadline=time.perf_counter()-1,
            column_limit=16,stop_after_negative=early)
        assert not result['complete'] and not result['infeasible'] and result['lower_bound'] <= expected+1e-8
    finally:
        pricer.dispose()


@pytest.mark.parametrize('field',['release','end_limit','window','service','travel'])
def test_fractional_tick_coefficients_are_rejected_before_certifying_exact_pricing(field):
    pytest.importorskip('gurobipy')
    p = tiny_problem(2,0)
    ctx = deepcopy(contexts(p)[2]['S'])
    if field in ('release','end_limit'):
        ctx[field] += .5
    elif field == 'window':
        low,high = ctx['nodes'][0]['window']
        ctx['nodes'][0]['window'] = (low+.5,high)
    elif field == 'service':
        ctx['nodes'][0]['order'] = replace(ctx['nodes'][0]['order'],service_time=.5)
    else:
        p._travel['S',ctx['nodes'][0]['order'].customer] = .5
    with pytest.raises(ValueError,match='integer tick'):
        DeliveryMipPricer(p,ctx,continuous_time=True)


@pytest.mark.parametrize('model',[3,4])
def test_continuous_delivery_pricing_does_not_silently_drop_pickups(model):
    p = tiny_problem(model,1)
    with pytest.raises(ValueError,match='M1/M2'):
        DeliveryMipPricer(p,{},continuous_time=True)
    with pytest.raises(ValueError,match='M1/M2'):
        root_route_bound(p,pricing_method='mip',pricing_continuous_time=True)


def test_continuous_flag_requires_mip_pricing():
    with pytest.raises(ValueError,match='requires MIP'):
        root_route_bound(tiny_problem(2,1),pricing_continuous_time=True)


@pytest.mark.parametrize('model',[1,2])
@pytest.mark.parametrize('strategy',['optimal','pool','negative'])
def test_full_hybrid_keeps_integer_schedule_and_original_optimality_proof(model,strategy):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model,1)
    result = solve_with_route_bound(p,time_limit=4,root_time=1,pool_time=.5,
        pricing_method='mip',pricing_continuous_time=True,mip_strategy=strategy,dual_cuts=True,pool_compact=True)
    assert result['info']['proven_optimal'] and audit_solution(p,result)['valid']
    assert result['info']['objective'] == pytest.approx(enumerate_tiny(p))
    assert not result['info']['route_bound_certificate']['proven_optimal']
    assert not result['info']['route_pool_primal']['global_proof']
    assert all(type(t['departure']) is int and type(t['return_complete']) is int for t in result['e2_trips'])
