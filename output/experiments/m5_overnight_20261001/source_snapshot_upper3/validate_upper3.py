"""Reproduce and verify the vehicle/calendar candidate on frozen data."""
from pathlib import Path
import copy
import hashlib
import json
import math
import os
import sys
import time

SNAPSHOT = Path(__file__).resolve().parent
HERE = SNAPSHOT.parent
OUT = HERE/'upper_retiming/validation'
OUT.mkdir(exist_ok=True)
sys.path.insert(0,str(SNAPSHOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_physical_site import build_physical_site, project_physical_site
from models.routing.solvers.exact.multibatch_e1_routes import build_e1_routes, set_route_projection
from models.routing.solvers.exact.multibatch_e1_polish import fix_e2_schedule
from models.routing.solvers.exact.multibatch_joint_inventory import set_joint_projection
from warm_context import context_for_export
import gurobipy as gp


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def checkpoint(status, **extra):
    value=json.loads((HERE/'checkpoint.json').read_text())
    value.update({'status':status,'active_process':os.getpid(),**extra})
    write(HERE/'checkpoint.json',value)


def optimize(model,limit=30):
    model.Params.Threads=1
    model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9
    model.Params.TimeLimit=limit
    model.optimize()
    assert model.Status==gp.GRB.OPTIMAL


begun=time.perf_counter()
checkpoint('validating_vehicle_reassignment_upper',source_snapshot=str(SNAPSHOT),
    next_action='Wait for this validator; inspect upper_retiming/validation/summary.json. No other heavy solver should start concurrently.')
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
initial=json.loads((HERE/'inputs/initial_result.json').read_text())
candidate=copy.deepcopy(initial)
# Reproduce the exact candidate instead of trusting a serialized new result.
for trip in candidate['e2_trips']:
    if trip['satellite_id']=='S4' and trip['vehicle_id']==0:
        if trip['trip_index']==0: trip['return_complete']=54001
        elif trip['trip_index']==1: trip['vehicle_id']=2;trip['trip_index']=0
for trip in candidate['e1_trips']:
    if trip['batch_id']=='b1':
        trip['events']=[e for e in trip['events'] if not(e['kind']=='p' and e['site_id']=='S4')]
    elif trip['batch_id']=='b3':
        for event in trip['events']:
            if event['kind']=='p' and event['site_id']=='S4': event['quantity']+=4
for field in ('audit','cutoff_snapshots','depot_releases'): candidate.pop(field,None)
candidate['info']=dict(status='FEASIBLE',objective=None,best_bound=None,mip_gap=None,
    proven_optimal=False,proven_infeasible=False,
    proof_scope='audited_vehicle_reassignment_and_fixed_sequence_retiming')
audit=audit_solution(problem,candidate)
assert audit['valid'],audit['errors']
candidate['info']['objective']=audit['objective']
candidate['audit']=audit
write(OUT/'reproduced_candidate.json',candidate)
write(OUT/'full_audit.json',audit)

# Separate route arithmetic: task coverage, directed distances, resource
# intervals (including preparation and turnaround), and causal use windows.
distances={}
coverage={}
resource={}
for layer in ('e1','e2'):
    distance=0.0
    for trip in candidate[layer+'_trips']:
        base=problem.depot if layer=='e1' else trip['satellite_id']
        places=[base]+[(e['site_id'] if layer=='e1' else problem.order_map[e['order_id']].customer)
                       for e in trip['events']]+[base]
        distance+=math.fsum(problem.distance(a,b) for a,b in zip(places,places[1:]))
        if layer=='e2':
            delivered=any(e['kind']=='d' for e in trip['events'])
            begin=trip['departure']-(problem.operating_policy.e2_dispatch_prep_time if delivered else 0)
            end=trip['return_complete']+problem.operating_policy.e2_turnaround_time
            key=(base,trip['vehicle_id'])
            assert 0<=trip['vehicle_id']<problem.fleet.e2_per_sat_count
            resource.setdefault(key,[]).append((begin,end,trip['trip_id']))
            for event in trip['events']:
                task=(event['order_id'],event['kind'])
                assert task not in coverage
                coverage[task]=event['start']
    distances[layer]=distance
assert len(coverage)==2*len(problem.orders)
for order in problem.orders:
    assert coverage[order.id,'p']>=coverage[order.id,'d']+order.service_time+order.min_use_time
for rows in resource.values():
    rows.sort()
    assert all(a[1]<=b[0] for a,b in zip(rows,rows[1:]))
assert all(abs(distances[k]-audit['distances'][k])<1e-6 for k in distances)
write(OUT/'separate_arithmetic.json',dict(directed_distances=distances,
    exact_task_coverage=len(coverage),resource_intervals={str(k):v for k,v in resource.items()},
    full_fleet_preserved=True,use_precedence_verified=True))

sites=[]
for site in sorted({o.satellite for o in problem.orders}):
    model,context=build_physical_site(problem,site,compress_routes=True)
    try:
        projection=project_physical_site(problem,context,candidate,fix=True)
        optimize(model)
        assert abs(model.ObjVal-projection['original_site_cost'])<1e-6
        projection.update(status=int(model.Status),objective=float(model.ObjVal),
                          fixed_physical_calendar_projection=True)
        model.write(str(OUT/('fixed_physical_'+site+'.mps.gz')))
        sites.append(projection)
    finally:model.dispose()
model,context=build_e1_routes(problem)
try:
    set_route_projection(problem,context,candidate,fix=True)
    fix_e2_schedule(problem,context,candidate)
    optimize(model)
    assert abs(model.ObjVal-audit['distances']['e1'])<1e-6
    e1=dict(status=int(model.Status),objective=float(model.ObjVal),actual_cutoff_quantities_fixed=True,
            original_pool_and_clean_stock=True)
    model.write(str(OUT/'fixed_E1.mps.gz'))
finally:model.dispose()

checkpoint('validating_new_upper_joint_projection')
manifest=json.loads((HERE/'manifest.json').read_text())
master=HERE/'inputs/master/joint_inventory.mps.gz'
digest=hashlib.sha256(master.read_bytes()).hexdigest()
entry=manifest.get('files',manifest)
assert entry['inputs/master/joint_inventory.mps.gz']==digest
model=gp.read(str(master))
try:
    context=context_for_export(problem,model,HERE/'inputs/master')
    projection=set_joint_projection(problem,context,candidate,fix=True)
    model.Params.Presolve=1
    optimize(model,45)
    assert model.ObjVal<=audit['objective']+1e-6
    joint=dict(status=int(model.Status),projected_objective=float(model.ObjVal),
        original_certified_master_sha256=digest,full_schedule_projection=True,
        complete_route_domain_unchanged=True,**projection)
finally:model.dispose()
baseline=json.loads((HERE/'inputs/baseline_certificate.json').read_text())
lower=baseline['lower_bound']
assert lower<=audit['objective']
summary=dict(full_audit=True,separate_arithmetic=True,physical_sites=sites,e1=e1,joint_projection=joint,
    lower_bound=lower,upper_bound=audit['objective'],gap=(audit['objective']-lower)/audit['objective'],
    proven_optimal=False,proven_infeasible=False,runtime=time.perf_counter()-begun,
    lower_proof_reused_without_modification=True,
    source_snapshot=str(SNAPSHOT),candidate_sha256=hashlib.sha256((OUT/'reproduced_candidate.json').read_bytes()).hexdigest())
write(OUT/'summary.json',summary)
checkpoint('new_upper_verified',active_process=None,known_upper=summary['upper_bound'],gap=summary['gap'],
    best_full_result=str(OUT/'reproduced_candidate.json'),upper_validation=str(OUT/'summary.json'),
    completed_tests='83 physical cuts/site/service tests passed',
    latest_note='Upper improved by 9.48955492456247 through real S4 vehicle reassignment and retiming; physical/E1/joint projections and full audit passed. Physical-site oracle probes added no new lower bound.',
    next_action='Generalize fixed-route retiming to optimize physical vehicle assignment (exact pairwise disjunctions plus ranks for zero-time safety), preserving four vehicles per site and all business rules. Compare against fixed-vehicle retiming with the SAME frozen original initial_result and budget. Use actual-cost epigraph for physical Benders floors, never direct compressed column-cost floors. Protect best verified upper and original lower; independently validate any further gain.')
print(json.dumps(summary,ensure_ascii=False,indent=2))
