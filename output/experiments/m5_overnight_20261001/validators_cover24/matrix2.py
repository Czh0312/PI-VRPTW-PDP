"""Independent pool equations; no production pool builder or extractor."""
from pathlib import Path
from collections import Counter,defaultdict
import sys,json,math,time,hashlib
import gurobipy as gp
HERE=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(HERE/'source_snapshot_cover24'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_e1_routes import build_e1_routes
started=time.perf_counter()
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')));p=problem.operating_policy
out=HERE/'cover24';columns=json.loads((out/'models/columns.json').read_text());slots=json.loads((out/'models/slots.json').read_text())
assert columns==json.loads((out/'preflight_columns.json').read_text())
assert slots==json.loads((out/'preflight_slots.json').read_text())
old=json.loads((HERE/'pool_retention23/models/pass_0/columns.json').read_text())
assert len(columns)==len(slots)==len(old)
for a,b in zip(old,columns):assert b[:len(a)]==a
base,ctx=build_e1_routes(problem);model=gp.read(str(out/'models/route_pool.mps.gz'))

def terms(items):
    d=defaultdict(float)
    for name,value in items:d[name]+=value
    return tuple(sorted((k,v) for k,v in d.items() if v))
def expression(e):return terms((e.getVar(i).VarName,e.getCoeff(i)) for i in range(e.size()))
def row(sense,rhs,items):return sense,float(rhs),terms(items)
def observed_row(m,r):return r.Sense,float(r.RHS),expression(m.getRow(r))
def indicator(m,r):
    assert r.GenConstrType==gp.GRB.GENCONSTR_INDICATOR
    v,value,e,sense,rhs=m.getGenConstrIndicator(r)
    return v.VarName,value,sense,float(rhs),expression(e)

try:
    n,m,g=base.NumVars,base.NumConstrs,base.NumGenConstrs
    original_vars=base.getVars();new_vars=model.getVars()
    for attr in ('VarName','LB','UB','VType','Obj'):
        assert base.getAttr(attr,original_vars)==model.getAttr(attr,new_vars[:n])
    assert base.ObjCon==model.ObjCon and base.ModelSense==model.ModelSense==gp.GRB.MINIMIZE
    for a,b in zip(base.getConstrs(),model.getConstrs()[:m]):
        assert a.ConstrName==b.ConstrName and observed_row(base,a)==observed_row(model,b)
    assert [indicator(base,r) for r in base.getGenConstrs()]==[indicator(model,r) for r in model.getGenConstrs()[:g]]
    expected_rows=[];expected_indicators=[];expected_vars={}
    limit=max(1,2*len(problem.orders)+2*len(slots))
    def var(name,kind='C',ub=gp.GRB.INFINITY,obj=0):expected_vars[name]=(0,ub,kind,obj)
    def linear(sense,rhs,*items):expected_rows.append(row(sense,rhs,items))
    def linked(name,choice_names):
        var(name,'B',1);linear('=',0,(name,1),*((v,-1) for v in choice_names));return name
    def conditional(flag,sense,rhs,*items):
        expected_indicators.append((flag,1,sense,float(rhs),terms(items)))
    ranks={key:f'pool_task_rank_{key[0]}_{key[1]}' for key in ctx['order_times']}
    for name in ranks.values():var(name,ub=limit)
    for order in problem.orders:linear('>',1,(ranks[order.id,'p'],1),(ranks[order.id,'d'],-1))
    coverage=defaultdict(list);internal=defaultdict(list);vehicles=defaultdict(list)
    checked_columns=0;added_columns=0
    for i,(slot,rows) in enumerate(zip(slots,columns)):
        assert 0<=slot['vehicle_id']<problem.fleet.e2_per_sat_count
        vehicles[slot['satellite_id'],slot['vehicle_id']].append(i)
        used,start,end=f'pool_used_{i}',f'pool_start_{i}',f'pool_end_{i}'
        first_rank,last_rank=f'pool_start_rank_{i}',f'pool_end_rank_{i}'
        var(used,'B',1);var(start,'I',p.cycle_end);var(end,'I',p.cycle_end)
        var(first_rank);var(last_rank)
        z=[f'pool_choice_{i}[{j}]' for j in range(len(rows))]
        deliveries=[];first=defaultdict(list);last=defaultdict(list);pickups=defaultdict(list);batches=defaultdict(list)
        for j,column in enumerate(rows):
            tasks=[tuple(k) for k in column['tasks']]
            assert tasks and len(set(tasks))==len(tasks)
            assert all(k in ranks and problem.order_map[k[0]].satellite==slot['satellite_id'] for k in tasks)
            places=[slot['satellite_id']]+[problem.order_map[k[0]].customer for k in tasks]+[slot['satellite_id']]
            cost=math.fsum(problem.distance(a,b) for a,b in zip(places,places[1:]))
            assert abs(cost-column['cost'])<1e-9
            var(z[j],'B',1,column['cost']);checked_columns+=1;added_columns+=int(j>=len(old[i]))
            weight=sum(problem.task(oid,kind)[1] for oid,kind in tasks if kind=='d')
            volume=sum(problem.task(oid,kind)[2] for oid,kind in tasks if kind=='d')
            for key in [None]+tasks:
                if key is not None:
                    _,w,v=problem.task(*key);sign=-1 if key[1]=='d' else 1
                    weight+=sign*w;volume+=sign*v
                assert -1e-9<=weight<=problem.fleet.e2_weight_cap+1e-9
                assert -1e-9<=volume<=problem.fleet.e2_volume_cap+1e-9
            bids={problem.order_map[oid].batch for oid,kind in tasks if kind=='d'};assert len(bids)<=1
            if bids:deliveries.append(z[j])
            first[tasks[0]].append(z[j]);last[tasks[-1]].append(z[j])
            for key in tasks:
                coverage[key].append(z[j])
                if key[1]=='p':pickups[key[0]].append(z[j])
            for bid in bids:batches[bid].append(z[j])
            for a,b in zip(tasks,tasks[1:]):internal[a,b].append(z[j])
        linear('=',0,*[(v,1) for v in z],(used,-1))
        linear('>',0,(start,1),(used,-p.cycle_start),*((v,-p.e2_dispatch_prep_time) for v in deliveries))
        linear('<',0,(start,1),(used,-p.cycle_end))
        linear('<',0,(end,1),(used,-(p.cycle_end-p.e2_turnaround_time)))
        linear('>',0,(first_rank,1),(used,-1));linear('<',0,(first_rank,1),(used,-limit))
        linear('<',0,(last_rank,1),(used,-limit))
        for key,names in first.items():
            flag=linked(f'pool_first_{i}_{key[0]}_{key[1]}',names)
            conditional(flag,'>',problem.travel(slot['satellite_id'],problem.order_map[key[0]].customer),(ctx['order_times'][key].VarName,1),(start,-1))
            conditional(flag,'>',1,(ranks[key],1),(first_rank,-1))
        for key,names in last.items():
            flag=linked(f'pool_last_{i}_{key[0]}_{key[1]}',names);order=problem.order_map[key[0]]
            conditional(flag,'>',order.service_time+problem.travel(order.customer,slot['satellite_id'])+p.e2_return_unload_time,(end,1),(ctx['order_times'][key].VarName,-1))
            conditional(flag,'>',1,(last_rank,1),(ranks[key],-1))
        for oid,names in pickups.items():
            flag=linked(f'pool_pickup_{i}_{oid}',names)
            conditional(flag,'=',0,(ctx['returns'][oid].VarName,1),(end,-1))
        for bid,names in batches.items():
            flag=linked(f'pool_batch_{i}_{bid}',names)
            conditional(flag,'>',p.e2_dispatch_prep_time,(start,1),(ctx['supplies'][slot['satellite_id'],bid].VarName,-1))
    assert set(coverage)==set(ctx['order_times'])
    for key,names in coverage.items():linear('=',1,*[(v,1) for v in names])
    for j,((a,b),names) in enumerate(internal.items()):
        flag=linked(f'pool_link_{j}',names);left,right=problem.order_map[a[0]],problem.order_map[b[0]]
        conditional(flag,'>',left.service_time+problem.travel(left.customer,right.customer),(ctx['order_times'][b].VarName,1),(ctx['order_times'][a].VarName,-1))
        conditional(flag,'>',1,(ranks[b],1),(ranks[a],-1))
    for indices in vehicles.values():
        indices.sort(key=lambda i:slots[i]['trip_index'])
        assert len(indices)<=p.e2_max_trips_per_vehicle
        for pos,i in enumerate(indices):
            for j in indices[pos+1:]:
                deliveries=[f'pool_choice_{j}[{k}]' for k,c in enumerate(columns[j]) if any(kind=='d' for _,kind in c['tasks'])]
                big=p.cycle_end+p.e2_turnaround_time
                linear('>',p.e2_turnaround_time-2*big,(f'pool_start_{j}',1),(f'pool_end_{i}',-1),(f'pool_used_{i}',-big),(f'pool_used_{j}',-big),*((v,-p.e2_dispatch_prep_time) for v in deliveries))
                big=limit+1
                linear('>',1-2*big,(f'pool_start_rank_{j}',1),(f'pool_end_rank_{i}',-1),(f'pool_used_{i}',-big),(f'pool_used_{j}',-big))
    observed_vars={v.VarName:(v.LB,v.UB,v.VType,v.Obj) for v in new_vars[n:]}
    assert observed_vars==expected_vars
    actual=Counter(observed_row(model,r) for r in model.getConstrs()[m:])
    expected=Counter(expected_rows);assert actual==expected,{'unexpected':list((actual-expected).items())[:2],'missing':list((expected-actual).items())[:2]}
    actual_indicators=Counter(indicator(model,r) for r in model.getGenConstrs()[g:])
    expected=Counter(expected_indicators);assert actual_indicators==expected,{'unexpected':list((actual_indicators-expected).items())[:2],'missing':list((expected-actual_indicators).items())[:2]}
    proof=dict(original_E1_variables=n,original_E1_rows=m,original_E1_indicators=g,
        original_E1_attributes_and_matrix_unchanged=True,all_pool_variables_checked=len(expected_vars),
        all_pool_linear_rows_checked=len(expected_rows),all_pool_indicators_checked=len(expected_indicators),
        original_32_candidate_prefix_preserved=True,independent_columns_cost_and_capacity_checked=checked_columns,
        added_columns=added_columns,physical_vehicle_holes_preparation_turnaround_and_rank_links_checked=True,
        all_order_times_supply_and_actual_trip_returns_linked=True,full_proven_optimal=False,
        runtime=time.perf_counter()-started)
finally:base.dispose();model.dispose()
(out/'matrix_validation.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof,indent=2))
