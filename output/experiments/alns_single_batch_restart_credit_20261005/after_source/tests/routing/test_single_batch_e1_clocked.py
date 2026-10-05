"""Every fixed-path integer-clock oracle and original complete MILP checks."""
from copy import copy,deepcopy
import itertools
import math
import time

import pytest

from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.formulation import build_model
from models.routing.solvers.exact.solver import solve_exact,_set_start
from models.routing.solvers.exact.single_batch_e1_bound import (
    enumerate_e1_routes,e1_route_cover_bound,project_e1_schedule,
    apply_e1_cover_bound,solve_exact_with_e1_cover)
from tests.routing.test_single_batch_solver_audit import tiny_problem,enumerate_tiny


def independent_path(p,seq):
    """Fixed-sequence integer MIP, not the candidate forward recurrence."""
    gp=pytest.importorskip('gurobipy');b=p.operating_policy.batches[0];policy=p.operating_policy;f=p.fleet
    if any(k=='p' and l=='d' for (_,k),(_,l) in zip(seq,seq[1:])):return None
    w=sum(p.weights[s,b.id] for s,k in seq if k=='d');v=sum(p.demands[s,b.id]*f.container_volume for s,k in seq if k=='d')
    if w>f.e1_weight_cap+1e-6 or v>f.e1_volume_cap+1e-6:return None
    for s,k in seq:
        w+=p.demands[s,b.id]*f.container_weight if k=='p' else -p.weights[s,b.id]
        v+=(1 if k=='p' else -1)*p.demands[s,b.id]*f.container_volume
        if not(-1e-6<=w<=f.e1_weight_cap+1e-6 and -1e-6<=v<=f.e1_volume_cap+1e-6):return None
    locations=list(p.network.nodes);fw={a:{z:p.travel(a,z) for z in locations} for a in locations}
    for k in locations:
        for a in locations:
            for z in locations:fw[a][z]=min(fw[a][z],fw[a][k]+fw[k][z])
    windows={}
    for s,k in seq:
        if k=='d':
            earliest=b.start+policy.e1_load_time+fw[p.depot][s]
            latest=min(min(o.delivery_window[1],b.cutoff-o.service_time)-policy.e2_dispatch_prep_time-fw[s][o.customer]
                for o in p.orders if o.satellite==s)-p.service(s)
        else:
            earliest=b.cutoff
            latest=min(b.end,policy.cycle_end-policy.cleaning_duration,policy.cycle_end-policy.e1_turnaround_time)
            latest-=p.service(s)+fw[s][p.depot]+policy.depot_return_unload_time
        if earliest>latest:return None
        windows[s,k]=(earliest,latest)
    m=gp.Model('independent_E1_integer_path');m.Params.OutputFlag=0;m.Params.Threads=1
    try:
        d=m.addVar(vtype=gp.GRB.INTEGER,lb=policy.cycle_start,ub=policy.cycle_end)
        if any(k=='d' for s,k in seq):m.addConstr(d==b.start+policy.e1_load_time)
        else:m.addConstr(d>=b.cutoff)
        times=[m.addVar(vtype=gp.GRB.INTEGER,lb=windows[t][0],ub=windows[t][1]) for t in seq]
        previous=p.depot;previous_time=d;previous_service=0;previous_kind=None
        for (s,k),t in zip(seq,times):
            m.addConstr(t>=previous_time+previous_service+p.travel(previous,s))
            if k=='p' and previous_kind in (None,'d'):m.addConstr(t>=b.cutoff+p.travel(previous,s))
            previous,previous_time,previous_service,previous_kind=s,t,p.service(s),k
        e=m.addVar(vtype=gp.GRB.INTEGER,lb=policy.cycle_start,ub=policy.cycle_end)
        m.addConstr(e>=previous_time+previous_service+p.travel(previous,p.depot)+policy.depot_return_unload_time)
        m.addConstr(e<=b.end);m.addConstr(e+policy.e1_turnaround_time<=policy.cycle_end)
        if any(k=='p' for s,k in seq):m.addConstr(e+policy.cleaning_duration<=policy.cycle_end)
        m.optimize()
        if m.Status!=gp.GRB.OPTIMAL:return None
        path=[p.depot]+[s for s,k in seq]+[p.depot]
        return sum(p.distance(a,z) for a,z in zip(path,path[1:]))
    finally:m.dispose()


