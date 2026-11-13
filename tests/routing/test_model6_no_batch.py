"""Independent physical counterexamples and hand-solvable Model 6 optima."""
from copy import deepcopy
from dataclasses import replace
import json
import subprocess
import sys

import pytest

from models.common.domain.node import Depot, Satellite, Customer
from models.common.domain.instance import ProblemInstance
from models.common.cycle.instance import Order
from models.common.no_batch import NoBatchInstance, NoBatchPolicy, audit_solution
from models.routing.model_6.exact import solve_exact
from models.routing.model_6.alns import solve_alns
from models.routing.model_6.alns.state import State, Route
from models.routing.model_6.alns.evaluator import evaluate


def tiny(pool=2,objective='transport',**policy_overrides):
    raw=dict(policy_version='no_batch_container_cycle_v1',time_unit='minutes',time_tick_seconds=60,
             cycle_start=0,cycle_end=120,cleaning_duration=5,e1_load_time=2,
             e2_dispatch_prep_time=1,e2_return_unload_time=1,depot_return_unload_time=1,
             e1_turnaround_time=0,e2_turnaround_time=1,e2_max_trips_per_vehicle=4)
    raw.update(policy_overrides)
    p=NoBatchPolicy.from_config(raw)
    nodes={'D':Depot('D',0,0),'S':Satellite('S',1,0,1),
           'C':Customer('C',2,0,0,1,(0,120),(0,120),'S')}
    fleet=dict(E1_vehicle_count=1,vehicles_per_satellite=1,E1_Qk=10,E1_Ck=10,E2_Qv=10,E2_Cv=10,
               container_weight=1,container_volume=1,speed=1)
    orders=[Order('a','C','S','',1,1,(10,20),(15,30),1,2),
            Order('b','C','S','',1,1,(60,70),(65,80),1,2)]
    return NoBatchInstance(ProblemInstance(nodes,fleet),orders,p,dict(pool_max=pool,objective=objective))


def hand_state():
    return State(dict(e1=[Route('e1','D',0,[('a','d'),('a','p')]),Route('e1','D',0,[('b','d'),('b','p')])],
                      e2=[Route('e2','S',0,[('a','d'),('a','p')]),Route('e2','S',0,[('b','d'),('b','p')])]),[('a','b')])


def hand_result(p=None):
    p=p or tiny()
    return evaluate(p,hand_state())['result']


def codes(a):return {e['code'] for e in a['errors']}


def test_hand_reuse_and_true_daily_repeat():
    p=tiny(pool=1);r=hand_result(p);a=audit_solution(p,r)
    assert a['valid'],a['errors']
    assert a['minimum_pool_for_schedule']==1
    assert a['transport_cost']==pytest.approx(.6)
    assert a['periodic_replay']['valid'] and a['periodic_replay']['repeat_interval']==1440
    assert a['terminal_state']['clean']==1
    assert all(sum(x['states'].values())==1 for x in a['container_ledger'])


def test_boxes_are_reserved_at_loading_start_not_departure():
    p=tiny();r=hand_result(p)
    first,second=r['e1_trips']
    clean=first['return_complete']+p.operating_policy.cleaning_duration
    second['loading_start']=clean-1;second['departure']=clean+1
    r.pop('container_pool_size')
    a=audit_solution(p,r)
    assert a['valid'],a['errors']
    assert a['minimum_pool_for_schedule']==2
    assert 'POOL_LIMIT' in codes(audit_solution(p.with_options(pool_max=1),r))


def test_empty_available_only_after_small_vehicle_unloading():
    p=tiny();r=hand_result(p)
    r['e1_trips'][0]['events'][1]['start']=r['e2_trips'][0]['return_complete']-1
    assert 'EMPTY_NOT_READY_FOR_LOADING' in codes(audit_solution(p,r))


def test_no_loading_overlap_on_same_physical_vehicle():
    p=tiny();r=hand_result(p)
    r['e1_trips'][1]['loading_start']=r['e1_trips'][0]['return_complete']-1
    assert 'VEHICLE_OVERLAP' in codes(audit_solution(p,r))


