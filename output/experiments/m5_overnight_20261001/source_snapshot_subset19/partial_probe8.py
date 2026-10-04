"""Exact whole-group physical subproblems on unchanged fifty-customer data."""
from pathlib import Path
import json
import os
import sys
import time

HERE=Path(__file__).resolve().parent
SNAPSHOT=HERE/'source_snapshot_partial8'
OUT=HERE/'partial8'
sys.path.insert(0,str(SNAPSHOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_physical_site import (
    build_physical_site,project_physical_site,physical_site_certificate)
import gurobipy as gp

def write(path,value):
    path.parent.mkdir(exist_ok=True,parents=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')

def checkpoint(status,**extra):
    value=json.loads((HERE/'checkpoint.json').read_text())
    value.update({'status':status,'active_process':os.getpid(),'source_snapshot':str(SNAPSHOT),**extra})
    write(HERE/'checkpoint.json',value)

problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
initial=json.loads((OUT/'initial_result.json').read_text())
assert audit_solution(problem,initial)['valid']
groups=json.loads((OUT/'groups.json').read_text())
floor=433.19459171291777
checkpoint('validating_partial8_full_projection',next_action='Wait for the partial8 sequential worker. Do not duplicate its exact group subproblems.')
model,context=build_physical_site(problem,'S3',trip_groups=groups,partial_groups=True,cost_floor=floor,compress_routes=True)
try:
    schema=[dict(label=r['label'],vehicle=r['vehicle'],slot=r['slot'],used=r['used'].VarName,
        nodes=[(n['order'].id,n['kind']) for n in r['nodes']],
        visits={str(k):v.VarName for k,v in r['visit'].items()},
        arcs=[dict(a=a,b=b,name=v.VarName) for (a,b),v in r['x'].items()]) for r in context['e2']]
    write(OUT/'route_schema.json',schema)
    projection=project_physical_site(problem,context,initial,fix=True)
    model.Params.TimeLimit=30;model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9
    model.optimize();assert model.Status==gp.GRB.OPTIMAL
    assert abs(model.ObjVal-projection['original_site_cost'])<1e-6
    projection.update(status=int(model.Status),objective=float(model.ObjVal),full_projection=True)
    model.write(str(OUT/'fixed_S3_groups.mps.gz'));write(OUT/'fixed_projection.json',projection)
finally:model.dispose()

summary=[]
for name,maximum in [('unconditional',{}),('maximum_b2_zero',{'b2':0})]:
    checkpoint('running_partial8_'+name)
    parameters=dict(time_limit=180,seed=42,maximum_returns=maximum,trip_groups=groups,partial_groups=True,
        fallback_bound=floor,compress_routes=True,initial_result=None)
    write(OUT/(name+'_parameters.json'),parameters)
    result=physical_site_certificate(problem,'S3',verbose=True,
        diagnostics_path=OUT/(name+'.mps.gz'),**parameters)
    result.update(full_lower_not_promoted=True,source_snapshot=str(SNAPSHOT),
        original_business_input_unchanged=True,all_group_paths_retained=True)
    write(OUT/(name+'.json'),result)
    summary.append(dict(name=name,bound=result['bound'],raw_bound=result['raw_bound'],
        status=result['status'],objective=result['subproblem_objective'],
        subproblem_infeasible=result['subproblem_infeasible'],runtime=result['runtime']))
    write(OUT/'summary.json',summary)
checkpoint('partial8_worker_complete',active_process=None,
    next_action='Read partial8/summary.json. Independently compare grouped matrix to original physical base and verify exact whole-task-group activation before adding a conditional cut to a separate global-master copy. Never promote conditional or restricted values directly to full M5 bounds.')
print(json.dumps(summary,indent=2))
