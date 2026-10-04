"""Scope, queue telemetry and audited behavior of the wider M3 pilot."""
from copy import deepcopy
from dataclasses import replace
import random,time
import pytest
from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution
from models.routing.service import solve_routing
from models.routing.solvers.alns import single_batch
from models.routing.solvers.alns.construction import chronological_state
from models.routing.solvers.alns.evaluator import evaluate
from models.routing.solvers.alns.single_batch_descent import SingleBatchDescent
from tests.routing.test_single_batch_solver_audit import tiny_problem

@pytest.mark.parametrize('model',[1,2,3,4,5])
def test_public_model_scope_and_fresh_full_audit(monkeypatch,model):
    caps=[]
    class Probe(SingleBatchDescent):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);caps.append(self.capacity)
    monkeypatch.setattr(single_batch,'SingleBatchDescent',Probe)
    if model==5:
        with pytest.raises(ValueError,match='Model 5 requires B>1'):
            tiny_problem(model,1)
        assert caps==[]
        return
    p=tiny_problem(model,1);saved=deepcopy(p.orders)
    result=solve_routing(model=model,method='alns',problem=p,alns_time=5,alns_iter=100,seed=271)
    audit=audit_solution(p,result)
    assert audit['valid'] and audit['periodic_replay']['valid'] and p.orders==saved
    if model==3:
        assert caps and set(caps)=={1024}
        assert result['info']['descent_queue_capacity']==1024
        assert 0<=result['info']['descent_e2_queue_peak']<=1024
    else:
        assert all(cap==256 for cap in caps)
        assert result['info'].get('descent_queue_capacity',256)==256
        if model==4:assert result['info']['descent_e2_queue_peak']==0

@pytest.mark.parametrize('capacity',[0,-1,True,1.5,None,'wide'])
def test_capacity_invalid_values_rejected(capacity):
    with pytest.raises(ValueError,match='positive integer'):
        single_batch.solve_alns(tiny_problem(3,1),max_iterations=0,_descent_capacity=capacity)

def test_disabled_capacity_and_zero_iterations_keep_original_queue(monkeypatch):
    caps=[]
    class Probe(SingleBatchDescent):
        def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);caps.append(self.capacity)
    monkeypatch.setattr(single_batch,'SingleBatchDescent',Probe)
    result=single_batch.solve_alns(tiny_problem(3,1),max_time=5,max_iterations=0,_descent_capacity=256)
    assert caps==[256] and result['info']['descent_queue_capacity']==256
    assert result['info']['descent_e2_queue_peak']==0
    assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']

def test_telemetry_tracks_largest_single_e2_queue_not_e1_or_sum(monkeypatch):
    class Probe(SingleBatchDescent):
        def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.calls=0
        def propose(self,*args):
            self.calls+=1;self.e2_options=[None]*(400 if self.calls==1 else 3)
            self.generated+=10000 # Includes other queues; must not be peak.
            return None
    monkeypatch.setattr(single_batch,'SingleBatchDescent',Probe)
    result=single_batch.solve_alns(tiny_problem(3,1),max_time=5,max_iterations=10,seed=271)
    assert result['info']['descent_e2_queue_peak']==400
    assert result['info']['descent_candidates']>=20000
    assert result['audit']['valid'] and result['audit']['periodic_replay']['valid']

def test_wider_directed_queue_preserves_tasks_state_and_distance_order():
    base=tiny_problem(3,1)
    orders=[replace(o,id='%s_%d'%(o.id,index)) for index in range(5) for o in base.orders]
    p=MultiBatchInstance(base.network,orders,replace(base.operating_policy,e2_max_trips_per_vehicle=12),model=3)
    state=chronological_state(p,random.Random(271));saved=deepcopy(state)
    wide=SingleBatchDescent(p,capacity=1024,cache_sites=False,cache_distances=True)
    small=SingleBatchDescent(p,capacity=256,cache_sites=False,cache_distances=True)
    options=wide._enumerate_routes(state['e2'],time.perf_counter()+10)
    original=small._enumerate_routes(state['e2'],time.perf_counter()+10)
    assert len(original)<=256 and len(options)<=1024
    assert [(gain,changes) for gain,serial,changes in original]==[(gain,changes) for gain,serial,changes in options[:256]]
    assert [gain for gain,serial,changes in options]==sorted([gain for gain,serial,changes in options],reverse=True)
    assert state==saved
    # Full independent audit remains authoritative for every proposal offered.
    _,decoded,issues=evaluate(p,state)
    if not issues:
        proposal=wide.propose(state,decoded,time.perf_counter()+1)
        if proposal is not None:
            _,result,issues=evaluate(p,proposal);audit=audit_solution(p,result)
            if not issues:assert audit['valid'] and audit['periodic_replay']['valid']
    assert state==saved