def independent_routes(p):
    b=p.operating_policy.batches[0]
    tasks=[(s,k) for s in p.satellites if p.demands[s,b.id] for k in ('d','p')];routes=[]
    for size in range(1,len(tasks)+1):
        for seq in itertools.permutations(tasks,size):
            cost=independent_path(p,seq)
            if cost is not None:routes.append((seq,cost))
    return tasks,routes


def independent_cover(p,integer=False):
    gp=pytest.importorskip('gurobipy');tasks,routes=independent_routes(p)
    m=gp.Model('independent_uncompressed_E1_clock_cover');m.Params.OutputFlag=0;m.Params.Threads=1
    try:
        variables=[m.addVar(obj=cost,vtype=gp.GRB.BINARY if integer else gp.GRB.CONTINUOUS) for seq,cost in routes]
        for task in tasks:m.addConstr(gp.quicksum(v for v,(seq,_) in zip(variables,routes) if task in seq)==1)
        m.addConstr(gp.quicksum(variables)<=p.fleet.e1_count);m.optimize()
        return (m.ObjVal if m.Status==gp.GRB.OPTIMAL else math.inf),routes
    finally:m.dispose()


@pytest.mark.parametrize('seed',range(12))
def test_complete_clocked_compression_and_duals_match_independent_integer_paths(seed):
    p=tiny_problem(3,seed);lp,oracle=independent_cover(p);actual,meta=enumerate_e1_routes(p,clocked=True)
    expected={}
    for seq,cost in oracle:expected[frozenset(seq)]=min(expected.get(frozenset(seq),math.inf),cost)
    assert meta['complete'] and meta['paths']==len(oracle)
    assert {frozenset(r['tasks']):r['cost'] for r in actual}==pytest.approx(expected,abs=1e-9)
    c=e1_route_cover_bound(p,2,clocked=True)
    assert c['proof_scope']=='B1_M3_E1_complete_clocked_cover_LP' and c['clocked']
    assert not c['proven_optimal'] and not c['proven_infeasible']
    if math.isfinite(lp):
        assert c['relaxation_complete'] and c['objective']==pytest.approx(lp,abs=1e-6)
        tasks=[(s,k) for s in meta['sites'] for k in ('d','p')];pi=dict(zip(tasks,c['cover_duals']));nu=c['fleet_dual']
        minimum=min([0.0]+[cost-sum(pi[k] for k in seq)-nu for seq,cost in oracle])
        assert nu<=0 and c['minimum_reduced_cost']<=minimum+1e-6
    original=enumerate_tiny(p)
    if math.isfinite(original):assert c['bound']<=original+1e-6


@pytest.mark.parametrize('weight,volume',[(4,20),(20,2),(4,2),(3,20)])
def test_clocked_cover_preserves_binding_two_resources_and_vehicle_partition(weight,volume):
    p=copy(tiny_problem(3,1));p.fleet=copy(p.fleet);p.fleet.e1_count=2
    p.fleet.e1_weight_cap=weight;p.fleet.e1_volume_cap=volume
    lp,routes=independent_cover(p);partition,_=independent_cover(p,True);c=e1_route_cover_bound(p,2,clocked=True)
    if math.isfinite(lp):
        assert c['relaxation_complete'] and c['objective']==pytest.approx(lp,abs=1e-6)
        assert c['bound']<=partition+1e-6
    else:assert not c['proven_infeasible']


