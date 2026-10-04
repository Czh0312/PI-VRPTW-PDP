"""Changed complete masters: identical group integrality, with/without no-good."""
from pathlib import Path
import json
import math
import os
import sys
import time

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot_groups7'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_joint_inventory import set_joint_projection
from models.routing.solvers.exact.multibatch_physical_cuts import add_physical_cut,project_physical_costs
from warm_context import context_for_export
import gurobipy as gp

OUT=HERE/'groups7/master'
OUT.mkdir(exist_ok=True,parents=True)
def write(path,value):path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
def checkpoint(status,**extra):
    c=json.loads((HERE/'checkpoint.json').read_text())
    c.update({'status':status,'active_process':os.getpid(),**extra});write(HERE/'checkpoint.json',c)
def parameters(model):
    model.Params.Threads=1;model.Params.Seed=42;model.Params.Presolve=1
    model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9
    model.Params.MIPGap=model.Params.MIPGapAbs=0;model.Params.DualReductions=0

begun=time.perf_counter()
checkpoint('building_group_master7',next_action='Wait for the sequential changed-master comparison; inspect groups7/master/summary.json. New global bounds require independent matrix and certificate replay.')
proof=json.loads((HERE/'groups7/independent_validation.json').read_text())
assert proof['conditional_infeasible_status']==gp.GRB.INFEASIBLE
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
initial=json.loads((HERE/'groups7/initial_result.json').read_text())
audit=audit_solution(problem,initial);assert audit['valid']
model=gp.read(str(HERE/'inputs/master/joint_inventory.mps.gz'))
context=context_for_export(problem,model,HERE/'inputs/master')
context['e1']['supplies']={(s,bid):model.getVarByName(f'supply_{s}_{bid}')
    for (s,bid),count in problem.demands.items() if count}
cert=json.loads((HERE/'groups7/maximum_b2_zero.json').read_text())
cut=add_physical_cut(problem,model,context,cert,base_floor=433.19459171291777,name='groups7_cut')
set_joint_projection(problem,context,initial)
project_physical_costs(problem,context,initial,cuts=[cut])
model.update();parameters(model)
starts=model.getAttr('Start',model.getVars())
shared_seconds=time.perf_counter()-begun
write(OUT/'parameters.json',dict(time_limit_per_variant=180,seed=42,
    shared_build_seconds_charged_to_each=shared_seconds,
    same_input=True,same_initial=True,same_group_integrality=True,
    conditional_no_good_only_difference=True,comparison_sequential=True))

projected=model.copy();parameters(projected)
for v,value in zip(projected.getVars(),starts):
    if math.isfinite(value) and abs(value)<1e100:v.LB=v.UB=value
projected.Params.TimeLimit=45
projected.optimize();assert projected.Status==gp.GRB.OPTIMAL
assert projected.ObjVal<=audit['objective']+1e-6
write(OUT/'full_projection.json',dict(status=int(projected.Status),objective=float(projected.ObjVal),
    original_full_objective=audit['objective'],actual_cost_epigraph=True,
    exact_group_and_maximum_flags=True,full_schedule_projection=True))
projected.dispose()

results=[]
for name in ('control','no_good'):
    checkpoint('running_group_master7_'+name)
    variant_begun=time.perf_counter()
    current=model.copy();parameters(current)
    try:
        if name=='control':current.remove(current.getConstrByName('groups7_cut_no_good'))
        for var,value in zip(current.getVars(),starts):var.Start=value
        current.update()
        current.write(str(OUT/(name+'.mps.gz')))
        current.Params.TimeLimit=max(0,180-shared_seconds-(time.perf_counter()-variant_begun))
        current.optimize()
        raw=float(current.ObjBound);margin=1e-7*max(1,abs(raw))
        assert current.Status!=gp.GRB.INFEASIBLE
        result=dict(name=name,status=int(current.Status),raw_bound=raw,margin=margin,
            candidate_global_bound=raw-margin,objective=float(current.ObjVal) if current.SolCount else None,
            relaxation_optimal=current.Status==gp.GRB.OPTIMAL,variables=current.NumVars,
            constraints=current.NumConstrs,nodes=current.NodeCount,
            charged_wall_seconds=shared_seconds+time.perf_counter()-variant_begun,
            shared_build_seconds=shared_seconds,solver_runtime=current.Runtime,
            full_proven_optimal=False,full_proven_infeasible=False,
            scope='experimental_complete_M5_joint_master_with_verified_conditional_group_cut',
            independent_validation_required=True)
        write(OUT/(name+'.json'),result);results.append(result);write(OUT/'summary.json',results)
    finally:current.dispose()
model.dispose()
checkpoint('groups_master7_complete',active_process=None,
    next_action='Read groups7/master/summary.json. Compare ALL added master coefficients and replay changed MPS independently before promoting any improved global lower; protect current full upper and original lower if validation is pending.')
print(json.dumps(results,indent=2))
