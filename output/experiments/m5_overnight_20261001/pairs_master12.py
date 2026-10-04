"""Changed complete masters: identical group integrality, with/without no-good."""
from pathlib import Path
import json
import math
import hashlib
import os
import sys
import time

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot_pairs11'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_joint_inventory import set_joint_projection
from models.routing.solvers.exact.multibatch_physical_cuts import add_physical_cut,project_physical_costs
from warm_context import context_for_export
import gurobipy as gp

OUT=HERE/'pairs12/master'
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
checkpoint('building_pairs_master12',next_action='Wait for the sequential changed-master comparison; inspect pairs12/master/summary.json. New global bounds require independent matrix and certificate replay.')
proof=json.loads((HERE/'pairs11/master/independent_validation.json').read_text())
assert proof['matrix']['original_matrix_exactly_identical']
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
initial=json.loads((HERE/'partial8/initial_result.json').read_text())
audit=audit_solution(problem,initial);assert audit['valid']
verified=json.loads((HERE/'global_lower_newpoint9_certificate.json').read_text())
assert hashlib.sha256(Path(verified['master']).read_bytes()).hexdigest()==verified['master_sha256']
input_hashes=json.loads((HERE/'manifest_pairs12_input.json').read_text())['sha256']
for relative,digest in input_hashes.items():
    assert hashlib.sha256(Path(relative).read_bytes()).hexdigest()==digest
model=gp.read(str(HERE/'pairs11/master/no_good.mps.gz'))
context=context_for_export(problem,model,HERE/'inputs/master')
context['physical_costs']={s:model.getVarByName('physical_cost_'+s) for s in context['routes']}
context['physical_cover_rows']={s:model.getConstrByName('physical_cover_cost_'+s) for s in context['routes']}
context['e1']['supplies']={(s,bid):model.getVarByName(f'supply_{s}_{bid}')
    for (s,bid),count in problem.demands.items() if count}
from models.routing.solvers.exact.multibatch_task_pairs import add_task_pair_integrality,project_task_pairs
# All20order delivery/pickup co-location flags, rather than individual time-column integrality.
pair_keys=json.loads((HERE/'pairs12/pairs.json').read_text())
pair_records=add_task_pair_integrality(problem,model,context,'S1',pair_keys,name='pairs12')
write(OUT/'pairs.json',pair_keys)
set_joint_projection(problem,context,initial)
project_physical_costs(problem,context,initial)
project_task_pairs(problem,initial,pair_records)
old_pairs=json.loads((HERE/'pairs11/master/pairs.json').read_text())
old_pair_records=[dict(site='S1',tasks=[tuple(k) for k in pair],flag=model.getVarByName(f'pairs11_{i}'))
    for i,pair in enumerate(old_pairs)]
project_task_pairs(problem,initial,old_pair_records)
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
model.update();parameters(model)
starts=model.getAttr('Start',model.getVars())
shared_seconds=time.perf_counter()-begun
write(OUT/'parameters.json',dict(time_limit_per_variant=120,seed=42,
    shared_build_seconds_charged_to_each=shared_seconds,
    same_input=True,same_initial=True,same_existing_group_integrality=True,previous_19_pair_flags_preserved=True,
    formal_same_budget_comparison=False,incremental_separation_probe=True,comparison_sequential=True))

projected=model.copy();parameters(projected)
for v,value in zip(projected.getVars(),starts):
    if math.isfinite(value) and abs(value)<1e100:v.LB=v.UB=value
projected.Params.TimeLimit=45
projected.optimize();assert projected.Status==gp.GRB.OPTIMAL
assert projected.ObjVal<=audit['objective']+1e-6
write(OUT/'full_projection.json',dict(status=int(projected.Status),objective=float(projected.ObjVal),
    original_full_objective=audit['objective'],actual_cost_epigraph=True,
    exact_group_and_maximum_flags=True,exact_task_pair_flags=True,full_schedule_projection=True))
projected.dispose()

results=[]
for name in ('no_good',):
    checkpoint('running_pairs_master12_'+name)
    variant_begun=time.perf_counter()
    current=model.copy();parameters(current)
    try:
        if name=='control':
            for record in pair_records:current.getVarByName(record['flag'].VarName).VType=gp.GRB.CONTINUOUS
        for var,value in zip(current.getVars(),starts):var.Start=value
        current.update()
        current.write(str(OUT/(name+'.mps.gz')))
        current.Params.TimeLimit=max(0,120-shared_seconds-(time.perf_counter()-variant_begun))
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
            scope='experimental_complete_M5_joint_master_with_necessary_task_pair_integrality',
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
checkpoint('pairs_master12_complete',active_process=None,
    next_action='Read pairs12/master/summary.json. Compare ALL added master coefficients and replay changed MPS independently before promoting any improved global lower; protect current full upper and original lower if validation is pending.')
print(json.dumps(results,indent=2))