def test_cheapest_clock_free_path_is_not_the_only_same_task_mask_candidate():
    p=deepcopy(tiny_problem(3,1));p._travel['T','S']=100;p._travel['S','T']=1
    i=p.network.node_index;p.network.dist[i['T']][i['S']]=1;p.network.dist[i['S']][i['T']]=20
    free,_=enumerate_e1_routes(p);clocked,meta=enumerate_e1_routes(p,clocked=True)
    key=frozenset((s,'d') for s in p.satellites)
    f=next(r for r in free if frozenset(r['tasks'])==key);c=next(r for r in clocked if frozenset(r['tasks'])==key)
    assert f['cost']<c['cost'] and independent_path(p,f['tasks']) is None
    assert independent_path(p,c['tasks'])==pytest.approx(c['cost'])


@pytest.mark.parametrize('seed',range(8))
def test_complete_original_kernel_with_clocked_row_matches_independent_integer_oracle(seed):
    p=tiny_problem(3,seed);expected=enumerate_tiny(p);r=solve_exact_with_e1_cover(p,3,clocked=True)
    if math.isfinite(expected):
        assert audit_solution(p,r)['valid'] and r['info']['proven_optimal']
        assert r['info']['objective']==pytest.approx(expected,abs=1e-6)
    else:assert r['info']['proven_infeasible']


@pytest.mark.parametrize('seed',[0,1,4,5])
def test_unchanged_original_integer_schedule_projects_and_satisfies_actual_clocked_row(seed):
    p=tiny_problem(3,seed);original=solve_exact(p,3);a=audit_solution(p,original);assert a['valid']
    routes,meta=enumerate_e1_routes(p,clocked=True);projection=project_e1_schedule(p,original,routes,clocked=True)
    assert sum(r['raw_cost'] for r in projection)==pytest.approx(a['distances']['e1'])
    c=e1_route_cover_bound(p,2,clocked=True);m,ctx=build_model(p)
    try:
        apply_e1_cover_bound(m,p,ctx,c);assert _set_start(p,m,ctx,original);m.update()
        constraint=m.getConstrByName('B1_M3_certified_E1_distance_floor')
        row=m.getRow(constraint) if constraint is not None else None
        raw=sum(row.getCoeff(i)*row.getVar(i).Start for i in range(row.size())) if row is not None else 0
        assert raw==pytest.approx(a['distances']['e1'],abs=1e-6) and raw>=c['bound']-1e-6
    finally:m.dispose()


@pytest.mark.parametrize('flag',[1,'yes',None])
def test_clocked_option_requires_boolean_at_all_public_entries(flag):
    p=tiny_problem(3,1)
    for call in (enumerate_e1_routes,e1_route_cover_bound,solve_exact_with_e1_cover):
        with pytest.raises(ValueError,match='boolean'):call(p,clocked=flag)


def test_partial_clocked_universe_and_expired_budget_only_return_safe_fallback():
    p=tiny_problem(3,1);routes,m=enumerate_e1_routes(p,clocked=True,deadline=time.perf_counter()-1)
    assert not routes and not m['complete']
    c=e1_route_cover_bound(p,1e-9,clocked=True)
    assert not c['relaxation_complete'] and c['bound']==c['fallback']
    c=e1_route_cover_bound(p,1,clocked=True,max_sites=0)
    assert not c['relaxation_complete'] and c['bound']==c['fallback']


@pytest.mark.parametrize('scope,marker',[
    ('B1_M3_E1_complete_clocked_cover_LP',False),('B1_M3_E1_complete_clocked_cover_LP',None),
    ('B1_M3_E1_complete_clock_relaxed_cover_LP',True)])
def test_mislabeled_clocked_certificate_is_rejected_before_model_mutation(scope,marker):
    gp=pytest.importorskip('gurobipy');p=tiny_problem(3,1);c=e1_route_cover_bound(p,1,clocked=True)
    c.update(proof_scope=scope,clocked=marker);m=gp.Model();m.Params.OutputFlag=0
    try:
        with pytest.raises(ValueError,match='scope'):apply_e1_cover_bound(m,p,{'e1':[]},c)
        m.update();assert m.NumConstrs==0
    finally:m.dispose()
