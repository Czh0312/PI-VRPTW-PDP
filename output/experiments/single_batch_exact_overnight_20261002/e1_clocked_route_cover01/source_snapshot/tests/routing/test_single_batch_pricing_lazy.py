"""Every temporal cut checked against ALL independently enumerated routes."""
from dataclasses import replace
import itertools
import math
import random
import time

import pytest

from models.routing.common.cycle import MultiBatchInstance
from models.routing.solvers.exact.single_batch_delivery_pricer import DeliveryMipPricer
from models.routing.solvers.exact.single_batch_pricing_schedule import infeasible_time_path
from models.routing.solvers.exact.single_batch_columns import root_route_bound, solve_with_route_bound
from models.routing.common.cycle.audit import audit_solution
from tests.routing.test_single_batch_columns import contexts, independently_enumerated_trips
from tests.routing.test_single_batch_solver_audit import tiny_problem, enumerate_tiny
from tests.routing.test_single_batch_dual_cuts import check_all_partitions
from tests.routing.test_single_batch_time_budget import manual_work


def arcs(indices):
    return {(-1,indices[0]),(indices[-1],-2),*zip(indices,indices[1:])}


def check_cuts(context,cuts,feasible):
    for cut in cuts:
        for route in feasible:
            bits = arcs([context['lookup'][key] for key in route])
            assert len(bits & set(cut)) <= len(cut)-1, (cut,route)


@pytest.mark.parametrize('model',[1,2])
@pytest.mark.parametrize('seed',range(8))
def test_each_route_and_every_generated_cut_match_independent_raw_enumeration(model,seed):
    gp = pytest.importorskip('gurobipy')
    p = tiny_problem(model,seed)
    for original in contexts(p)[2].values():
        feasible = {c['tasks'] for c in independently_enumerated_trips(p,original)}
        pricer = DeliveryMipPricer(p,original,lazy_time=True)
        try:
            ctx = pricer.context
            keys = list(ctx['lookup'])
            assert not pricer.times
            pricer.model.setObjective(0)
            for size in range(1,len(keys)+1):
                for tasks in itertools.permutations(keys,size):
                    indices = [ctx['lookup'][key] for key in tasks]
                    bits = arcs(indices)
                    violation = infeasible_time_path(p,ctx,indices)
                    if violation:
                        assert tasks not in feasible
                        check_cuts(ctx,[violation[0]],feasible)
                    if not bits <= set(pricer.arcs):
                        assert tasks not in feasible
                        continue
                    for i,v in pricer.visits.items():
                        v.LB = v.UB = int(i in indices)
                    for key,v in pricer.arcs.items():
                        v.LB = v.UB = int(key in bits)
                    pricer.model.optimize(pricer._time_callback)
                    assert pricer.callback_error is None
                    assert (pricer.model.Status==gp.GRB.OPTIMAL) == (tasks in feasible),tasks
            check_cuts(ctx,pricer.lazy_cuts,feasible)
        finally:
            pricer.dispose()


@pytest.mark.parametrize('seed',range(8))
@pytest.mark.parametrize('phase_one',[False,True])
@pytest.mark.parametrize('weighted',[False,True])
@pytest.mark.parametrize('strategy',['optimal','pool','negative'])
def test_lazy_minimum_all_pool_columns_and_reused_cuts_remain_exact(seed,phase_one,weighted,strategy):
    pytest.importorskip('gurobipy')
    p = tiny_problem(2,seed)
    for site,ctx in contexts(p)[2].items():
        rng = random.Random(seed+19)
        prices = {key:rng.uniform(5,40) for key in ctx['lookup']}
        weights = [((0,30),.2),((8,65),.3)] if weighted else []
        oracle = {c['tasks']:c for c in independently_enumerated_trips(p,ctx)}
        def cost(c):
            return (0 if phase_one else c['cost'])+sum(v*manual_work(p,site,c['tasks'],b) for b,v in weights) \
                -sum(prices[key] for key in c['tasks'])+2
        expected = min(map(cost,oracle.values()),default=math.inf)
        pricer = DeliveryMipPricer(p,ctx,lazy_time=True)
        try:
            result = pricer.price(prices,-2,phase_one=phase_one,work_prices=weights,
                column_limit=1 if strategy=='optimal' else 16,stop_after_negative=strategy=='negative')
            assert result['lower_bound'] <= expected+1e-8 and result['time_domain']=='lazy'
            assert result['integer_time_variables']==0
            if math.isfinite(expected) and result['complete']:
                assert result['minimum_reduced_cost']==pytest.approx(expected)
            elif math.isinf(expected):
                assert result['infeasible']
            for column in result['columns']:
                assert column['tasks'] in oracle and cost(column)<-1e-7
                assert column['cost']==pytest.approx(oracle[column['tasks']]['cost'])
                assert column['starts']==oracle[column['tasks']]['starts']
            check_cuts(pricer.context,pricer.lazy_cuts,oracle)
            result = pricer.price({key:-2 for key in ctx['lookup']},column_limit=1)
            assert result['complete'] and not result['columns']
            assert pricer.model.Params.SolutionNumber==0 and pricer.model.Params.BestObjStop < -1e90
            check_cuts(pricer.context,pricer.lazy_cuts,oracle)
        finally:
            pricer.dispose()


