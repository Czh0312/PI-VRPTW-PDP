"""Reproduce and verify the vehicle/calendar candidate on frozen data."""
from pathlib import Path
import copy
import hashlib
import json
import math
import os
import sys
import time

HERE = Path(__file__).resolve().parent.parent
SNAPSHOT = HERE/'source_snapshot_cover24'
OUT = HERE/'cover24/validation'
OUT.mkdir(exist_ok=True,parents=True)
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
checkpoint('validating_pool_candidate',source_snapshot=str(SNAPSHOT),
    next_action='Wait for this validator; inspect upper_retiming/validation/summary.json. No other heavy solver should start concurrently.')
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
initial=json.loads((HERE/'pool22/initial_result.json').read_text())
candidate=json.loads((HERE/'cover24/result.json').read_text())
for field in ('audit','cutoff_snapshots','depot_releases'): candidate.pop(field,None)
candidate['info']=dict(status='FEASIBLE',objective=None,best_bound=None,mip_gap=None,
    proven_optimal=False,proven_infeasible=False,
    proof_scope='audited_route_pool_full_upper')
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


manifest=json.loads((HERE/'manifest_cover24_input.json').read_text())['sha256']
for path,digest in manifest.items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest
result=dict(full_audit_valid=True,independent_directed_arithmetic=True,
    original_physical_site_projections=sites,original_E1_projection=e1,
    source_and_input_hashes_checked=len(manifest),upper=audit['objective'],
    full_proven_optimal=False,full_proven_infeasible=False,runtime=time.perf_counter()-begun)
write(OUT/'summary.json',result)
checkpoint('pool22_full_upper_validation_complete',active_process=None)
print(json.dumps(result,indent=2))
