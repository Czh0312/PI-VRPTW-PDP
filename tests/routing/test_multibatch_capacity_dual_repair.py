"""Overlapping subset repair checked over every elementary feasible route."""
import math
import random
import time

import pytest

from models.routing.model_5.exact.multibatch_capacity_dual_repair import repair_capacity_duals
from models.routing.model_5.exact.multibatch_count_dual_repair import repair_count_duals
from models.routing.model_5.exact.pickup_route_pricing import route_context
from tests.routing.test_multibatch_hybrid import capacity_problem
from tests.routing.test_multibatch_route_cg import enumerate_trips


@pytest.mark.parametrize('seed',range(8))
def test_overlapping_subsets_preserve_full_route_dual_feasibility_and_old_floor(seed):
    pytest.importorskip('gurobipy')
    p=capacity_problem();ctx=route_context(p,'S');oracle=enumerate_trips(p,'S');rng=random.Random(640+seed)
    pi={n['key']:rng.uniform(-3,8) for n in ctx['nodes']};nu=-rng.uniform(0,3);lam=rng.uniform(0,5)
    subsets={0:(frozenset([('A','d'),('B','d')]),1,rng.uniform(2,13)),
             1:(frozenset([('B','d'),('C','d')]),1,rng.uniform(2,13))}
    groups=[]
    for b in [None,'b1']:
        costs=[cost-math.fsum(pi[k] for k in tasks)-nu-(lam if b else 0)
               -math.fsum(sigma for keys,_,sigma in subsets.values() if keys.intersection(tasks))
               for tasks,cost in oracle if bool(b)==any(kind=='d' for _,kind in tasks)]
        groups.append(dict(category=b,lower_bound=min(costs)-rng.uniform(0,2),infeasible=False))
    pricing=dict(groups=groups);rows={'b1':(2,lam)}
    old=repair_count_duals(ctx,pi,nu,pricing,rows)
    repaired=repair_capacity_duals(ctx,pi,nu,pricing,rows,subsets)
    assert repaired['bound']>=old['bound']-1e-8
    fixed={tuple(k):v for k,v in repaired['repaired_duals']};fixed_lam=dict(repaired['repaired_delivery_duals'])['b1']
    fixed_sigma=dict(repaired.get('repaired_subset_duals',[(s,row[2]) for s,row in subsets.items()]))
    for tasks,cost in oracle:
        reduced=cost-math.fsum(fixed[k] for k in tasks)-repaired['repaired_fleet_dual']\
                -(fixed_lam if any(kind=='d' for _,kind in tasks) else 0)\
                -math.fsum(fixed_sigma[s] for s,(keys,_,_) in subsets.items() if keys.intersection(tasks))
        assert reduced>=-1e-8
    original_constant=2*lam+math.fsum(n*sigma for _,n,sigma in subsets.values())
    value=math.fsum(fixed.values())+ctx['maximum_trips']*repaired['repaired_fleet_dual']\
          +2*fixed_lam+math.fsum(subsets[s][1]*v for s,v in fixed_sigma.items())
    assert repaired['bound']+original_constant==pytest.approx(value)
    assert value<=4+1e-7


def test_already_expired_deadline_keeps_the_valid_count_repair_without_creating_lp(monkeypatch):
    gp=pytest.importorskip('gurobipy');p=capacity_problem();ctx=route_context(p,'S')
    pi={n['key']:1.0 for n in ctx['nodes']};pricing=dict(groups=[dict(category=b,lower_bound=-2,infeasible=False) for b in [None,'b1']])
    rows={'b1':(2,3)};old=repair_count_duals(ctx,pi,0,pricing,rows)
    monkeypatch.setattr(gp,'Model',lambda *a,**kw:pytest.fail('expired budget must not build a correction LP'))
    new=repair_capacity_duals(ctx,pi,0,pricing,rows,{},deadline=0)
    assert new['bound']==old['bound'] and new['capacity_repair_status']=='BUDGET_EXHAUSTED'


@pytest.mark.parametrize('keys',[[],[('missing','d')],[('A','p')]])
def test_unknown_or_nondelivery_subsets_are_rejected(keys):
    p=capacity_problem();ctx=route_context(p,'S');pi={n['key']:1.0 for n in ctx['nodes']}
    pricing=dict(groups=[dict(category=b,lower_bound=-2,infeasible=False) for b in [None,'b1']])
    with pytest.raises(ValueError,match='subsets'):
        repair_capacity_duals(ctx,pi,0,pricing,{'b1':(2,3)},{0:(keys,1,2)})


def test_actual_column_loop_with_overlap_keeps_complete_oracle_optimum():
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.multibatch_route_cg import site_route_cover
    p=capacity_problem();ctx=route_context(p,'S')
    row=site_route_cover(p,ctx,deadline=time.perf_counter()+3,batch_pricing=True,
                         dual_repair=True,delivery_count_cuts=True,capacity_dual_repair=True,
                         subset_cuts=[[('A','d'),('B','d')],[('B','d'),('C','d')]])
    assert row['lp_converged'] and row['bound']==pytest.approx(4,abs=1e-5)
    assert any('capacity_repair_status' in it.get('dual_repair',{}) for it in row['iterations'])


def test_actual_public_pipeline_with_capacity_repair_preserves_full_audit(monkeypatch):
    pytest.importorskip('gurobipy')
    from copy import deepcopy
    import models.routing.service as service
    from models.routing.model_5.exact.multibatch_hybrid import solve_hybrid
    from models.common.cycle.audit import audit_solution
    from tests.routing.test_multibatch_cycle import fixture,hand_solution
    p=fixture(pool=2);initial=hand_solution(p)
    monkeypatch.setattr(service,'solve_routing',lambda **kw:deepcopy(initial))
    out=solve_hybrid(p,time_limit=2,primal_time_limit=0,cover_time_limit=1,
                     conditional_time_limit=0,adaptive_pricing_mode='resume',capacity_dual_repair=True)
    assert out['target_reached'] and out['bound']<=out['upper_bound']+1e-6
    audit=audit_solution(p,out['result'])
    assert audit['valid'] and audit['periodic_replay']['valid']
