"""Independent every-route LP/integer partition and raw schedule oracles."""
import itertools
import math
import time
from copy import copy

import pytest

from models.routing.model_3.exact.accelerators.single_batch.e1_bound import enumerate_e1_routes, e1_route_cover_bound, project_e1_schedule
from models.routing.model_3.exact.accelerators.single_batch.pickup_bound import root_pickup_route_bound
from models.routing.model_3.exact.search.engine import solve_exact
from tests.routing.test_single_batch_solver_audit import tiny_problem,enumerate_tiny


def independent_routes(p):
    b=p.operating_policy.batches[0];f=p.fleet
    tasks=[(s,k) for s in p.satellites if p.demands[s,b.id] for k in ('d','p')]
    answer=[]
    # Every permutation of every task subset, rather than the production
    # two-list join/compression algorithm. Raw dynamic load independently.
    for size in range(1,len(tasks)+1):
        for sequence in itertools.permutations(tasks,size):
            if any(k=='p' and l=='d' for (_,k),(_,l) in zip(sequence,sequence[1:])):continue
            w=sum(p.weights[s,b.id] for s,k in sequence if k=='d')
            v=sum(p.demands[s,b.id]*f.container_volume for s,k in sequence if k=='d')
            feasible=w<=f.e1_weight_cap+1e-6 and v<=f.e1_volume_cap+1e-6
            for s,k in sequence:
                w+=p.demands[s,b.id]*f.container_weight if k=='p' else -p.weights[s,b.id]
                v+=(1 if k=='p' else -1)*p.demands[s,b.id]*f.container_volume
                feasible &= -1e-6<=w<=f.e1_weight_cap+1e-6 and -1e-6<=v<=f.e1_volume_cap+1e-6
            if not feasible:continue
            locations=[p.depot]+[s for s,_ in sequence]+[p.depot]
            cost=sum(p.distance(a,b) for a,b in zip(locations,locations[1:]))
            answer.append((sequence,cost))
    return tasks,answer


def independent_cover(p,integer=False):
    gp=pytest.importorskip('gurobipy');tasks,routes=independent_routes(p)
    m=gp.Model();m.Params.OutputFlag=0;m.Params.Threads=1
    try:
        variables=[m.addVar(obj=cost,vtype=gp.GRB.BINARY if integer else gp.GRB.CONTINUOUS) for seq,cost in routes]
        for task in tasks:m.addConstr(gp.quicksum(v for v,(seq,_) in zip(variables,routes) if task in seq)==1)
        m.addConstr(gp.quicksum(variables)<=p.fleet.e1_count);m.optimize()
        return m.ObjVal if m.Status==gp.GRB.OPTIMAL else math.inf
    finally:m.dispose()


@pytest.mark.parametrize('seed',range(12))
def test_complete_compression_matches_independent_every_raw_route(seed):
    p=tiny_problem(3,seed);tasks,oracle=independent_routes(p)
    actual,info=enumerate_e1_routes(p);assert info['complete'] and info['paths']==len(oracle)
    expected={}
    for seq,cost in oracle:expected[frozenset(seq)]=min(expected.get(frozenset(seq),math.inf),cost)
    assert len(actual)==len(expected)
    for route in actual:assert route['cost']==pytest.approx(expected[frozenset(route['tasks'])],abs=1e-10)


@pytest.mark.parametrize('seed',range(12))
def test_component_and_combined_bound_match_independent_lp_and_original_integer_oracle(seed):
    p=tiny_problem(3,seed);lp=independent_cover(p);partition=independent_cover(p,True)
    original=enumerate_tiny(p);result=e1_route_cover_bound(p,2)
    if math.isfinite(lp):
        assert result['relaxation_complete'] and result['objective']==pytest.approx(lp,abs=1e-6)
        assert result['bound']<=partition+1e-6
    if math.isfinite(original):
        combined=root_pickup_route_bound(p,5,e1_route_cover=True)
        assert combined['bound']<=original+1e-6 and not combined['proven_optimal']


@pytest.mark.parametrize('seed',[0,1,4,5])
def test_original_integer_schedule_projects_unchanged_raw_arcs_then_dominates(seed):
    p=tiny_problem(3,seed);original=solve_exact(p,3)
    routes,info=enumerate_e1_routes(p);assert info['complete']
    records=project_e1_schedule(p,original,routes)
    assert sum(len(r['tasks']) for r in records)==2*len({o.satellite for o in p.orders})
    assert sum(r['representative_cost'] for r in records)<=sum(r['raw_cost'] for r in records)+1e-6


def test_partial_universe_never_promotes_restricted_pool():
    p=tiny_problem(3,1)
    routes,info=enumerate_e1_routes(p,deadline=time.perf_counter()-1)
    assert not routes and not info['complete']
    r=e1_route_cover_bound(p,1e-9)
    assert not r['relaxation_complete'] and r['bound']==r['fallback']


@pytest.mark.parametrize('weight,volume',[(4,20),(20,2),(4,2),(3,20)])
def test_binding_two_resource_loads_and_separate_physical_vehicles(weight,volume):
    base=tiny_problem(3,1);p=copy(base);p.fleet=copy(base.fleet)
    p.fleet.e1_count=2;p.fleet.e1_weight_cap=weight;p.fleet.e1_volume_cap=volume
    _,oracle=independent_routes(p);routes,info=enumerate_e1_routes(p)
    assert info['complete'] and info['paths']==len(oracle)
    expected={}
    for sequence,cost in oracle:expected[frozenset(sequence)]=min(expected.get(frozenset(sequence),math.inf),cost)
    assert {frozenset(r['tasks']):r['cost'] for r in routes}==pytest.approx(expected)
    lp=independent_cover(p);result=e1_route_cover_bound(p,2)
    if math.isfinite(lp):
        assert result['relaxation_complete'] and result['objective']==pytest.approx(lp,abs=1e-6)
        assert result['bound']<=independent_cover(p,True)+1e-6
    else:assert not result['proven_infeasible'] and not result['proven_optimal']
    r=e1_route_cover_bound(p,1,max_sites=0)
    assert not r['relaxation_complete'] and r['bound']==r['fallback']


@pytest.mark.parametrize('model',[1,2,4])
def test_fixed_e1_component_is_not_transferred_to_free_assignment_or_delivery_only(model):
    with pytest.raises(ValueError,match='M3'):e1_route_cover_bound(tiny_problem(model,1))
    if model==4:
        with pytest.raises(ValueError,match='M3'):root_pickup_route_bound(tiny_problem(model,1),e1_route_cover=True)
