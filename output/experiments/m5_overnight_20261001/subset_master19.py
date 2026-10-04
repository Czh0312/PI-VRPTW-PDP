"""Changed complete masters: identical group integrality, with/without no-good."""
from pathlib import Path
import json
import math
import hashlib
import os
import sys
import time

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot_subset19'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_joint_inventory import set_joint_projection
from models.routing.solvers.exact.multibatch_physical_cuts import add_physical_cut,project_physical_costs
from warm_context import context_for_export
import gurobipy as gp

OUT=HERE/'subset19/master'
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
checkpoint('building_subset19',next_action='Wait for the sequential changed-master comparison; inspect s1master18/master/summary.json. New global bounds require independent matrix and certificate replay.')
proof=json.loads((HERE/'s1master18/master/independent_validation.json').read_text())
assert proof['matrix']['original_matrix_exactly_identical']
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
initial=json.loads((HERE/'partial8/initial_result.json').read_text())
audit=audit_solution(problem,initial);assert audit['valid']
manifest=json.loads((HERE/'manifest_subset19_input.json').read_text())['sha256']
for relative,digest in manifest.items():
    assert hashlib.sha256(Path(relative).read_bytes()).hexdigest()==digest
model=gp.read(str(HERE/'s1master18/master/no_good.mps.gz'))
context=context_for_export(problem,model,HERE/'inputs/master')
context['physical_costs']={s:model.getVarByName('physical_cost_'+s) for s in context['routes']}
context['physical_cover_rows']={s:model.getConstrByName('physical_cover_cost_'+s) for s in context['routes']}
context['e1']['supplies']={(s,bid):model.getVarByName(f'supply_{s}_{bid}')
    for (s,bid),count in problem.demands.items() if count}
from models.routing.solvers.exact.multibatch_subset_rows import add_triangle_rows
triangles=json.loads((HERE/'subset19/triangles.json').read_text())
assert triangles
records=add_triangle_rows(problem,model,context,'S1',[row['tasks'] for row in triangles],name='subset19')
set_joint_projection(problem,context,initial)
project_physical_costs(problem,context,initial)
actual_groups={frozenset((e['order_id'],e['kind']) for e in t['events'])
    for t in initial['e2_trips'] if t['satellite_id']=='S3'}
returned=sum(e['quantity'] for t in initial['e2_trips'] if t['satellite_id']=='S3'
    and t['return_complete']<=problem.batch_map['b2'].cutoff for e in t['events'] if e['kind']=='p')
for prefix,folder in (('groups7_cut','groups7'),('partial8_cut','partial8'),('newpoint9_cut','newpoint9')):
    old_certificate=json.loads((HERE/folder/'maximum_b2_zero.json').read_text())
    old_flags=[]
    for g,group in enumerate(old_certificate['trip_groups']):
        value=int(frozenset(tuple(k) for k in group) in actual_groups)
        model.getVarByName(f'{prefix}_group_{g}').Start=value;old_flags.append(value)
    model.getVarByName(f'{prefix}_maximum_returns_b2').Start=int(returned==0)
    model.getVarByName(f'{prefix}_active').Start=int(returned==0 and all(old_flags))
s1_groups=json.loads((HERE/'s1full16/unconditional.json').read_text())['trip_groups']
s1_actual={frozenset((e['order_id'],e['kind']) for e in t['events'])
    for t in initial['e2_trips'] if t['satellite_id']=='S1'}
s1_flags=[]
for i,group in enumerate(s1_groups):
    value=int(frozenset(tuple(k) for k in group) in s1_actual);s1_flags.append(value)
    model.getVarByName(f's1master18_cut_group_{i}').Start=value
model.getVarByName('s1master18_cut_active').Start=int(all(s1_flags))
from models.routing.solvers.exact.multibatch_task_pairs import project_task_pairs
old_pairs=json.loads((HERE/'pairs11/master/pairs.json').read_text())
old_pair_records=[dict(site='S1',tasks=[tuple(k) for k in pair],flag=model.getVarByName(f'pairs11_{i}'))
    for i,pair in enumerate(old_pairs)]
project_task_pairs(problem,initial,old_pair_records)
model.update();parameters(model)
starts=model.getAttr('Start',model.getVars())
shared_seconds=time.perf_counter()-begun
write(OUT/'parameters.json',dict(time_limit_per_variant=180,seed=42,
    shared_build_seconds_charged_to_each=shared_seconds,
    same_input=True,same_initial=True,same_group_integrality=True,
    triangle_rows_only_difference=True,recorded_control='s1master18/master/no_good.json',comparison_sequential=True))

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
for name in ('with_rows',):
    checkpoint('running_subset19_'+name)
    variant_begun=time.perf_counter()
    current=model.copy();parameters(current)
    try:
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
            scope='complete_M5_joint_master_with_task_triangle_subset_rows',
            independent_validation_required=True)
        if current.SolCount:
            current.write(str(OUT/(name+'.sol.gz')))
            points={}
            for site,data in context['routes'].items():
                selected=[]
                for (i,bid),variable in data['choices'].items():
                    v=current.getVarByName(variable.VarName)
                    if v.X>1e-7:
                        row=data['rows'][i]
                        selected.append(dict(value=float(v.X),cutoff=bid,tasks=row['keys'],column=row['column']))
                points[site]=dict(selected_columns=selected,
                    projected_supplies={b.id:int(round(current.getVarByName(f'supply_{site}_{b.id}').X))
                        for b in problem.operating_policy.batches if problem.demands[site,b.id]},
                    cumulative_return_counts={b.id:int(round(sum(o.count*current.getVarByName(f'cutoff_{o.id}_{bb.id}').X
                        for o in problem.orders if o.satellite==site for bb in problem.operating_policy.batches
                        if problem.batch_index[bb.id]<=problem.batch_index[b.id]))) for b in problem.operating_policy.batches},
                    integer_partition=all(abs(c['value']-1)<1e-7 for c in selected),
                    site_cost=float(current.getVarByName('physical_cost_'+site).X))
            write(OUT/(name+'_point.json'),dict(objective=float(current.ObjVal),sites=points,
                physical_full_schedule=False,full_proven_optimal=False))
        write(OUT/(name+'.json'),result);results.append(result);write(OUT/'summary.json',results)
    finally:current.dispose()
model.dispose()
checkpoint('subset19_complete',active_process=None,
    next_action='Read s1master18/master/summary.json. Compare ALL added master coefficients and replay changed MPS independently before promoting any improved global lower; protect current full upper and original lower if validation is pending.')
print(json.dumps(results,indent=2))
