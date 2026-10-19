"""Full slots need complementary exchanges; five-site proofs remain scoped."""
from copy import deepcopy

import pytest

from models.common.cycle import MultiBatchInstance, Order
from models.common.cycle.audit import audit_solution
from models.common.cycle.state import empty_result
from models.common.domain.instance import ProblemInstance
from models.common.domain.node import Customer, Depot, Satellite
from models.routing.model_5.exact.primal.route_pool import candidate_pools, reoptimize_route_pool
from models.routing.model_5.exact.bounds.inventory.e1_inventory import e1_inventory_certificate
from tests.routing.test_multibatch_cycle import policy_config


def full_slot_fixture():
    nodes={'D':Depot('D',0,0),'S':Satellite('S',0,0)}
    orders=[]
    for oid,x in [('A',1),('B',2),('C',-1),('E',-2)]:
        nodes[oid]=Customer(oid,x,0,0,0,(0,125),(0,125),'S')
        orders.append(Order(oid,oid,'S','b1',1,0,(0,25),(0,29),0,0))
    fleet=dict(E1_vehicle_count=1,vehicles_per_satellite=2,E1_Qk=10,E1_Ck=10,
        E2_Qv=2,E2_Cv=2,container_weight=1,container_volume=1)
    cfg=policy_config();cfg.update(container_pool_size=4,e2_max_trips_per_vehicle=1)
    p=MultiBatchInstance(ProblemInstance(nodes,fleet),orders,cfg)
    result=empty_result(p,'hand_full_slots')
    result['e1_trips']=[dict(trip_id='e1',vehicle_id=0,batch_id='b1',departure=0,
        return_complete=30,events=[dict(kind='d',site_id='S',quantity=4,start=0),
            dict(kind='p',site_id='S',quantity=4,start=30)])]
    for vehicle,ids in enumerate([('A','C'),('B','E')]):
        now,previous,events=0,'S',[]
        for oid,kind in [(i,'d') for i in ids]+[(i,'p') for i in ids]:
            now+=p.travel(previous,oid)
            events.append(dict(order_id=oid,kind=kind,quantity=1,start=now))
            previous=oid
        result['e2_trips'].append(dict(trip_id=f'e2_{vehicle}',satellite_id='S',vehicle_id=vehicle,
            trip_index=0,departure=0,return_complete=now+p.travel(previous,'S'),events=events))
    audit=audit_solution(p,result)
    assert audit['valid'] and audit['periodic_replay']['valid'],audit['errors']
    result['info']['objective']=audit['objective']
    return p,result


def five_site_fixture(count=5):
    nodes={'D':Depot('D',0,0)};orders=[]
    fleet=dict(E1_vehicle_count=count,vehicles_per_satellite=1,E1_Qk=1,E1_Ck=1,
        E2_Qv=1,E2_Cv=1,container_weight=1,container_volume=1)
    for i in range(count):
        site,oid=f'S{i}',f'C{i}'
        nodes[site]=Satellite(site,0,0)
        nodes[oid]=Customer(oid,0,0,0,0,(0,125),(0,125),site)
        orders.append(Order(oid,oid,site,'b1',1,0,(0,25),(0,29),0,0))
    cfg=policy_config();cfg.update(container_pool_size=count,e2_max_trips_per_vehicle=1)
    p=MultiBatchInstance(ProblemInstance(nodes,fleet),orders,cfg)
    r=empty_result(p,'hand_five_sites')
    for i,o in enumerate(orders):
        r['e1_trips'].append(dict(trip_id=f'e1_{i}',vehicle_id=i,batch_id='b1',departure=0,
            return_complete=30,events=[dict(kind='d',site_id=o.satellite,quantity=1,start=0),
                dict(kind='p',site_id=o.satellite,quantity=1,start=30)]))
        r['e2_trips'].append(dict(trip_id=f'e2_{i}',satellite_id=o.satellite,vehicle_id=0,
            trip_index=0,departure=0,return_complete=1,events=[dict(order_id=o.id,kind='d',quantity=1,start=0),
                dict(order_id=o.id,kind='p',quantity=1,start=1)]))
    a=audit_solution(p,r);assert a['valid'] and a['periodic_replay']['valid'],a['errors']
    r['info']['objective']=a['objective']
    return p,r


