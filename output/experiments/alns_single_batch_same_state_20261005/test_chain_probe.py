from types import SimpleNamespace as N
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from chain_probe import chain, state_from_key

def problem():
    orders={i:N(delivery_window=(0,500),pickup_window=(0,500),service_time=10)
            for i in 'abcdef'}
    return N(order_map=orders,operating_policy=N(cycle_end=1000,e2_turnaround_time=10,batches=[N(cutoff=450)]))

def route(vehicle,tasks):
    return dict(satellite='S',vehicle=vehicle,tasks=tasks)

def trip(tasks,starts=None,returned=800):
    return dict(return_complete=returned,events=[dict(order_id=o,kind=k,start=t)
                for (o,k),t in zip(tasks,starts or [100]*len(tasks))])

def test_dependency_closure_includes_delivery_and_previous_vehicle_routes():
    routes=[route(0,[('a','d')]),route(1,[('b','d')]),route(0,[('c','d')]),
            route(1,[('a','p'),('c','p')]),route(2,[('d','d')])]
    decoded=dict(e2_trips=[trip(r['tasks'],[501,501] if i==3 else None) for i,r in enumerate(routes)])
    selected,late=chain(problem(),dict(e2=routes),decoded)
    assert late==[3] and selected==[0,1,2,3]

def test_two_hops_do_not_expand_whole_vehicle_chain():
    routes=[route(0,[(o,'d')]) for o in 'abcd']
    decoded=dict(e2_trips=[trip(r['tasks'],[441] if i==3 else None) for i,r in enumerate(routes)])
    assert chain(problem(),dict(e2=routes),decoded)==([1,2,3],[3])

def test_empty_route_mapping_and_terminal_boundary():
    routes=[route(0,[]),route(0,[('a','d')]),route(1,[('b','d')])]
    decoded=dict(e2_trips=[trip(routes[1]['tasks'],returned=990),trip(routes[2]['tasks'],returned=991)])
    assert chain(problem(),dict(e2=routes),decoded)==([2],[2])

def test_cached_state_reconstruction_retains_hints_and_routes():
    key=((('b1',(('S',),())),),(('b1',('S',)),),(('b1',(('S',1),)),),
         (('S',0,(('a','d'),('a','p')),123,456),('S',1,(),None,None)))
    state=state_from_key(key)
    assert state['deliveries']=={'b1':[['S'],[]]}
    assert state['pickup_order']=={'b1':['S']} and state['pickup_vehicle']=={'b1':{'S':1}}
    assert state['e2'][0]==dict(satellite='S',vehicle=0,tasks=[('a','d'),('a','p')],depart_after=123,return_after=456)
    assert state['e2'][1]==dict(satellite='S',vehicle=1,tasks=[])