def test_end_requires_clean_and_vehicle_ready():
    p=tiny();r=hand_result(p)
    r['e1_trips'][-1]['return_complete']=p.operating_policy.cycle_end-1
    assert 'TERMINAL_DIRTY' in codes(audit_solution(p,r))
    r=hand_result(p);r['e2_trips'][-1]['return_complete']=p.operating_policy.cycle_end
    assert 'VEHICLE_NOT_READY_AT_END' in codes(audit_solution(p,r))


def test_duplicate_collection_and_wrong_warehouse_are_rejected():
    p=tiny();r=hand_result(p)
    r['e1_trips'][1]['events'][-1]['order_ids']=['a']
    assert {'DUPLICATE_TASK','MISSING_TASK'}<=codes(audit_solution(p,r))
    r=hand_result(p);r['e2_trips'][0]['satellite_id']='unknown'
    assert not audit_solution(p,r)['valid']


def test_full_boxes_required_before_e2_preparation():
    p=tiny();r=hand_result(p)
    r['e2_trips'][0]['loading_start']=r['e1_trips'][0]['events'][0]['start']
    assert 'FULL_BOX_NOT_READY_FOR_PREPARATION' in codes(audit_solution(p,r))


@pytest.mark.parametrize('cap,cost',[(2,.3),(1,.6)])
def test_native_exact_matches_hand_computed_optima(cap,cost):
    p=tiny(pool=cap);r=solve_exact(p,time_limit=25)
    assert r['feasible'] and r['info']['proven_optimal'],r
    assert r['info']['objective']==pytest.approx(cost)
    assert r['audit']['valid'] and r['audit']['periodic_replay']['valid']
    assert r['container_pool_size']==cap


def test_exact_min_pool_and_proven_infeasibility():
    r=solve_exact(tiny(objective='min_pool'),time_limit=25)
    assert r['info']['proven_optimal'] and r['container_pool_size']==1,r
    r=solve_exact(tiny(pool=0),time_limit=10)
    assert r['info']['proven_infeasible'] and not r['feasible'],r


@pytest.mark.parametrize('objective', ['transport','min_pool'])
def test_alns_finds_reuse_without_exact_imports(objective):
    p=tiny(pool=1,objective=objective)
    r=solve_alns(p,max_time=1,max_iterations=100,seed=42)
    assert r['feasible'],r
    assert r['container_pool_size']==1
    assert r['audit']['periodic_replay']['valid']
    assert not r['info']['proven_optimal']
    assert r['info']['operator_weights']


def test_alns_failure_does_not_claim_infeasibility():
    r=solve_alns(tiny(pool=0),max_time=.1,max_iterations=5)
    assert not r['feasible'] and r['info']['status']=='NO_SOLUTION_FOUND'
    assert not r['info']['proven_infeasible']


def test_audited_warm_start_preserves_reuse():
    p=tiny(pool=1);r=solve_alns(p,max_time=.2,max_iterations=0,initial_result=hand_result(p))
    assert r['feasible'] and r['container_pool_size']==1


def test_m6_model_entry_and_algorithm_isolation():
    code='''
import importlib.abc, sys
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.startswith(('gurobipy','scipy','models.routing.model_6.exact')):
   raise RuntimeError('ALNS imported exact backend: '+fullname)
  if fullname.startswith('models.routing.model_') and not fullname.startswith('models.routing.model_6'):
   raise RuntimeError('Cross-model solver import: '+fullname)
sys.meta_path.insert(0,Guard())
from tools.configuration.loader import load_routing_case
from models.common.no_batch import load_instance
from models.routing.service import solve_routing
c,f=load_routing_case('config/experiments/examples/model6_demo.json')
r=solve_routing(model=6,method='alns',problem=load_instance(c,f),alns_time=.2,alns_iter=5)
assert r['feasible']
assert r['model_module']=='models.routing.model_6.alns'
'''
    run=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
    assert run.returncode==0,run.stderr


def test_validate_unit_count_and_trip_domain():
    p=tiny()
    with pytest.raises(ValueError,match='one container'):
        NoBatchInstance(p.network,[replace(p.orders[0],count=2)],p.operating_policy)
    assert p.with_options(e1_max_trips_per_vehicle=1).restricted_trip_domain