def test_complementary_whole_order_exchange_unlocks_full_capacity_slots():
    pytest.importorskip('gurobipy')
    p,initial=full_slot_fixture();untouched=deepcopy(initial)
    plain=candidate_pools(p,initial,exchanges=False,max_columns=256)
    swapped=candidate_pools(p,initial,exchanges=True,max_columns=256)
    tasksets=lambda rows:{frozenset(c['tasks']) for c in rows}
    wanted0=frozenset((o,k) for o in ('A','B') for k in ('d','p'))
    wanted1=frozenset((o,k) for o in ('C','E') for k in ('d','p'))
    assert wanted0 not in tasksets(plain[0]) and wanted1 not in tasksets(plain[1])
    assert wanted0 in tasksets(swapped[0]) and wanted1 in tasksets(swapped[1])
    # Two pickup tasks from different orders need a complementary exchange,
    # rather than a succession of infeasible additions to a full route.
    pickup_pair0=frozenset([('A','d'),('C','d'),('B','p'),('E','p')])
    pickup_pair1=frozenset([('B','d'),('E','d'),('A','p'),('C','p')])
    assert pickup_pair0 in tasksets(swapped[0]) and pickup_pair1 in tasksets(swapped[1])
    before=reoptimize_route_pool(p,initial,time_limit=5,exchanges=False)
    after=reoptimize_route_pool(p,initial,time_limit=5,exchanges=True,max_columns=256)
    assert initial==untouched
    assert after['info']['objective']==pytest.approx(8)
    assert after['info']['objective']<before['info']['objective']
    assert after['audit']['valid'] and after['audit']['periodic_replay']['valid']
    assert after['info']['best_bound'] is None and after['info']['mip_gap'] is None
    assert not after['info']['proven_optimal'] and not after['info']['route_pool_complete']
    from models.routing.model_5.exact.enhanced_solver import solve_exact
    full=solve_exact(p,time_limit=5,initial_result=after)
    assert full['info']['proven_optimal'] and full['info']['objective']==pytest.approx(8)


def test_explicit_five_site_patterns_produce_audited_ub_and_scoped_lb():
    pytest.importorskip('gurobipy')
    p,initial=five_site_fixture()
    cert=e1_inventory_certificate(p,time_limit=5,initial_result=initial,backend='patterns')
    assert cert['backend']=='patterns' and cert['enumeration_complete'] and cert['relaxation_optimal']
    assert cert['bound']<=audit_solution(p,initial)['distances']['e1']+1e-6
    assert not cert['proven_optimal'] and not cert['proven_infeasible']
    result=reoptimize_route_pool(p,initial,time_limit=5,e1_backend='patterns',exchanges=True)
    assert result['info']['route_pool_backend']=='patterns' and result['audit']['valid']
    assert result['info']['objective']==0 and result['info']['best_bound'] is None
    default=reoptimize_route_pool(p,initial,time_limit=5)
    assert default['info']['route_pool_backend']=='indexed'
    from models.routing.model_5.exact import route_cover_certificate
    combined=route_cover_certificate(p,initial_result=result,time_limit=5,e1_time_limit=2,e1_backend='patterns')
    assert combined['e1_certificate']['backend']=='patterns'
    assert combined['bound']<=result['info']['objective']+1e-6
    assert not combined['proven_optimal'] and not combined['proven_infeasible']


def test_explicit_pattern_primal_rejects_unrepresented_physical_turnaround():
    from dataclasses import replace
    p,initial=five_site_fixture()
    p.operating_policy=replace(p.operating_policy,e1_turnaround_time=1)
    initial['operating_policy']=empty_result(p,'updated_policy')['operating_policy']
    with pytest.raises(ValueError,match='zero E1 turnaround'):
        reoptimize_route_pool(p,initial,time_limit=1,e1_backend='patterns')


def test_six_active_sites_keep_the_bounded_enumeration_gate():
    p,initial=five_site_fixture(6)
    with pytest.raises(ValueError,match='five active satellites'):
        e1_inventory_certificate(p,time_limit=1,backend='patterns')
    with pytest.raises(ValueError,match='five active satellites'):
        reoptimize_route_pool(p,initial,time_limit=1,e1_backend='patterns')
