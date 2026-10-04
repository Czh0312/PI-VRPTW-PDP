"""Exact partial E1 rankings; final schedules remain independently decoded."""
from copy import deepcopy
from dataclasses import replace
import random

import pytest

from models.routing.solvers.alns.cycle_cache import CycleEvaluator
from models.routing.solvers.alns.evaluator import _e1_trip, _e1_returns, evaluate, initial_state, route_distance
from models.routing.solvers.alns.neighborhoods import clone_state
from models.routing.solvers.alns.single_batch_cache import SingleBatchCache, SingleBatchEvaluator
from tests.routing.test_single_batch_solver_audit import tiny_problem


@pytest.mark.parametrize('seed',range(12))
def test_return_rankings_full_schedules_and_memo_ownership_match_reference(seed):
    p=tiny_problem(3,seed);p.fleet.e1_count=2
    state=initial_state(p,random.Random(seed))
    cache=SingleBatchEvaluator(p,capacity=2,trial_capacity=8)
    reference=CycleEvaluator(p,capacity=2)
    rng=random.Random(seed)
    for step in range(8):
        q={'b1':{s:rng.randrange(4) for s in p.satellites}}
        rng.shuffle(state['pickup_order']['b1'])
        state['pickup_vehicle']['b1']={s:rng.randrange(2) for s in p.satellites}
        expected=_e1_returns(p,state,q)
        actual=cache.returns(state,q)
        assert actual==expected==reference.returns(state,q)
        saved=deepcopy(actual)
        if actual[0]:actual[0][0]['events'][0]['quantity']=-123
        actual[1].append('caller-only')
        assert cache.returns(state,q)==saved
        assert len(cache.trials)<=8 and len(cache.entries)<=2
    assert cache.hits==reference.hits+8
    assert cache.misses==reference.misses


@pytest.mark.parametrize('changed',['delivery_order','pickup_order','selected_quantity','readiness','vehicle'])
def test_exact_trial_key_covers_inputs(changed):
    p=tiny_problem(3,1);cache=SingleBatchEvaluator(p);batch=p.operating_policy.batches[0]
    deliveries=['S','T'];pickups=['S','T'];q={'S':2,'T':1};ready=p.operating_policy.cycle_start;vehicle=0
    a=cache.score_trial(batch,vehicle,deliveries,pickups,q,ready)
    assert cache.score_trial(batch,vehicle,deliveries,pickups,dict(q),ready)==a
    assert cache.trial_hits==1 and cache.trial_misses==1
    if changed=='delivery_order':deliveries.reverse()
    elif changed=='pickup_order':pickups.reverse()
    elif changed=='selected_quantity':q['T']=100
    elif changed=='readiness':ready=batch.start+1
    else:vehicle=1
    trip,issues=_e1_trip(p,batch,vehicle,deliveries,pickups,q,ready)
    assert cache.score_trial(batch,vehicle,deliveries,pickups,q,ready)==(len(issues),route_distance(p,trip,'e1'))
    assert cache.trial_misses==2


def test_only_selected_quantities_enter_trial_key_and_capacity_is_fresh():
    p=tiny_problem(3,1);cache=SingleBatchEvaluator(p);b=p.operating_policy.batches[0]
    first=cache.score_trial(b,0,['S','T'],['S'],{'S':1,'T':1},0)
    assert cache.score_trial(b,0,['S','T'],['S'],{'S':1,'T':999},0)==first
    assert cache.trial_hits==1
    second=cache.score_trial(b,0,['S','T'],['S'],{'S':999,'T':999},0)
    trip,issues=_e1_trip(p,b,0,['S','T'],['S'],{'S':999,'T':999},0)
    assert 'E1_WEIGHT_CAPACITY' in issues and second==(len(issues),route_distance(p,trip,'e1'))


