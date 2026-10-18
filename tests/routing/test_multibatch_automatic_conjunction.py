"""Automatic pairs use current necessary supplies and the original deadline."""
from copy import deepcopy
import time

import pytest

from models.routing.model_5.exact.accelerators.multibatch.conditional_inventory import refine_conditional_bound, select_supply_conjunctions
from tests.routing.test_multibatch_cycle import fixture, hand_solution


def test_selector_requires_two_nontrivial_batches_and_ranks_actual_master_delays():
    p=fixture(pool=2)
    incumbent={('S','b1'):10,('S','b2'):70}
    rows=[dict(site='S',batch='b1',supply=12),dict(site='S',batch='b2',supply=73)]
    before=deepcopy(rows)
    expected=[dict(site='S',supply_conditions=[dict(batch='b1',threshold=12),dict(batch='b2',threshold=73)])]
    assert select_supply_conjunctions(p,rows,incumbent,{'S':4})==expected
    assert select_supply_conjunctions(p,list(reversed(rows)),incumbent,{'S':4})==expected
    assert rows==before
    assert select_supply_conjunctions(p,rows[:1],incumbent,{'S':4})==[]
    assert select_supply_conjunctions(p,rows,incumbent,{'S':4},max_conjunctions=0)==[]
    rows[0]['supply']=10 # Exactly the unconditional release does not tighten.
    assert select_supply_conjunctions(p,rows,incumbent,{'S':4})==[]


def test_automatic_refinement_prices_joint_context_before_singles_with_shared_deadline(monkeypatch):
    import models.routing.model_5.exact.accelerators.multibatch.conditional_inventory as inventory
    import models.routing.model_5.exact.accelerators.multibatch.route_cg as cg
    from models.routing.model_5.exact.accelerators.pickup_route_pricing import route_context, evaluate_column
    p=fixture(pool=2);initial=hand_solution(p)
    context=route_context(p,'S')
    columns=[evaluate_column(p,context,[(e['order_id'],e['kind']) for e in t['events']]) for t in initial['e2_trips']]
    reference=dict(bound=40,e1_bound=40,components=[dict(satellite='S',bound=0)],
                   sites=[dict(satellite='S',columns=columns)])
    supplied=[dict(site='S',batch='b1',supply=12),dict(site='S',batch='b2',supply=73)]
    masters=[];leaves=[];deadline=time.perf_counter()+100
    def master(p,floors,rows,**kw):
        assert kw['deadline']<=deadline
        masters.append(deepcopy(rows))
        return dict(bound=43 if rows else 40,supplies=supplied)
    def price(p,context,**kw):
        assert kw['deadline']<deadline and kw['batch_pricing'] and kw['dual_repair']
        leaves.append(context)
        return dict(bound=3,columns=[])
    monkeypatch.setattr(inventory,'solve_conditional_inventory',master)
    monkeypatch.setattr(cg,'site_route_cover',price)
    out=refine_conditional_bound(p,reference,initial,deadline=deadline,max_conditions=3,max_supply_conjunctions=1)
    assert len(leaves)==1 and leaves[0]['release']=={'b1':12,'b2':73}
    assert masters[1]==[dict(site='S',supply_conditions=[dict(batch='b1',threshold=12),dict(batch='b2',threshold=73)],bound=3)]
    assert out['conditions'][0]['supply_conditions']==masters[1][0]['supply_conditions']
    assert out['bound']==43 and len(out['conditions'])<=3


def test_public_options_reach_all_primal_passes_and_conditional_selection(monkeypatch):
    from models.routing.model_5.exact.accelerators.multibatch.hybrid import solve_hybrid
    import models.routing.service as service
    import models.routing.model_5.exact.accelerators.multibatch.route_pool as pool
    import models.routing.model_5.exact.accelerators.multibatch.route_cg as cg
    import models.routing.model_5.exact.accelerators.multibatch.conditional_inventory as inventory
    p=fixture(pool=2);initial=hand_solution(p);primals=[];conditions=[]
    monkeypatch.setattr(service,'solve_routing',lambda **kw:deepcopy(initial))
    def primal(p,result,**kw):
        primals.append((kw['extra_slots_per_vehicle'],kw['max_columns']))
        result['info']['runtime']=0
        return result
    def cover(p,**kw):
        return dict(bound=40,e1_bound=40,components=[dict(satellite='S',bound=0)],sites=[],runtime=0)
    def conditional(p,ref,result,**kw):
        conditions.append(kw['max_supply_conjunctions'])
        return dict(bound=43,runtime=0,conditions=[],masters=[])
    monkeypatch.setattr(pool,'reoptimize_route_pool',primal)
    monkeypatch.setattr(cg,'route_cover_certificate',cover)
    monkeypatch.setattr(inventory,'refine_conditional_bound',conditional)
    out=solve_hybrid(p,time_limit=1,primal_time_limit=.1,cover_time_limit=.1,conditional_time_limit=.1,
                     extra_slots_per_vehicle=1,route_pool_max_columns=64,max_supply_conjunctions=1,adaptive_finish=False)
    assert primals==[(1,64),(1,64)] and conditions==[1] and out['target_reached']


@pytest.mark.parametrize('value',[-1,True,.5])
def test_invalid_automatic_counts_are_rejected_before_solving(value):
    from models.routing.model_5.exact.accelerators.multibatch.hybrid import solve_hybrid
    p=fixture(pool=2)
    with pytest.raises(ValueError):solve_hybrid(p,max_supply_conjunctions=value)
    with pytest.raises(ValueError):solve_hybrid(p,route_pool_max_columns=value)
    with pytest.raises(ValueError):select_supply_conjunctions(p,[],{}, {'S':0},max_conjunctions=value)


def test_zero_pool_column_limit_is_rejected():
    from models.routing.model_5.exact.accelerators.multibatch.hybrid import solve_hybrid
    with pytest.raises(ValueError):solve_hybrid(fixture(pool=2),route_pool_max_columns=0)
