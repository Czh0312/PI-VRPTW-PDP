"""Replay all enumerated columns and independently solve exact set partitioning."""
from pathlib import Path
import importlib.util,math
import pytest
spec=importlib.util.spec_from_file_location('route_bound_test',Path(__file__).with_name('route_lower_bound.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)

def data():
    cfg,fleet=r.t.d.load_routing_case(r.t.BASE/'cases/S01_m8_k1_4.json',use_pool_config=False)
    p=r.t.d.load_instance(cfg,fleet)
    return p,r.t.read(r.OUT/'route_lower_bound.json'),r.t.read(r.OUT/'route_bound_patterns.json')

def test_every_enumerated_route_and_optimal_cover_replay():
    p,bound,patterns=data();site=next(iter(p.satellites));f=p.fleet;policy=p.operating_policy
    ids=bound['orders'];index={oid:i for i,oid in enumerate(ids)}
    cert=r.t.read(r.OUT/'reduction_certificate.json');first=set(cert['forced_first_batch_orders'])
    for pattern in patterns:
        tasks=pattern['tasks'];delivered={oid for oid,kind,_ in tasks if kind=='d'}
        assert not (delivered&first and delivered-first)
        w=sum(p.order_map[oid].goods_weight+f.container_weight for oid in delivered);v=len(delivered)
        assert w<=f.e2_weight_cap and v<=f.e2_volume_cap
        clock=policy.cycle_start;at=site;length=0;mask=0;dt={}
        for oid,kind,start in tasks:
            o=p.order_map[oid];bit=1<<(index[oid]+(0 if kind=='d' else len(ids)))
            assert not mask&bit;mask|=bit
            assert start>=clock+p.travel(at,o.customer)
            lo,hi=o.delivery_window if kind=='d' else o.pickup_window
            assert lo<=start<=hi
            if kind=='d':dt[oid]=start;w-=o.goods_weight+f.container_weight;v-=1
            else:
                earliest=o.delivery_window[0]+o.service_time+o.min_use_time
                if oid in delivered:
                    assert oid in dt;earliest=max(earliest,dt[oid]+o.service_time+o.min_use_time)
                assert start>=earliest;w+=f.container_weight;v+=1
            assert -1e-7<=w<=f.e2_weight_cap+1e-7 and 0<=v<=f.e2_volume_cap
            length+=p.distance(at,o.customer);at=o.customer;clock=start+o.service_time
        assert clock+p.travel(at,site)+policy.e2_return_unload_time<=policy.cycle_end
        assert mask==pattern['mask']
        assert length+p.distance(at,site)==pytest.approx(pattern['distance'])
    cover=bound['cover'];combined=0
    for column in cover:assert not combined&column['mask'];combined|=column['mask']
    assert combined==(1<<(2*len(ids)))-1
    assert sum(column['distance'] for column in cover)==pytest.approx(bound['E2_distance_lower_bound'])

def test_independent_gurobi_partition_optimum_matches_dynamic_program():
    p,bound,patterns=data();gp=r.t.gp;m=gp.Model('independent_E2_relaxation_set_partition')
    m.Params.OutputFlag=0;m.Params.Threads=1;m.Params.MIPGap=0;m.Params.MIPGapAbs=0;m.Params.TimeLimit=30
    x=m.addVars(len(patterns),vtype=gp.GRB.BINARY)
    for task in range(14):m.addConstr(gp.quicksum(x[j] for j,column in enumerate(patterns) if column['mask']>>task&1)==1)
    m.setObjective(gp.quicksum(column['distance']*x[j] for j,column in enumerate(patterns)),gp.GRB.MINIMIZE)
    m.optimize();assert m.Status==gp.GRB.OPTIMAL
    assert m.ObjVal==pytest.approx(bound['E2_distance_lower_bound'],abs=1e-7)
    r.t.save('independent_partition_verification.json',dict(status='OPTIMAL',variables=m.NumVars,constraints=m.NumConstrs,
        runtime=m.Runtime,objective=m.ObjVal,best_bound=m.ObjBound,gap=m.MIPGap,DP_objective=bound['E2_distance_lower_bound']))
    m.dispose()