def test_nonmetric_bad_return_cut_keeps_feasible_extension_with_same_prefix():
    gp = pytest.importorskip('gurobipy')
    base = tiny_problem(1,0)
    p = MultiBatchInstance(base.network,[replace(o,satellite='S',delivery_window=(0,65)) for o in base.orders],
                           base.operating_policy,model=1)
    a,b = [o.customer for o in p.orders]
    p._travel['S',a],p._travel[a,'S'] = 50,80
    pricer = DeliveryMipPricer(p,contexts(p)[2]['S'],lazy_time=True)
    try:
        ctx = pricer.context
        bad = infeasible_time_path(p,ctx,[0])
        assert bad is not None and bad[1]=='return' and (0,-2) in bad[0]
        assert infeasible_time_path(p,ctx,[0,1]) is None
        assert len(arcs([0,1]) & set(bad[0])) <= len(bad[0])-1
        for indices,expected in [([0],gp.GRB.INFEASIBLE),([0,1],gp.GRB.OPTIMAL)]:
            bits = arcs(indices)
            assert bits <= set(pricer.arcs)
            for i,v in pricer.visits.items():
                v.LB = v.UB = int(i in indices)
            for key,v in pricer.arcs.items():
                v.LB = v.UB = int(key in bits)
            pricer.model.optimize(pricer._time_callback)
            assert pricer.model.Status==expected and pricer.callback_error is None
        assert 'return' in pricer.lazy_cuts.values()
    finally:
        pricer.dispose()


@pytest.mark.parametrize('model',[1,2])
@pytest.mark.parametrize('seed',range(8))
@pytest.mark.parametrize('strategy',['optimal','pool','negative'])
def test_root_certificate_and_every_partition_plane_still_bound_original_integer_problem(model,seed,strategy):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model,seed)
    oracle = enumerate_tiny(p)
    certificate = root_route_bound(p,time_limit=3,pricing_method='mip',pricing_lazy_time=True,
        mip_strategy=strategy,dual_cuts=True)
    old = root_route_bound(p,time_limit=3,pricing_method='mip',mip_strategy=strategy)
    assert certificate['pricing_lazy_time'] and not certificate['proven_optimal']
    if math.isfinite(oracle):
        assert certificate['bound']<=oracle+1e-8 and not certificate['proven_infeasible']
        assert check_all_partitions(p,certificate)>0
        assert certificate['relaxation_complete'] and old['relaxation_complete']
        assert certificate['restricted_objective']==pytest.approx(old['restricted_objective'])
    else:
        assert certificate['proven_infeasible']==old['proven_infeasible']


@pytest.mark.parametrize('early',[False,True])
def test_zero_remaining_lazy_pricing_budget_does_not_claim_completion(early):
    pytest.importorskip('gurobipy')
    p = tiny_problem(2,1)
    ctx = contexts(p)[2]['S']
    prices = {key:40 for key in ctx['lookup']}
    oracle = min(c['cost']-sum(prices[key] for key in c['tasks']) for c in independently_enumerated_trips(p,ctx))
    pricer = DeliveryMipPricer(p,ctx,lazy_time=True)
    try:
        result = pricer.price(prices,column_limit=16,stop_after_negative=early,deadline=time.perf_counter()-1)
        assert not result['complete'] and not result['infeasible'] and result['lower_bound']<=oracle+1e-8
    finally:
        pricer.dispose()


def test_callback_errors_fail_closed_without_returning_a_pricing_certificate(monkeypatch):
    pytest.importorskip('gurobipy')
    from models.routing.solvers.exact import single_batch_pricing_schedule as schedule
    def broken(*args):
        raise ValueError('deliberate separation failure')
    monkeypatch.setattr(schedule,'infeasible_time_path',broken)
    p = tiny_problem(2,0)
    pricer = DeliveryMipPricer(p,contexts(p)[2]['S'],lazy_time=True)
    try:
        with pytest.raises(RuntimeError,match='no certificate returned'):
            pricer.price({o.id:100 for o in p.orders})
        assert isinstance(pricer.callback_error,ValueError)
    finally:
        pricer.dispose()


@pytest.mark.parametrize('model',[1,2])
def test_full_hybrid_uses_original_integer_proof_and_audited_integer_schedules(model):
    pytest.importorskip('gurobipy')
    p = tiny_problem(model,1)
    result = solve_with_route_bound(p,time_limit=4,root_time=1,pool_time=.5,
        pricing_method='mip',mip_strategy='pool',pricing_lazy_time=True,dual_cuts=True,pool_compact=True)
    assert result['info']['proven_optimal'] and audit_solution(p,result)['valid']
    assert result['info']['objective']==pytest.approx(enumerate_tiny(p))
    assert not result['info']['route_pool_primal']['global_proof']
    assert not result['info']['route_bound_certificate']['proven_optimal']


def test_lazy_flag_cannot_be_mixed_with_continuous_clocks_or_label_pricing():
    p = tiny_problem(2,0)
    with pytest.raises(ValueError,match='Choose either'):
        DeliveryMipPricer(p,{},continuous_time=True,lazy_time=True)
    with pytest.raises(ValueError,match='Choose either'):
        root_route_bound(p,pricing_method='mip',pricing_lazy_time=True,pricing_continuous_time=True)
    with pytest.raises(ValueError,match='requires MIP'):
        root_route_bound(p,pricing_lazy_time=True)
