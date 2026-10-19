"""Extra physical slots unlock splits without relaxing fleet or chronology."""
from copy import deepcopy
import time
import pytest

from models.common.cycle import MultiBatchInstance, Order
from models.common.cycle.audit import audit_solution
from models.common.cycle.state import empty_result
from models.common.domain.instance import ProblemInstance
from models.common.domain.node import Depot, Satellite, Customer
from models.routing.model_5.exact.primal.route_pool import expanded_trip_slots, reoptimize_route_pool
from tests.routing.test_multibatch_cycle import policy_config


def split_fixture(vehicles=1):
    cfg=policy_config();cfg.update(container_pool_size=3,e2_max_trips_per_vehicle=3)
    nodes={'D':Depot('D',0,0),'S':Satellite('S',0,0)}
    orders=[]
    for oid in ('A','B','C'):
        nodes[oid]=Customer(oid,1,0,0,0,(0,125),(0,125),'S')
        orders.append(Order(oid,oid,'S','b1',1,0,(0,25),(0,29),0,1))
    fleet=dict(E1_vehicle_count=1,vehicles_per_satellite=vehicles,E1_Qk=10,E1_Ck=10,
               E2_Qv=3,E2_Cv=3,container_weight=1,container_volume=1)
    distances={a:{} for a in nodes}
    # Explicit nonmetric raw arcs: returning to the satellite between two
    # customers is allowed only by completing a physical trip.
    for a in nodes:
        for b in nodes:
            distance=0 if a==b or {a,b}<={'D','S'} else 10 if a in 'ABC' and b in 'ABC' else 1
            distances[a][b]=distance
    network=ProblemInstance(nodes,fleet,distance_matrix=distances,travel_time_matrix=distances)
    p=MultiBatchInstance(network,orders,cfg)
    r=empty_result(p,'extra_slot_counterexample')
    r['e1_trips']=[dict(trip_id='e1',batch_id='b1',vehicle_id=0,departure=0,return_complete=30,
        events=[dict(kind='d',site_id='S',quantity=3,start=0),dict(kind='p',site_id='S',quantity=3,start=30)])]
    r['e2_trips']=[dict(trip_id='e2',satellite_id='S',vehicle_id=0,trip_index=0,departure=0,return_complete=25,
        events=[dict(order_id=o,kind=k,quantity=1,start=t) for o,k,t in
                [('A','d',1),('A','p',2),('B','d',12),('B','p',13),('C','d',23),('C','p',24)]])]
    a=audit_solution(p,r);assert a['valid'],a['errors'];r['info']['objective']=a['objective']
    return p,r


def test_additional_slots_unlock_known_full_optimum_and_never_report_pool_bound_as_global(tmp_path):
    pytest.importorskip('gurobipy')
    p,initial=split_fixture();saved=deepcopy(initial)
    proposals={'S':[dict(tasks=[(oid,'d'),(oid,'p')],cost=-100) for oid in 'ABC']}
    fixed=reoptimize_route_pool(p,initial,time_limit=2,max_columns=32,additional_columns=proposals)
    expanded=reoptimize_route_pool(p,initial,time_limit=2,max_columns=32,additional_columns=proposals,
                                   extra_slots_per_vehicle=2,diagnostics_dir=tmp_path)
    assert fixed['info']['objective']==22 and expanded['info']['objective']==6
    assert len(expanded['e2_trips'])==3 and initial==saved
    a=audit_solution(p,expanded);assert a['valid'] and a['periodic_replay']['valid']
    assert expanded['info']['best_bound'] is None and not expanded['info']['proven_optimal']
    from models.routing.model_5.exact.enhanced_solver import solve_exact
    exact=solve_exact(p,time_limit=3,initial_result=expanded)
    assert exact['info']['proven_optimal'] and exact['info']['objective']==6
    assert (tmp_path/'pass_0/slots.json').exists()


def test_slot_layout_interleaves_originals_uses_idle_vehicles_and_caps_physical_count():
    p,initial=split_fixture(vehicles=2);saved=deepcopy(initial)
    layout=expanded_trip_slots(p,initial,100)
    for vehicle in range(2):
        slots=[t for t in layout['e2_trips'] if t['vehicle_id']==vehicle]
        assert len(slots)==3 and [t['trip_index'] for t in slots]==[0,1,2]
    originals=[t for t in layout['e2_trips'] if not t.get('_pool_optional')]
    assert len(originals)==1 and originals[0]['events']==initial['e2_trips'][0]['events']
    assert originals[0]['trip_index']==1 and initial==saved
    assert len({t['trip_id'] for t in layout['e2_trips']})==6
    assert expanded_trip_slots(p,initial,0) is initial


def test_short_budget_preserves_audited_original_and_invalid_extra_count_is_rejected():
    p,initial=split_fixture();saved=deepcopy(initial)
    out=reoptimize_route_pool(p,initial,time_limit=1e-12,extra_slots_per_vehicle=2)
    assert out['e2_trips']==initial['e2_trips'] and initial==saved
    assert audit_solution(p,out)['valid'] and out['info']['route_pool_status']=='TIME_LIMIT_BUILD'
    for count in (-1,True,.5):
        with pytest.raises(ValueError,match='nonnegative integer'):
            reoptimize_route_pool(p,initial,time_limit=1,extra_slots_per_vehicle=count)


def test_hybrid_forwards_explicit_extra_slots_and_validates_their_count(monkeypatch):
    from models.routing.model_5.hybrid.solver import solve_hybrid
    import models.routing.service as service
    import models.routing.model_5.exact.primal.route_pool as pool
    p,initial=split_fixture();calls=[]
    monkeypatch.setattr(service,'solve_routing',lambda **kw:deepcopy(initial))
    def reopt(p,result,**kw):
        calls.append(kw['extra_slots_per_vehicle'])
        result['info']['runtime']=0
        return result
    monkeypatch.setattr(pool,'reoptimize_route_pool',reopt)
    out=solve_hybrid(p,time_limit=1,primal_time_limit=.1,cover_time_limit=0,
                     conditional_time_limit=0,extra_slots_per_vehicle=2)
    assert calls==[2,2] and audit_solution(p,out['result'])['valid']
    for count in (-1,True,.5):
        with pytest.raises(ValueError,match='nonnegative integer'):
            solve_hybrid(p,extra_slots_per_vehicle=count)
