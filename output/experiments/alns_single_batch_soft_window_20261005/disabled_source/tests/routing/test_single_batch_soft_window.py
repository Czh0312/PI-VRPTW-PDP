"""Bounded intermediate infeasibility must never become feasible output."""
from copy import deepcopy
from types import SimpleNamespace as N
import math
import pytest

from models.routing.solvers.alns.single_batch_soft_window import bounded_window_score,BoundedWindowCache
from models.routing.solvers.alns import single_batch
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.neighborhoods import clone_state
from models.routing.common.cycle.audit import audit_solution
from models.routing.service import solve_routing
from tests.routing.test_single_batch_solver_audit import nonmetric_problem,tiny_problem

def fixture(tick=1,late_seconds=60):
    p=N(operating_policy=N(cycle_end=10000/tick,e2_turnaround_time=60/tick,
              time_tick_seconds=tick,batches=[N(cutoff=8000/tick)]),
        order_map={'a':N(delivery_window=(0,6000/tick),pickup_window=(0,7000/tick),service_time=30/tick)})
    result=dict(info=dict(objective=100.0),e2_trips=[dict(departure=1000/tick,return_complete=9000/tick,
            events=[dict(order_id='a',kind='d',start=(6000+late_seconds)/tick)])])
    return p,(1000100.0,result,['E2_TIME_WINDOW'])

@pytest.mark.parametrize('tick',[1,30,60])
@pytest.mark.parametrize('late',[1,60,300])
def test_units_linear_severity_and_original_result_immutable(tick,late):
    p,raw=fixture(tick,late);saved=deepcopy(raw)
    adjusted=bounded_window_score(p,raw)
    assert adjusted[0]==pytest.approx(100+10*late/60)
    assert adjusted[1] is raw[1] and adjusted[2] is raw[2]
    assert raw==saved

@pytest.mark.parametrize('late',[0,301,1000])
def test_no_lateness_or_cap_exceeded_keeps_raw(late):
    p,raw=fixture(late_seconds=late)
    assert bounded_window_score(p,raw) is raw

@pytest.mark.parametrize('code',['E2_CAPACITY','TIMING_CYCLE','E2_TASK_COVERAGE','CLEAN_STOCK_SHORTAGE',
                               'TERMINAL_OUTSTANDING','E1_WEIGHT_CAPACITY','E2_TRIP_LIMIT'])
def test_any_other_violation_keeps_original_score(code):
    p,raw=fixture();raw[2].append(code)
    assert bounded_window_score(p,raw) is raw

@pytest.mark.parametrize('distance',[None,float('nan'),float('inf')])
def test_invalid_objective_keeps_raw(distance):
    p,raw=fixture();raw[1]['info']['objective']=distance
    assert bounded_window_score(p,raw) is raw

def test_nonfinite_schedule_keeps_raw_even_with_another_late_event():
    p,raw=fixture();raw[1]['e2_trips'][0]['return_complete']=float('nan')
    assert bounded_window_score(p,raw) is raw

def test_delivery_cutoff_service_and_return_deadline_both_count():
    p,raw=fixture();p.order_map['a'].delivery_window=(0,9000)
    trip=raw[1]['e2_trips'][0];trip['events'][0]['start']=8030;trip['return_complete']=10000
    # Delivery due=min(9000,8000-30)=7970, return due=10000-60.
    assert bounded_window_score(p,raw)[0]==120

def test_pickup_uses_pickup_window_without_delivery_cutoff():
    p,raw=fixture();e=raw[1]['e2_trips'][0]['events'][0];e.update(kind='p',start=7060)
    assert bounded_window_score(p,raw)[0]==110

def test_wrapper_preserves_cached_raw_values_and_delegates_context():
    p,raw=fixture()
    class Cache:
        hits=12
        def __call__(self,state):return raw
    wrapper=BoundedWindowCache(p,Cache())
    assert wrapper({})[0]==110 and wrapper({})[0]==110
    assert raw[0]==1000100 and wrapper.hits==12 and wrapper.eligible_scores==2 and wrapper.last_softened
    raw[2].clear()
    assert wrapper({}) is raw and not wrapper.last_softened

@pytest.mark.parametrize('recover',[False,True])
def test_real_infeasible_acceptance_never_replaces_audited_output(monkeypatch,recover):
    p=nonmetric_problem()
    good=dict(deliveries={'b1':[['T','S']]},pickup_order={'b1':['S','T']},pickup_vehicle={'b1':{}},
        e2=[dict(satellite='S',vehicle=0,tasks=[('a','d'),('a','p')]),
            dict(satellite='T',vehicle=0,tasks=[('b','d'),('b','p')])])
    bad=clone_state(good);bad['e2'][0]['depart_after']=26
    raw=evaluate(p,bad);assert raw[2]==['E2_TIME_WINDOW'] and not audit_solution(p,raw[1])['valid']
    assert bounded_window_score(p,raw)[0]==48
    clock={'done':False};calls=[]
    class Rng:
        def choices(self,*args,**kwargs):return [0]
        def random(self):
            if not recover:clock['done']=True
            return 0.0
    def move(problem,current,rng,operator):
        calls.append(clone_state(current))
        if len(calls)==1:return clone_state(bad)
        assert recover and current==bad
        clock['done']=True
        return clone_state(good)
    monkeypatch.setattr(single_batch.random,'Random',lambda seed:Rng())
    monkeypatch.setattr(single_batch,'initial_state',lambda *args,**kw:clone_state(good))
    monkeypatch.setattr(single_batch,'_move',move)
    monkeypatch.setattr(single_batch.time,'perf_counter',lambda:2.0 if clock['done'] else 0.0)
    result=single_batch.solve_alns(p,max_time=1,max_iterations=100)
    assert result['info']['soft_window_accepted_infeasible']==1
    assert result['info']['soft_window_entered_from_feasible']==1
    assert result['info']['soft_window_returns_to_feasible']==int(recover)
    assert result['info']['objective']==8 and audit_solution(p,result)['valid']
    assert result['audit']['periodic_replay']['valid']

@pytest.mark.parametrize('model',[1,2,3,4])
def test_public_scope_and_fresh_decoder(model):
    p=tiny_problem(model,1)
    result=solve_routing(model=model,method='alns',problem=p,alns_time=30,alns_iter=100,seed=77)
    assert result['info']['soft_window_enabled']==(model==3)
    assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']
    if model!=3:
        assert result['info']['soft_window_accepted_infeasible']==0
