"""Count-row repair checked against all routes and an independent repair LP."""
import math
import random
from types import SimpleNamespace
import time

import pytest

from models.routing.model_5.exact.bounds.route_cover_cg.count_dual_repair import repair_count_duals
from models.routing.model_5.exact.bounds.route_cover_cg.route_cg import repair_category_duals
from models.routing.model_5.exact.bounds.route_cg.pickup_pricing import route_context
from tests.routing.test_multibatch_hybrid import capacity_problem
from tests.routing.test_multibatch_route_cg import enumerate_trips


@pytest.mark.parametrize('seed', range(6))
def test_repaired_dual_is_feasible_for_every_elementary_route_and_dominates_old_repair(seed):
    p=capacity_problem();ctx=route_context(p,'S');oracle=enumerate_trips(p,'S')
    rng=random.Random(1990+seed);pi={n['key']:rng.uniform(-3,9) for n in ctx['nodes']}
    nu=-rng.uniform(0,4);lam=rng.uniform(0,14);subset={('A','d'),('B','d')};sigma=1.3
    groups=[]
    for category in [None,'b1']:
        costs=[cost-math.fsum(pi[k] for k in tasks)-nu-(lam if category else 0)
               -(sigma if subset.intersection(tasks) else 0)
               for tasks,cost in oracle if bool(category)==any(kind=='d' for _,kind in tasks)]
        # A deliberately unfinished pricing bound still gives a valid repair.
        groups.append(dict(category=category,lower_bound=min(costs)-rng.uniform(0,3),infeasible=False))
    pricing=dict(groups=groups);rows={'b1':(2,lam)}
    new=repair_count_duals(ctx,pi,nu,pricing,rows)
    old=repair_category_duals(ctx,pi,nu,pricing)
    assert new['bound']>=old['bound']-1e-8
    fixed={tuple(k):v for k,v in new['repaired_duals']}
    lam_fixed=dict(new['repaired_delivery_duals'])['b1'];nu_fixed=new['repaired_fleet_dual']
    assert 0<=lam_fixed<=lam
    for tasks,cost in oracle:
        assert cost-math.fsum(fixed[k] for k in tasks)-nu_fixed\
            -(lam_fixed if any(kind=='d' for _,kind in tasks) else 0)\
            -(sigma if subset.intersection(tasks) else 0)>=-1e-8
    objective=math.fsum(fixed.values())+ctx['maximum_trips']*nu_fixed+2*lam_fixed+sigma
    assert new['bound']+2*lam+sigma==pytest.approx(objective)
    assert objective<=4+1e-7  # independently enumerated capacity-cover optimum


@pytest.mark.parametrize('seed', range(4))
def test_breakpoint_solution_matches_independent_linear_repair_program(seed):
    gp=pytest.importorskip('gurobipy');rng=random.Random(2710+seed)
    nodes=[];sizes={None:7,'a':4,'b':5,'c':3};required={'a':2,'b':3,'c':4}
    for category,size in sizes.items():
        for i in range(size):
            nodes.append(dict(key=(str(category)+str(i),'p' if category is None else 'd'),
                              order=SimpleNamespace(batch=category)))
    ctx=dict(nodes=nodes,maximum_trips=8);pi={n['key']:rng.uniform(-2,9) for n in nodes};nu=-1.1
    rows={b:(required[b],rng.uniform(0,8)) for b in required}
    bounds={b:rng.uniform(-13,2) for b in sizes}
    pricing=dict(groups=[dict(category=b,lower_bound=rho,infeasible=False) for b,rho in bounds.items()])
    actual=repair_count_duals(ctx,pi,nu,pricing,rows)
    m=gp.Model();m.Params.OutputFlag=0
    try:
        fleet=m.addVar();task={b:m.addVar() for b in sizes}
        reductions={b:m.addVar(ub=rows[b][1]) for b in rows}
        for b,rho in bounds.items():m.addConstr(fleet+task[b]+reductions.get(b,0)>=-rho)
        m.setObjective(8*fleet+gp.quicksum(sizes[b]*task[b] for b in sizes)
                       +gp.quicksum(rows[b][0]*reductions[b] for b in rows))
        m.optimize();assert m.Status==gp.GRB.OPTIMAL
        expected=math.fsum(pi.values())+8*nu-m.ObjVal
        assert actual['bound']==pytest.approx(expected,abs=1e-7)
    finally:m.dispose()


@pytest.mark.parametrize('change',['missing_tasks','missing_category','negative_count_price','nan_fleet'])
def test_invalid_dual_certificate_domain_is_rejected(change):
    ctx=route_context(capacity_problem(),'S');pi={n['key']:1.0 for n in ctx['nodes']};nu=0
    pricing=dict(groups=[dict(category=b,lower_bound=-2,infeasible=False) for b in [None,'b1']]);rows={'b1':(2,3)}
    if change=='missing_tasks':pi.pop(next(iter(pi)))
    if change=='missing_category':pricing['groups'].pop()
    if change=='negative_count_price':rows={'b1':(2,-1)}
    if change=='nan_fleet':nu=math.nan
    with pytest.raises(ValueError):repair_count_duals(ctx,pi,nu,pricing,rows)


def test_actual_cover_loop_uses_count_repair_and_matches_complete_route_oracle():
    pytest.importorskip('gurobipy')
    from models.routing.model_5.exact.bounds.route_cover_cg.route_cg import site_route_cover
    p=capacity_problem();ctx=route_context(p,'S')
    out=site_route_cover(p,ctx,deadline=time.perf_counter()+3,batch_pricing=True,
                        delivery_count_cuts=True,dual_repair=True,count_dual_repair=True)
    assert out['lp_converged'] and out['bound']==pytest.approx(4,abs=1e-5)
    assert any(it.get('dual_repair',{}).get('repair_mode')=='delivery_count_first' for it in out['iterations'])


def test_actual_public_pipeline_supports_count_repair_and_retains_full_audit(monkeypatch):
    pytest.importorskip('gurobipy')
    from copy import deepcopy
    import models.routing.service as service
    from models.routing.model_5.hybrid.solver import solve_hybrid
    from models.common.cycle.audit import audit_solution
    from tests.routing.test_multibatch_cycle import fixture,hand_solution
    p=fixture(pool=2);initial=hand_solution(p)
    monkeypatch.setattr(service,'solve_routing',lambda **kw:deepcopy(initial))
    out=solve_hybrid(p,time_limit=2,primal_time_limit=0,cover_time_limit=1,
                     conditional_time_limit=0,adaptive_pricing_mode='resume',count_dual_repair=True)
    assert out['target_reached'] and out['bound']<=out['upper_bound']+1e-6
    audit=audit_solution(p,out['result'])
    assert audit['valid'] and audit['periodic_replay']['valid']
