"""Regression cases for missing cleanup breakpoints and weak timing relaxation."""
from dataclasses import replace
import random
import time

import pytest

from models.routing.common.cycle.joint import JointBatchInstance
from models.routing.common.cycle.uniform import UniformCycleInstance
from models.routing.variants.model_6 import solve_alns, solve_exact
from models.routing.variants.model_6.alns.solver import (
    JointEvaluator, _best_construction, _cutoff_events, _polish_cutoff)
from tests.routing.test_model6_joint import joint_fixture, exact_available


def test_polish_hits_cleaning_equality_and_reduces_pool_without_changing_routes():
    base=joint_fixture(pool_rate=30,cleaning=25)
    p=JointBatchInstance(base.network,base.orders,base.operating_policy,
                        dict(optimize_cutoffs=True,shared_cutoff=True,
                             cutoff_bounds_minutes={'b1':[20,50],'b2':[80,110]},
                             fixed_assignment={'o1':'b1','o2':'b2'}),base.cost_profile.config)
    assignment={'o1':'b1','o2':'b2'}
    evaluator=JointEvaluator(p)
    score,state,result,_=_best_construction(p,evaluator,assignment,random.Random(42),time.perf_counter()+2)
    assert result['audit']['valid'] and result['container_pool_size']==2
    # 40 cutoff + 5 return + 25 cleaning misses the next issue at 60.
    # The exact reuse boundary is 30, not an empty arrival or a supply time.
    assert 30 in _cutoff_events(p,state['cutoffs'],result,'b1')
    polished=_polish_cutoff(p,evaluator,assignment,state,time.perf_counter()+2)
    improved,candidate,_=evaluator(assignment,polished)
    assert candidate['audit']['valid'] and candidate['container_pool_size']==1
    assert candidate['audit']['periodic_replay']['valid'] and improved<score
    offsets={polished['cutoffs'][b.id]-b.start for b in p.operating_policy.batches}
    assert len(offsets)==1 and next(iter(offsets))<=30
    from models.routing.variants.model_6.alns.solver import _with_cutoff
    assert evaluator(assignment,_with_cutoff(p,state,'b1',30))[1]['container_pool_size']==1
    assert evaluator(assignment,_with_cutoff(p,state,'b1',31))[1]['container_pool_size']==2
    for key in ('deliveries','pickup_order','pickup_vehicle','e2'):
        assert state[key]==polished[key]


def test_late_pickup_initialization_finds_a_narrow_feasible_cutoff_without_random_moves():
    base=joint_fixture(zero=True,cleaning=1)
    orders=[replace(base.orders[0],delivery_window=(0,1),pickup_window=(125,125),min_use_time=0)]
    p=UniformCycleInstance(base.network,orders,base.operating_policy,
                          dict(schedule_mode='uniform_cycle',batch_count_candidates=[1]),base.cost_profile.config)
    result=solve_alns(p,max_time=.2,max_iterations=0)
    assert result['audit']['valid'] and result['shared_cutoff_offset_ticks']==125
    assert result['container_pool_size']==1 and result['audit']['periodic_replay']['valid']


def test_shared_events_include_other_batches_and_cleaning_terminal_boundary():
    base=joint_fixture()
    p=JointBatchInstance(base.network,base.orders,base.operating_policy,
                        dict(optimize_cutoffs=True,shared_cutoff=True),base.cost_profile.config)
    cuts=p.default_cutoffs
    decoded=dict(e2_trips=[dict(return_complete=95,events=[dict(kind='p')])],
                 e1_trips=[dict(batch_id='b1',return_complete=50,events=[dict(kind='p')])])
    targets=_cutoff_events(p,cuts,decoded,'b1')
    assert 35 in targets  # b2 return at 95 corresponds to the same relative d=35.
    assert 45 in targets  # current d=40 + next start 60 - cleaning 5 - return 50.
    assert {34,35,36}.issubset(targets)


def test_route_duration_lower_bound_strengthens_lp_and_preserves_exact_optimum(exact_available):
    from models.routing.variants.model_6.exact.model import build_model
    p=joint_fixture()
    model,_=build_model(p)
    relaxed=None
    try:
        relaxed=model.relax()
        relaxed.Params.OutputFlag=0
        relaxed.optimize()
        assert relaxed.Status==2
        assert relaxed.ObjVal>=p.cost_profile.constant_cost(p.total)['cleaning']+p.pool_min*p.cost_profile.rates['container_pool_cost_per_period']
    finally:
        if relaxed is not None:
            relaxed.dispose()
        model.dispose()
    result=solve_exact(p,time_limit=5)
    assert result['audit']['valid'] and result['info']['proven_optimal']
    assert result['info']['objective']==pytest.approx(47.1)


def test_uniform_operator_statistics_cover_all_counts():
    from tests.routing.test_model6_uniform import uniform_fixture
    result=solve_alns(uniform_fixture(),max_time=.4,max_iterations=20)
    assert result['audit']['valid']
    for key in ('operator_attempts','operator_improvements'):
        for operator,value in result['info'][key].items():
            assert value==sum(row.get(key,{}).get(operator,0) for row in result['count_search'])
    assert result['info']['evaluations']==sum(row.get('evaluations',0) for row in result['count_search'])
    for key in ('cutoff_polish_calls','cutoff_polish_improvements','cutoff_polish_evaluations'):
        assert result['info'][key]==sum(row.get(key,0) for row in result['count_search'])