def test_foreign_batch_and_missing_quantity_keep_reference_behavior():
    p=tiny_problem(3,1);cache=SingleBatchEvaluator(p);b=p.operating_policy.batches[0]
    other=replace(b,cutoff=b.cutoff+2)
    trip,issues=_e1_trip(p,other,0,['S','T'],['S'],{'S':1},0)
    assert cache.score_trial(other,0,['S','T'],['S'],{'S':1},0)==(len(issues),route_distance(p,trip,'e1'))
    with pytest.raises(KeyError):cache.score_trial(b,0,['S','T'],['S'],{},0)
    assert not cache.trials


def test_partial_cache_is_bounded_and_contexts_are_fresh():
    p=tiny_problem(3,1);a=SingleBatchEvaluator(p,trial_capacity=2);b=SingleBatchEvaluator(p,trial_capacity=2)
    batch=p.operating_policy.batches[0]
    for quantity in range(4):a.score_trial(batch,0,['S','T'],['S'],{'S':quantity},0)
    assert len(a.trials)==2 and b.trial_hits==b.trial_misses==0 and not b.trials
    a.score_trial(batch,0,['S','T'],['S'],{'S':0},0)
    assert a.trial_misses==5


@pytest.mark.parametrize('seed',range(8))
def test_full_decoder_penalties_trips_and_ledgers_equal_disabled_cache(seed):
    p=tiny_problem(3,seed);rng=random.Random(seed);state=initial_state(p,rng)
    enabled=SingleBatchCache(p,trial_capacity=16);disabled=SingleBatchCache(p,trial_capacity=0)
    for step in range(6):
        saved=deepcopy(state)
        assert enabled(state)==disabled(state)==evaluate(p,state)
        assert state==saved
        state=clone_state(state)
        rng.shuffle(state['pickup_order']['b1'])
        if state['e2']:
            rng.shuffle(state['e2'])
            state['e2'][0]['return_after']=p.operating_policy.batches[0].cutoff+step


def test_final_selected_trips_do_not_use_trial_cache(monkeypatch):
    from models.routing.solvers.alns import evaluator
    p=tiny_problem(3,1);state=initial_state(p,random.Random(1));cache=SingleBatchEvaluator(p)
    quantities={'b1':{s:1 for s in p.satellites}}
    calls=[];original=evaluator._e1_trip
    def counted(*args):calls.append(args);return original(*args)
    monkeypatch.setattr(evaluator,'_e1_trip',counted)
    _e1_returns(p,state,quantities,score_trial=cache.score_trial)
    first=len(calls)
    _e1_returns(p,state,quantities,score_trial=cache.score_trial)
    assert len(calls)==2*first and cache.trial_hits>0
    assert first==p.fleet.e1_count


@pytest.mark.parametrize('capacity',[-1,1.5,None])
def test_invalid_trial_capacity_is_rejected(capacity):
    with pytest.raises(ValueError,match='E1 trial capacity'):
        SingleBatchEvaluator(tiny_problem(3,0),trial_capacity=capacity)


@pytest.mark.parametrize('model',[1,2,3,4])
def test_public_trial_cache_enabled_only_for_fixed_pickup_model(monkeypatch,model):
    from models.routing.service import solve_routing
    from models.routing.common.cycle.audit import audit_solution
    calls=[];original=SingleBatchEvaluator.__init__
    def counted(self,*args,**kwargs):
        calls.append(kwargs.get('trial_capacity',1024));original(self,*args,**kwargs)
    monkeypatch.setattr(SingleBatchEvaluator,'__init__',counted)
    p=tiny_problem(model,0)
    result=solve_routing(model=model,method='alns',problem=p,alns_time=5,alns_iter=160,seed=231)
    assert calls and all(c==(1024 if model==3 else 0) for c in calls)
    if model==3:
        assert result['info']['e1_trial_cache_capacity']==1024
        assert result['info']['e1_trial_cache_entries']<=1024
    audit=audit_solution(p,result)
    assert audit['valid'] and audit['periodic_replay']['valid']
