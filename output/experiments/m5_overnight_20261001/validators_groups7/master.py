"""Independent master audit: infer trip sets from ORIGINAL cover rows."""
from pathlib import Path
import json
import sys
import time
import numpy as np

HERE=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(HERE/'source_snapshot_groups7'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
import gurobipy as gp

def row_value(model,row):
    e=model.getRow(row)
    return row.Sense,row.RHS,{e.getVar(i).VarName:e.getCoeff(i) for i in range(e.size())}

begun=time.perf_counter()
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
certificate=json.loads((HERE/'groups7/maximum_b2_zero.json').read_text())
physical=json.loads((HERE/'groups7/independent_validation.json').read_text())
assert physical['conditional_infeasible_status']==3
groups=certificate['trip_groups']
base=gp.read(str(HERE/'inputs/master/joint_inventory.mps.gz'))
changed=gp.read(str(HERE/'groups7/master/no_good.mps.gz'))
try:
    old_vars,new_vars=base.getVars(),changed.getVars()
    n,m=base.NumVars,base.NumConstrs
    names=base.getAttr('VarName',old_vars)
    assert names==changed.getAttr('VarName',new_vars[:n])
    for attribute in ('LB','UB','VType'):
        assert base.getAttr(attribute,old_vars)==changed.getAttr(attribute,new_vars[:n])
    objectives=np.asarray(base.getAttr('Obj',old_vars))
    cover_mask=np.asarray([name.startswith('joint_route_') for name in names])
    expected=objectives.copy();expected[cover_mask]=0
    assert np.array_equal(expected,np.asarray(changed.getAttr('Obj',new_vars[:n])))
    assert base.ModelSense==changed.ModelSense==gp.GRB.MINIMIZE and base.ObjCon==changed.ObjCon
    old_rows,new_rows=base.getConstrs(),changed.getConstrs()
    for attribute in ('ConstrName','Sense','RHS'):
        assert base.getAttr(attribute,old_rows)==changed.getAttr(attribute,new_rows[:m])
    old_matrix,new_matrix=base.getA().tocsr(),changed.getA().tocsr()
    assert np.array_equal(old_matrix.indptr,new_matrix.indptr[:m+1])
    assert np.array_equal(old_matrix.indices,new_matrix.indices[:old_matrix.nnz])
    assert np.array_equal(old_matrix.data,new_matrix.data[:old_matrix.nnz])
    assert base.NumGenConstrs==changed.NumGenConstrs==0
    costs={s:f'physical_cost_{s}' for s in ('S0','S1','S3','S4')}
    expected_rows={};added_vars=set(costs.values())
    for index,site in enumerate(sorted(costs)):
        prefix=f'joint_route_{index}_'
        terms={name:-float(objectives[i]) for i,name in enumerate(names)
               if name.startswith(prefix) and objectives[i]!=0}
        terms[costs[site]]=1
        expected_rows['physical_cover_cost_'+site]=('>',0,terms)
        v=changed.getVarByName(costs[site])
        assert v.LB==(433.19459171291777 if site=='S3' else 0)
        assert v.UB==gp.GRB.INFINITY and v.VType==gp.GRB.CONTINUOUS and v.Obj==1
    max_flag='groups7_cut_maximum_returns_b2'
    added_vars.add(max_flag)
    total=sum(o.count for o in problem.orders if o.satellite=='S3')
    cumulative={f'cutoff_{o.id}_{b.id}':-o.count for o in problem.orders if o.satellite=='S3'
        for b in problem.operating_policy.batches if problem.batch_index[b.id]<=problem.batch_index['b2']}
    expected_rows[max_flag+'_lower']=('>',-total,{**cumulative,max_flag:-total})
    expected_rows[max_flag+'_upper']=('<',-1,{**cumulative,max_flag:-1})
    # Decode task sets by the original coverage matrix, rather than calling
    # production grouping, timing-domain compression or cut-building helpers.
    site_tasks=[(o.id,k) for o in problem.orders if o.satellite=='S3' for k in ('d','p')]
    task_positions={key:i for i,key in enumerate(site_tasks)}
    masks={}
    for position,key in enumerate(site_tasks):
        row=base.getConstrByName(f'joint_cover_2_{position}')
        assert row.Sense=='=' and row.RHS==1
        i=row.index
        for entry in range(old_matrix.indptr[i],old_matrix.indptr[i+1]):
            column=int(old_matrix.indices[entry]);value=old_matrix.data[entry]
            assert value==1 and names[column].startswith('joint_route_2_')
            masks[column]=masks.get(column,0)|(1<<position)
    triggers=[max_flag]
    for g,group in enumerate(groups):
        flag=f'groups7_cut_group_{g}';added_vars.add(flag);triggers.append(flag)
        mask=sum(1<<task_positions[tuple(key)] for key in group)
        terms={names[i]:-1 for i,value in masks.items() if value==mask}
        terms[flag]=1
        expected_rows[f'groups7_cut_group_match_{g}']=('=',0,terms)
    active='groups7_cut_active';added_vars.add(active)
    for i,flag in enumerate(triggers):
        expected_rows[f'groups7_cut_and_upper_{i}']=('<',0,{active:1,flag:-1})
    expected_rows['groups7_cut_and_lower']=('>',1-len(triggers),
                                         {active:1,**{flag:-1 for flag in triggers}})
    expected_rows['groups7_cut_no_good']=('<',0,{active:1})
    assert {v.VarName for v in new_vars[n:]}==added_vars
    for name in added_vars-set(costs.values()):
        v=changed.getVarByName(name)
        assert (v.LB,v.UB,v.VType,v.Obj)==(0,1,gp.GRB.BINARY,0)
    observed={r.ConstrName:row_value(changed,r) for r in new_rows[m:]}
    assert observed.keys()==expected_rows.keys(),(observed.keys(),expected_rows.keys())
    assert all(observed[name]==value for name,value in expected_rows.items())
    matrix_proof=dict(original_variables=n,original_rows=m,original_nonzeros=old_matrix.nnz,
        original_matrix_exactly_identical=True,added_variables=len(added_vars),
        added_rows_checked=len(expected_rows),groups_from_original_cover_rows=True,
        theta_objective_and_floors_checked=True,exact_AND_and_maximum_checked=True)
    del old_matrix,new_matrix
finally:base.dispose()

trial=json.loads((HERE/'groups7/master/no_good.json').read_text())
old_lower=json.loads((HERE/'combined_certificate.json').read_text())['lower_bound']
candidate=trial['candidate_global_bound']
replay=None;supported=False
if candidate>old_lower+1e-6:
    changed.Params.Threads=1;changed.Params.Seed=42;changed.Params.Presolve=1
    changed.Params.IntFeasTol=changed.Params.FeasibilityTol=1e-9
    changed.Params.MIPGap=changed.Params.MIPGapAbs=0
    changed.Params.TimeLimit=180;changed.Params.BestBdStop=candidate
    # The same audited initial is given as a sparse start, using original
    # already-validated projection helpers only AFTER coefficient validation.
    from warm_context import context_for_export
    from models.routing.solvers.exact.multibatch_joint_inventory import set_joint_projection
    context=context_for_export(problem,changed,HERE/'inputs/master')
    initial=json.loads((HERE/'groups7/initial_result.json').read_text())
    assert audit_solution(problem,initial)['valid']
    set_joint_projection(problem,context,initial)
    changed.optimize()
    bound=float(changed.ObjBound);supported=bound>=candidate-1e-7
    replay=dict(status=int(changed.Status),raw_bound=bound,candidate_supported=supported,
        candidate=candidate,objective=float(changed.ObjVal) if changed.SolCount else None,
        runtime=changed.Runtime,independent_bound_stop=candidate)
changed.dispose()
result=dict(matrix=matrix_proof,physical_conditional_proof=physical,
    candidate_global_lower=candidate,old_global_lower=old_lower,independent_replay=replay,
    promote_supported=supported,full_proven_optimal=False,runtime=time.perf_counter()-begun)
(HERE/'groups7/master/independent_validation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
