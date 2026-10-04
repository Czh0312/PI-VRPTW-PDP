"""Sequential fresh-pool comparison and exact maximum-return physical probes."""
from pathlib import Path
import copy
import hashlib
import json
import os
import sys
import time

HERE=Path(__file__).resolve().parent
SNAPSHOT=HERE/'source_snapshot_maximum6'
OUT=HERE/'maximum6'
sys.path.insert(0,str(SNAPSHOT))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_physical_site import (
    build_physical_site, project_physical_site, physical_site_certificate)
from models.routing.solvers.exact.multibatch_route_pool import reoptimize_route_pool
import gurobipy as gp

def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')

def checkpoint(status,**extra):
    value=json.loads((HERE/'checkpoint.json').read_text())
    value.update({'status':status,'active_process':os.getpid(),'source_snapshot':str(SNAPSHOT),**extra})
    write(HERE/'checkpoint.json',value)

problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
initial=json.loads((OUT/'initial_result.json').read_text())
columns=json.loads((HERE/'pool_after_vehicle4/additional_columns.json').read_text())
assert audit_solution(problem,initial)['valid']
begun=time.perf_counter()
pools=json.loads((OUT/'pool_summary.json').read_text())
(OUT/'projections').mkdir(exist_ok=True)
projections=[]
for site in sorted({o.satellite for o in problem.orders}):
    checkpoint('validating_maximum6_full_projection_'+site)
    counts={b.id:sum(e['quantity'] for t in initial['e2_trips'] if t['satellite_id']==site
        and t['return_complete']<=b.cutoff for e in t['events'] if e['kind']=='p')
        for b in problem.operating_policy.batches}
    model,context=build_physical_site(problem,site,minimum_returns=counts,maximum_returns=counts,
                                     compress_routes=True)
    try:
        # Check both indicator directions directly, before fixing clocks changes
        # variable bounds. Every nonconstant flag must use the SAME return clock.
        inspected=0
        indicators={r.GenConstrName:r for r in model.getGenConstrs()}
        for (oid,bid),flag in context['return_flags'].items():
            if isinstance(flag,int):continue
            for suffix,value,sense,rhs in [('yes',1,'<',problem.batch_map[bid].cutoff),
                                          ('no',0,'>',problem.batch_map[bid].cutoff+1)]:
                row=indicators[f'physical_early_{suffix}_{oid}_{bid}']
                f,v,expression,s,r=model.getGenConstrIndicator(row)
                assert f.index==flag.index and v==value and s==sense and r==rhs
                assert expression.size()==1 and expression.getCoeff(0)==1
                assert expression.getVar(0).index==context['pickup_returns'][oid].index
                inspected+=1
        projected=project_physical_site(problem,context,initial,fix=True)
        model.Params.Threads=1;model.Params.TimeLimit=30
        model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9
        model.optimize();assert model.Status==gp.GRB.OPTIMAL
        assert abs(model.ObjVal-projected['original_site_cost'])<1e-6
        projected.update(checked_return_indicators=inspected,status=int(model.Status),
                         objective=float(model.ObjVal),full_schedule_projection=True)
        model.write(str(OUT/'projections'/('fixed_'+site+'.mps.gz')))
        projections.append(projected)
        write(OUT/'projections'/'summary.json',projections)
    finally:model.dispose()

source=json.loads((OUT/'S3_cover_floor_source.json').read_text())
floor=source['bound']
probes=[]
# Both runs have identical business input, no initial MIP start, floor, budget
# and seed. The second run adds one exact count condition; it is NOT a full bound.
for name,maximum in [('unconditional',{}),('maximum_b2_zero',{'b2':0})]:
    checkpoint('running_maximum6_physical_'+name)
    result=physical_site_certificate(problem,'S3',time_limit=180,maximum_returns=maximum,
        fallback_bound=floor,seed=42,verbose=True,compress_routes=True,
        diagnostics_path=OUT/'physical'/(name+'.mps.gz'))
    result.update(source_snapshot=str(SNAPSHOT),floor_source=str(OUT/'S3_cover_floor_source.json'),
                  full_global_lower_unchanged=True)
    write(OUT/'physical'/(name+'.json'),result)
    probes.append(dict(name=name,bound=result['bound'],raw_bound=result['raw_bound'],
        objective=result['subproblem_objective'],status=result['status'],runtime=result['runtime'],
        additional_conditional_floor=max(0,result['bound']-floor),
        subproblem_infeasible=result['subproblem_infeasible']))
    write(OUT/'physical'/'summary.json',probes)
write(OUT/'summary.json',dict(pools=pools,full_projections=projections,physical_probes=probes,
    runtime=time.perf_counter()-begun,global_lower_unchanged=True,upper_not_promoted=True))
checkpoint('maximum6_worker_complete',active_process=None,
    next_action='Read maximum6/summary.json. Independently replay any improved complete upper before promotion; any conditional physical bound must be replayed and activated on an actual-cost epigraph before it affects the global lower. Record negative probes and avoid repeating identical pools.')
print(json.dumps(dict(runtime=time.perf_counter()-begun,pools=[(p['name'],p['objective']) for p in pools],probes=probes),indent=2))