def test_migrate_consolidated_single_batch_schedule():
    from tests.routing.test_unrestricted_work_window import daily_problem, solve
    from models.common.no_batch.migration import migrate_cycle_result
    old = daily_problem(3)
    source = solve(old, 'alns')
    copied = deepcopy(source)
    new, result = migrate_cycle_result(old, source)
    assert source == copied
    assert result['audit']['valid']
    assert result['container_pool_size'] <= old.operating_policy.container_pool_size
    assert new.operating_policy.repeat_interval == 1440


def test_partial_collection_leaves_other_empty_boxes_at_satellite():
    p=tiny()
    state=State(dict(e1=[Route('e1','D',0,[('a','d'),('b','d')]),
                        Route('e1','D',0,[('a','p')]),Route('e1','D',0,[('b','p')])],
                     e2=[Route('e2','S',0,[('a','d'),('a','p'),('b','d'),('b','p')])]))
    value=evaluate(p,state)
    assert value and value['result']['feasible']
    ledger=value['result']['audit']['container_ledger']
    assert any(row['states']['sat_empty:S']==1 for row in ledger)
    assert len(value['result']['e1_trips'])==3


def test_cli_saves_frontier_and_does_not_stop_on_unknown(monkeypatch,tmp_path):
    from cli.no_batch import run_case
    import cli.no_batch as cli
    from tools.configuration.loader import load_routing_case
    cfg,fleet=load_routing_case('config/experiments/examples/model6_demo.json')
    calls=[]
    def fake(**kwargs):
        calls.append(kwargs['problem'].pool_max)
        return dict(model=6,feasible=False,info=dict(status='NO_SOLUTION_FOUND',proven_optimal=False,
                                                   proven_infeasible=False,runtime=.01))
    monkeypatch.setattr(cli,'solve_routing',fake)
    run_case(cfg,fleet,output=tmp_path/'run',pool_caps=[3,2,1])
    assert calls==[3,2,1]
    assert (tmp_path/'run/frontier.csv').is_file()
    assert (tmp_path/'run/manifest.json').is_file()


def test_nonmetric_shortest_path_bound_is_optimistic():
    from models.common.no_batch.bounds import physical_bounds
    p=tiny()
    p._travel['D','S']=50
    p._travel['D','C']=1;p._travel['C','S']=1
    bound=physical_bounds(p)['orders']['a']
    assert bound['e1_delivery_lb']==4 # load 2 + relaxed shortest travel 2


def test_exact_and_alns_public_dispatch_and_help(capsys):
    from models.routing.service import solve_routing
    from cli.route import main
    from models.routing.catalog import MODEL_SPECS
    assert MODEL_SPECS[6].module_path('no_batch','exact')=='models.routing.model_6.exact'
    with pytest.raises(ValueError):MODEL_SPECS[6].module_path('multi_batch','alns')
    with pytest.raises(ValueError):MODEL_SPECS[5].module_path('no_batch','alns')
    result=solve_routing(model=6,method='ALNS',problem=tiny(pool=1),alns_time=.2,alns_iter=10)
    assert result['feasible']
    with pytest.raises(SystemExit) as exc:main(['--model','6','--help'])
    assert exc.value.code==0 and '--pool-caps' in capsys.readouterr().out


