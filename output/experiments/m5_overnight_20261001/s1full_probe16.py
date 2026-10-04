"""Exact whole-group physical subproblems on unchanged fifty-customer data."""
from pathlib import Path
import json
import os
import sys
import time

HERE=Path(__file__).resolve().parent
SNAPSHOT=HERE/'source_snapshot_ceiling14'
OUT=HERE/'s1full16'
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
initial=json.loads((HERE/'best_full_result.json').read_text())
assert audit_solution(problem,initial)['valid']
groups=json.loads((OUT/'groups.json').read_text())
floor=451.71429479447573
checkpoint('validating_s1full16_full_projection',next_action='Wait for the s1full16 sequential worker. Do not duplicate its exact group subproblems.')
model,context=build_physical_site(problem,'S1',trip_groups=groups,partial_groups=False,cost_floor=floor,compress_routes=True)
try:
    schema=[dict(label=r['label'],vehicle=r['vehicle'],slot=r['slot'],used=r['used'].VarName,
        nodes=[(n['order'].id,n['kind']) for n in r['nodes']],
        visits={str(k):v.VarName for k,v in r['visit'].items()},
        arcs=[dict(a=a,b=b,name=v.VarName) for (a,b),v in r['x'].items()]) for r in context['e2']]
    write(OUT/'route_schema.json',schema)
    projection=project_physical_site(problem,context,initial,fix=True)
    model.update();model.write(str(OUT/'fixed_full_projection.mps.gz'))
    model.Params.Threads=1;model.Params.TimeLimit=30
    model.optimize();assert model.Status==gp.GRB.OPTIMAL
    write(OUT/'full_projection.json',dict(projection,objective=float(model.ObjVal),status=int(model.Status),
        full_schedule_projection=True))
finally:model.dispose()

summary=[]
for name,maximum in [('unconditional',{}),('maximum_b2_ten',{'b2':10})]:
    checkpoint('running_s1full16_'+name)
    parameters=dict(time_limit=30,seed=42,maximum_returns=maximum,trip_groups=groups,partial_groups=False,
        fallback_bound=floor,compress_routes=True,initial_result=initial if not maximum else None)
    write(OUT/(name+'_parameters.json'),{k:v for k,v in parameters.items() if k!='initial_result'})
    result=physical_site_certificate(problem,'S1',verbose=True,
        diagnostics_path=OUT/(name+'.mps.gz'),**parameters)
    result.update(full_lower_not_promoted=True,source_snapshot=str(SNAPSHOT),
        original_business_input_unchanged=True,all_group_paths_retained=True)
    write(OUT/(name+'.json'),result)
    summary.append(dict(name=name,bound=result['bound'],raw_bound=result['raw_bound'],
        status=result['status'],objective=result['subproblem_objective'],
        subproblem_infeasible=result['subproblem_infeasible'],runtime=result['runtime']))
    write(OUT/'summary.json',summary)
checkpoint('s1full16_worker_complete',active_process=None,
    next_action='Read s1full16/summary.json. Independently compare grouped matrix to original physical base and verify exact whole-task-group activation before adding a conditional cut to a separate global-master copy. Never promote conditional or restricted values directly to full M5 bounds.')
print(json.dumps(summary,indent=2))
