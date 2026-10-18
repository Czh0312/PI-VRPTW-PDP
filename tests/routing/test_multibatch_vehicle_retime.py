"""Known optima, physical preparation, and zero-duration causal counterexamples."""
from copy import deepcopy
from dataclasses import replace
import math

import pytest

from models.common.cycle import MultiBatchInstance, Order
from models.common.cycle.audit import audit_solution
from models.common.cycle.state import empty_result
from models.common.domain.instance import ProblemInstance
from models.routing.model_5.exact.accelerators.multibatch.vehicle_retime import build_vehicle_retiming, set_vehicle_projection, reoptimize_vehicle_retiming
from models.routing.model_5.exact.search.engine import solve_exact
from tests.routing.test_multibatch_cycle import fixture, hand_solution
from tests.routing.test_multibatch_departure_loads import nonmetric_problem


@pytest.mark.parametrize('seed',range(6))
@pytest.mark.parametrize('backend',['patterns','indexed'])
def test_known_nonmetric_optimum_projects_and_cannot_be_improved(seed,backend):
    gp=pytest.importorskip('gurobipy')
    problem=nonmetric_problem(seed)
    initial=solve_exact(problem,time_limit=5)
    assert initial['info']['proven_optimal'] and audit_solution(problem,initial)['valid']
    original=deepcopy(initial)
    model,context=build_vehicle_retiming(problem,initial,backend=backend)
    try:
        set_vehicle_projection(problem,context,initial,fix=True)
        model.optimize()
        assert model.Status==gp.GRB.OPTIMAL
        assert model.ObjVal==pytest.approx(initial['info']['objective'])
    finally:model.dispose()
    result=reoptimize_vehicle_retiming(problem,initial,time_limit=3,backend=backend)
    assert audit_solution(problem,result)['valid'] and initial==original
    assert result['info']['objective']==pytest.approx(initial['info']['objective'])
    assert result['info']['subproblem_optimal']
    assert not result['info']['proven_optimal'] and not result['info']['proven_infeasible']
    assert result['info']['best_bound'] is None and result['info']['mip_gap'] is None


@pytest.mark.parametrize('vehicles',[1,2])
def test_preparation_overlap_needs_a_different_original_physical_vehicle(vehicles):
    gp=pytest.importorskip('gurobipy')
    base=fixture(pool=2,e2_dispatch_prep_time=2)
    network=ProblemInstance(base.network.nodes,
        dict(E1_vehicle_count=1,vehicles_per_satellite=vehicles,E1_Qk=500,E1_Ck=500,
             E2_Qv=500,E2_Cv=500,container_weight=1,container_volume=1))
    problem=MultiBatchInstance(network,
        [replace(base.orders[0],pickup_window=(70,70)),
         replace(base.orders[1],delivery_window=(74,74))],base.operating_policy)
    initial=hand_solution(problem,71)
    first,second=initial['e2_trips']
    first['departure'],first['events'][0]['start'],first['events'][1]['start']=12,13,70
    second['departure'],second['events'][0]['start']=73,74
    assert audit_solution(problem,initial)['valid']
    model,context=build_vehicle_retiming(problem,initial)
    try:
        model.addConstr(context['e2_timing'][1][0]==72)
        model.optimize()
        if vehicles==1:
            assert model.Status==gp.GRB.INFEASIBLE
        else:
            assert model.Status==gp.GRB.OPTIMAL
            assigned=[next(v for v in range(2) if context['e2_assignment'][i,v].X>.5) for i in range(2)]
            assert assigned[0]!=assigned[1]
            candidate=deepcopy(initial)
            for t,v in zip(candidate['e2_trips'],assigned):t['vehicle_id']=v;t['trip_index']=0
            candidate['e2_trips'][1]['departure']=72
            assert audit_solution(problem,candidate)['valid']
    finally:model.dispose()


def test_zero_duration_reverse_vehicle_order_cannot_precede_its_own_delivery():
    gp=pytest.importorskip('gurobipy')
    base=fixture(pool=2,cleaning=0,zero=True,e2_max_trips_per_vehicle=3)
    problem=MultiBatchInstance(base.network,
        [Order('a','C','S','b1',1,1,(10,10),(10,10),0,0),
         Order('b','C','S','b1',1,1,(10,10),(10,10),0,0)],base.operating_policy)
    initial=empty_result(problem,'manual')
    initial['e1_trips']=[dict(trip_id='e1',vehicle_id=0,batch_id='b1',departure=0,return_complete=30,
        events=[dict(kind='d',site_id='S',quantity=2,start=0),dict(kind='p',site_id='S',quantity=2,start=30)])]
    for i,sequence in enumerate([[('a','d')],[('b','d'),('a','p')],[('b','p')]]):
        initial['e2_trips'].append(dict(trip_id=f't{i}',satellite_id='S',vehicle_id=0,trip_index=i,
            departure=10,return_complete=10,
            events=[dict(order_id=o,kind=k,start=10,quantity=1) for o,k in sequence]))
    assert audit_solution(problem,initial)['valid']
    model,context=build_vehicle_retiming(problem,initial)
    try:
        model.addConstr(context['e2_before'][0,1]==0)
        model.optimize()
        assert model.Status==gp.GRB.INFEASIBLE
        # Pure clock inequalities admit this zero-time causal cycle. Removing
        # just rank rows reproduces that weakness independently of extraction.
        model.remove([r for r in model.getConstrs() if r.ConstrName.startswith('fleet_rank_')])
        model.optimize()
        assert model.Status==gp.GRB.OPTIMAL
    finally:model.dispose()


def test_positive_e1_turnaround_uses_indexed_physical_e1_and_preserves_fixed_paths():
    pytest.importorskip('gurobipy')
    problem=fixture(pool=2,e1_turnaround_time=2,e2_dispatch_prep_time=2)
    initial=hand_solution(problem)
    for trip in initial['e2_trips']:
        trip['departure']+=2;trip['events'][0]['start']+=2
    result=reoptimize_vehicle_retiming(problem,initial,time_limit=3)
    assert result['info']['vehicle_backend']=='indexed' and result['audit']['valid']
    assert [[(e['order_id'],e['kind']) for e in t['events']] for t in result['e2_trips']]==[
        [(e['order_id'],e['kind']) for e in t['events']] for t in initial['e2_trips']]
    with pytest.raises(ValueError,match='zero E1 turnaround'):
        build_vehicle_retiming(problem,initial,backend='patterns')


def test_budget_and_input_guards_keep_the_audited_incumbent_and_no_global_proof():
    pytest.importorskip('gurobipy')
    problem=fixture(pool=2)
    initial=hand_solution(problem)
    result=reoptimize_vehicle_retiming(problem,initial,time_limit=1e-12)
    assert result['audit']['valid'] and result['info']['objective']==44
    assert result['e2_trips']==initial['e2_trips'] and not result['info']['subproblem_optimal']
    for budget in (0,-1,math.nan,math.inf):
        with pytest.raises(ValueError,match='time_limit'):
            reoptimize_vehicle_retiming(problem,initial,time_limit=budget)
    with pytest.raises(ValueError,match='audit'):reoptimize_vehicle_retiming(problem,{})
    with pytest.raises(ValueError,match='backend'):build_vehicle_retiming(problem,initial,backend='bad')