def test_milp_can_represent_a_full_seven_order_reuse_schedule():
    from tools.configuration.loader import load_routing_case
    from models.common.no_batch import load_instance
    from models.routing.model_6.alns.construction import initial_states
    from models.routing.model_6.exact.formulation import build_model
    from models.routing.model_6.exact.solution import extract_solution
    cfg,fleet=load_routing_case('config/experiments/examples/model6_demo.json')
    p=load_instance(cfg,fleet).with_options(pool_max=4)
    incumbent=next(v['result'] for s in initial_states(p) for v in [evaluate(p,s)] if v and v['result']['feasible'])
    model,context=build_model(p)
    for layer in ('e1','e2'):
        bases=sorted({r['base'] for r in context['routes'][layer]})
        for base in bases:
            rows=[r for r in context['routes'][layer] if r['base']==base]
            old=sorted([r for r in incumbent[layer+'_trips'] if layer=='e1' or r['satellite_id']==base],key=lambda r:r['loading_start'])
            labels={}
            for index,r in enumerate(rows):
                model.eq(r['used'],int(index<len(old)))
                if index>=len(old):continue
                trip=old[index]
                if trip['vehicle_id'] not in labels:labels[trip['vehicle_id']]=len(labels)
                selected=labels[trip['vehicle_id']]
                for v,x in enumerate(r['vehicle']):model.eq(x,int(v==selected))
                actions=[]
                for event in trip['events']:
                    ids=event['order_ids'] if layer=='e1' else [event['order_id']]
                    actions.extend((oid,event['kind']) for oid in ids)
                if layer=='e1':actions.sort(key=lambda a:(a[1]=='p',list(p.order_map).index(a[0])))
                for a,x in r['assignments'].items():model.eq(x,int(a in actions))
                for a,x in r['first'].items():model.eq(x,int(a==actions[0]))
                for a,x in r['last'].items():model.eq(x,int(a==actions[-1]))
                selected_arcs=set(zip(actions,actions[1:]))
                for a,x in r['arcs'].items():model.eq(x,int(a in selected_arcs))
    raw=model.solve('highs',15)
    assert raw['x'] is not None,raw
    converted=extract_solution(context,raw['x'])
    checked=audit_solution(p,converted)
    assert checked['valid'],checked['errors']


@pytest.mark.parametrize('cap,expected',[(1,.4+.2*2**.5),(2,.4+.1*2**.5)])
def test_two_satellite_exact_with_interleaved_first_echelon_service(cap,expected):
    p=tiny(pool=cap)
    nodes=dict(p.network.nodes)
    nodes['T']=Satellite('T',1,1,1)
    nodes['C2']=Customer('C2',2,1,0,1,(0,120),(0,120),'T')
    network=ProblemInstance(nodes,dict(E1_vehicle_count=1,vehicles_per_satellite=1,
                          E1_Qk=10,E1_Ck=10,E2_Qv=10,E2_Cv=10,container_weight=1,container_volume=1))
    p=NoBatchInstance(network,[p.orders[0],replace(p.orders[1],satellite='T',customer='C2')],p.operating_policy,p.options)
    r=solve_exact(p,time_limit=15)
    assert r['info']['proven_optimal'] and r['audit']['valid'],r
    assert r['info']['objective']==pytest.approx(expected)


def test_frontier_shares_feasible_plans_without_transferring_proofs():
    from cli.no_batch import share_frontier_incumbents
    p=tiny()
    inefficient=State(dict(e1=[Route('e1','D',0,[(o,k)]) for o in ('a','b') for k in ('d','p')],
                           e2=[Route('e2','S',0,[(o,k)]) for o in ('a','b') for k in ('d','p')]))
    a=evaluate(p,inefficient)['result'];b=hand_result(p.with_options(pool_max=1))
    points=[dict(pool_cap=cap,actual_pool=r['container_pool_size'],transport_cost=r['audit']['transport_cost'],
                 status='FEASIBLE',proven_optimal=False,proven_infeasible=False) for cap,r in ((2,a),(1,b))]
    rows=share_frontier_incumbents(p,points,[a,b])
    assert rows[0]['transport_cost']==pytest.approx(.6)
    assert rows[0]['status']=='FEASIBLE_SHARED' and rows[0]['source_pool_cap']==1
    assert not rows[0]['proven_optimal']


def test_model5_migration_preserves_actual_calendar_and_pool():
    from tests.routing.test_model5_calendar import calendar_fixture
    from models.routing.model_5.alns import solve_alns as solve_m5
    from models.common.no_batch.migration import migrate_cycle_result
    old=calendar_fixture(5)
    source=solve_m5(old,max_time=.5,max_iterations=100,seed=42)
    p,r=migrate_cycle_result(old,source)
    assert r['audit']['valid'] and r['container_pool_size']<=source['container_pool_size']
