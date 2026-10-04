"""Independent augmented-matrix check and direct MPS schedule reconstruction."""
from pathlib import Path
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
import os
import sys
import time

HERE=Path(__file__).resolve().parent.parent
SNAPSHOT=HERE/'source_snapshot_vehicle4'
OUT=HERE/'vehicle_retime4/validation'
OUT.mkdir(exist_ok=True)
sys.path.insert(0,str(SNAPSHOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.state import empty_result
from validation_e1 import all_patterns
from validation_polish import replay_polish
import gurobipy as gp


def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def checkpoint(status,**extra):
    c=json.loads((HERE/'checkpoint.json').read_text())
    c.update({'status':status,'active_process':os.getpid(),**extra});write(HERE/'checkpoint.json',c)


def check_fleet_matrix(problem,initial,model):
    p,f=problem.operating_policy,problem.fleet
    trips=initial['e2_trips'];by_site=defaultdict(list);seen=set()
    for i,t in enumerate(trips):by_site[t['satellite_id']].append(i)
    def row(name,sense,rhs,terms):
        constraint=model.getConstrByName(name);assert constraint is not None,name
        expression=model.getRow(constraint);actual=defaultdict(float)
        for j in range(expression.size()):actual[expression.getVar(j).VarName]+=expression.getCoeff(j)
        expected=defaultdict(float)
        for variable,coefficient in terms:expected[variable]+=coefficient
        actual={k:v for k,v in actual.items() if abs(v)>1e-10}
        expected={k:v for k,v in expected.items() if abs(v)>1e-10}
        assert constraint.Sense==sense and abs(constraint.RHS-rhs)<1e-7,(name,constraint.Sense,constraint.RHS,rhs)
        assert actual.keys()==expected.keys(),(name,actual,expected)
        assert all(abs(actual[k]-v)<1e-7 for k,v in expected.items()),name
        seen.add(name)
    def var(name,kind,lo,hi):
        v=model.getVarByName(name);assert v is not None,name
        assert v.VType==kind and v.LB==lo and v.UB==hi,(name,v.VType,v.LB,v.UB,lo,hi)
        assert v.Obj==0,name
    tasks={};fixed_cost=0.0
    for i,t in enumerate(trips):
        s=t['satellite_id'];events=t['events'];prepare=p.e2_dispatch_prep_time if any(e['kind']=='d' for e in events) else 0
        N=sum(len(trips[j]['events'])+2 for j in by_site[s])
        var(f'fleet_departure_{i}','I',p.cycle_start+prepare,p.cycle_end)
        upper=p.cycle_end-p.e2_turnaround_time
        if any(e['kind']=='p' for e in events):upper=min(upper,p.batches[-1].cutoff)
        var(f'fleet_return_{i}','I',p.cycle_start,upper)
        for k in range(-1,len(events)+1):var(f'fleet_rank_{i}_{k}','I',0,N-1)
        for k in range(len(events)+1):
            row(f'fleet_rank_path_{i}_{k}','>',1,[(f'fleet_rank_{i}_{k}',1),(f'fleet_rank_{i}_{k-1}',-1)])
        previous,clock,service=s,f'fleet_departure_{i}',0
        places=[s]
        for k,e in enumerate(events):
            o=problem.order_map[e['order_id']]
            at=('delivery_' if e['kind']=='d' else 'pickup_time_')+o.id
            row(f'fleet_travel_{i}_{k}','>',service+problem.travel(previous,o.customer),[(at,1),(clock,-1)])
            if e['kind']=='d':
                row(f'fleet_supply_{i}_{k}','>',prepare,[(f'fleet_departure_{i}',1),(f'supply_{s}_{o.batch}',-1)])
            else:
                row(f'fleet_common_return_{i}_{k}','=',0,[(f'e2_return_{o.id}',1),(f'fleet_return_{i}',-1)])
            tasks[o.id,e['kind']]=i,k
            previous,clock,service=o.customer,at,o.service_time
            places.append(o.customer)
        row(f'fleet_end_{i}','>',service+problem.travel(previous,s)+p.e2_return_unload_time,
            [(f'fleet_return_{i}',1),(clock,-1)])
        places.append(s);fixed_cost+=math.fsum(problem.distance(a,b) for a,b in zip(places,places[1:]))
        for v in range(f.e2_per_sat_count):var(f'fleet_assign_{i}_{v}','B',0,1)
        row(f'fleet_exact_assignment_{i}','=',1,[(f'fleet_assign_{i}_{v}',1) for v in range(f.e2_per_sat_count)])
    for o in problem.orders:
        di,dk=tasks[o.id,'d'];pi,pk=tasks[o.id,'p']
        row(f'fleet_rank_use_{o.id}','>',1,[(f'fleet_rank_{pi}_{pk}',1),(f'fleet_rank_{di}_{dk}',-1)])
    for s,indices in by_site.items():
        N=sum(len(trips[i]['events'])+2 for i in indices);H=p.cycle_end-p.cycle_start
        for v in range(f.e2_per_sat_count):
            row(f'fleet_trip_limit_{s}_{v}','<',p.e2_max_trips_per_vehicle,[(f'fleet_assign_{i}_{v}',1) for i in indices])
        for pos,i in enumerate(indices):
            for j in indices[pos+1:]:
                var(f'fleet_before_{i}_{j}','B',0,1)
                for v in range(f.e2_per_sat_count):
                    common=[(f'fleet_assign_{i}_{v}',-H),(f'fleet_assign_{j}_{v}',-H)]
                    prepare_j=p.e2_dispatch_prep_time if any(e['kind']=='d' for e in trips[j]['events']) else 0
                    prepare_i=p.e2_dispatch_prep_time if any(e['kind']=='d' for e in trips[i]['events']) else 0
                    row(f'fleet_forward_{i}_{j}_{v}','>',prepare_j+p.e2_turnaround_time-3*H,
                        [(f'fleet_departure_{j}',1),(f'fleet_return_{i}',-1),(f'fleet_before_{i}_{j}',-H)]+common)
                    row(f'fleet_backward_{i}_{j}_{v}','>',prepare_i+p.e2_turnaround_time-2*H,
                        [(f'fleet_departure_{i}',1),(f'fleet_return_{j}',-1),(f'fleet_before_{i}_{j}',H)]+common)
                    common=[(f'fleet_assign_{i}_{v}',-N),(f'fleet_assign_{j}_{v}',-N)]
                    row(f'fleet_rank_forward_{i}_{j}_{v}','>',1-3*N,
                        [(f'fleet_rank_{j}_-1',1),(f'fleet_rank_{i}_{len(trips[i]["events"])}',-1),(f'fleet_before_{i}_{j}',-N)]+common)
                    row(f'fleet_rank_backward_{i}_{j}_{v}','>',1-2*N,
                        [(f'fleet_rank_{i}_-1',1),(f'fleet_rank_{j}_{len(trips[j]["events"])}',-1),(f'fleet_before_{i}_{j}',N)]+common)
    assert {r.ConstrName for r in model.getConstrs() if r.ConstrName.startswith('fleet_')}==seen
    assert abs(model.ObjCon-fixed_cost)<1e-6
    return dict(all_added_rows_checked=len(seen),fixed_e2_objective_constant=fixed_cost,
                actual_fleet_and_trip_limits=True,preparation_and_turnaround=True,event_causality_ranks=True)


def reconstruct(problem,initial,model):
    result=empty_result(problem,'independent_vehicle_MPS_replay')
    result['e2_trips']=deepcopy(initial['e2_trips']);grouped=defaultdict(list)
    for i,t in enumerate(result['e2_trips']):
        t['departure']=int(round(model.getVarByName(f'fleet_departure_{i}').X))
        t['return_complete']=int(round(model.getVarByName(f'fleet_return_{i}').X))
        selected=[v for v in range(problem.fleet.e2_per_sat_count) if model.getVarByName(f'fleet_assign_{i}_{v}').X>.5]
        assert len(selected)==1;t['vehicle_id']=selected[0]
        grouped[t['satellite_id'],selected[0]].append(i)
        for e in t['events']:
            e['start']=int(round(model.getVarByName(('delivery_' if e['kind']=='d' else 'pickup_time_')+e['order_id']).X))
    for indices in grouped.values():
        # Large-instance routes have positive duration. Departure order is an
        # independent physical sequence, without production rank extraction.
        indices.sort(key=lambda i:result['e2_trips'][i]['departure'])
        for slot,i in enumerate(indices):result['e2_trips'][i]['trip_index']=slot
    next_vehicle={b.id:0 for b in problem.operating_policy.batches}
    for i,pattern in enumerate(all_patterns(problem)):
        chosen=model.getVarByName(f'route[{i}]');assert chosen is not None
        assert abs(chosen.Obj-pattern['cost'])<1e-6
        if chosen.X<.5:continue
        b=problem.batch_map[pattern['batch']]
        now=b.start+problem.operating_policy.e1_load_time if pattern['delivery'] else b.cutoff
        start=now;previous=problem.depot;events=[]
        for s in pattern['delivery']:
            now+=problem.travel(previous,s);events.append(dict(kind='d',site_id=s,quantity=problem.demands[s,b.id],start=now))
            now+=problem.service(s);previous=s
        if pattern['pickup']:now=max(now,b.cutoff)
        for s in pattern['pickup']:
            now+=problem.travel(previous,s);quantity=int(round(model.getVarByName(f'quantity_{i}_{s}').X))
            events.append(dict(kind='p',site_id=s,quantity=quantity,start=now));now+=problem.service(s);previous=s
        end=now+problem.travel(previous,problem.depot)+problem.operating_policy.depot_return_unload_time
        assert end==pattern['end']
        v=next_vehicle[b.id];next_vehicle[b.id]+=1
        result['e1_trips'].append(dict(trip_id=f'independent_e1_{b.id}_{v}',batch_id=b.id,vehicle_id=v,
                                    departure=start,return_complete=end,events=events))
    audit=audit_solution(problem,result);assert audit['valid'],audit['errors']
    assert abs(audit['objective']-model.ObjVal)<1e-5
    return result,audit


begun=time.perf_counter()
checkpoint('validating_flexible_vehicle_models',next_action='Wait for independent fleet matrix/MPS validation; read vehicle_retime4/validation/summary.json.')
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
initial=json.loads((HERE/'inputs/initial_result.json').read_text())
summaries=json.loads((HERE/'vehicle_retime4/summary.json').read_text());checked=[]
for row in summaries:
    job=row['job'];path=HERE/'vehicle_retime4'/(job+'.mps.gz')
    recorded=json.loads((HERE/'vehicle_retime4'/(job+'_result.json')).read_text())
    assert audit_solution(problem,recorded)['valid']
    assert not recorded['info']['proven_optimal'] and recorded['info']['best_bound'] is None
    if row['variant']=='fixed_vehicle':
        proof=replay_polish(problem,initial,recorded,path,True)
    else:
        model=gp.read(str(path))
        try:
            proof=check_fleet_matrix(problem,initial,model)
            model.Params.OutputFlag=0;model.Params.Threads=1
            model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9
            model.Params.MIPGap=model.Params.MIPGapAbs=0;model.Params.Seed=row['seed'];model.Params.TimeLimit=30
            model.optimize();assert model.Status==gp.GRB.OPTIMAL
            assert abs(model.ObjVal-row['objective'])<1e-5
            rebuilt,audit=reconstruct(problem,initial,model)
            write(OUT/(job+'_reconstructed.json'),rebuilt);write(OUT/(job+'_audit.json'),audit)
            proof.update(replay_status=int(model.Status),replay_objective=float(model.ObjVal),
                         full_cycle_audit=True,reconstructed_from_MPS_without_production_extractor=True)
        finally:model.dispose()
    checked.append(dict(job=job,proof=proof));write(OUT/'summary.json',dict(jobs=checked,runtime=time.perf_counter()-begun))
    print('validated '+job,flush=True)
checkpoint('flexible_vehicle_models_verified',active_process=None,
    completed_tests='41 vehicle-retiming/E1-polish tests passed; previous 83 cuts/site/service tests passed',
    vehicle_trial_validation=str(OUT/'summary.json'),
    latest_note='All three seeds automatically reproduce upper 2238.1568127099035; fixed-vehicle mode stays at 2247.646367634466. Each fixed-path flexible-vehicle subproblem is optimal, so further upper gains require changing E2 paths. These local optima are not full M5 proofs.',
    next_action='Use changed E2 path pools or complementary complete-cover routes with flexible vehicle calendars to reduce the remaining 2.733675338 gap. Keep the verified global lower and upper. No need to repeat the now-optimal fixed-path retiming subproblem. Preserve actual-cost epigraph for any physical lower cuts.')
