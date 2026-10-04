"""Independent master audit: infer trip sets from ORIGINAL cover rows."""
from pathlib import Path
import json
import sys
import time
import numpy as np
import hashlib
import gzip
import itertools

HERE=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(HERE/'source_snapshot_ceiling14'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
import gurobipy as gp

def row_value(model,row):
    e=model.getRow(row)
    return row.Sense,row.RHS,{e.getVar(i).VarName:e.getCoeff(i) for i in range(e.size())}

begun=time.perf_counter()
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
verified_base=json.loads((HERE/'global_lower_newpoint9_certificate.json').read_text())
assert verified_base['independent_replay']['promote_supported']
assert hashlib.sha256(Path(verified_base['master']).read_bytes()).hexdigest()==verified_base['master_sha256']
input_manifest=json.loads((HERE/'manifest_s1master18_input.json').read_text())['sha256']
for relative,digest in input_manifest.items():
    assert hashlib.sha256(Path(relative).read_bytes()).hexdigest()==digest
base=gp.read(str(HERE/'pairs11/master/no_good.mps.gz'))
changed=gp.read(str(HERE/'s1master18/master/no_good.mps.gz'))
try:
    old_vars,new_vars=base.getVars(),changed.getVars()
    n,m=base.NumVars,base.NumConstrs
    names=base.getAttr('VarName',old_vars)
    assert names==changed.getAttr('VarName',new_vars[:n])
    for attribute in ('LB','UB','VType'):
        assert base.getAttr(attribute,old_vars)==changed.getAttr(attribute,new_vars[:n])
    objectives=np.asarray(base.getAttr('Obj',old_vars))
    assert np.array_equal(objectives,np.asarray(changed.getAttr('Obj',new_vars[:n])))
    assert base.ModelSense==changed.ModelSense==gp.GRB.MINIMIZE and base.ObjCon==changed.ObjCon
    old_rows,new_rows=base.getConstrs(),changed.getConstrs()
    for attribute in ('ConstrName','Sense','RHS'):
        assert base.getAttr(attribute,old_rows)==changed.getAttr(attribute,new_rows[:m])
    def original_entries(path,rows):
        with gzip.open(path,'rt') as stream:
            active=False
            for line in stream:
                fields=line.split()
                if not active:
                    if fields==['COLUMNS']:active=True
                    continue
                if fields==['RHS']:break
                if len(fields)>=2 and fields[1]=="'MARKER'":continue
                assert len(fields) in (3,5),fields
                for j in range(1,len(fields),2):
                    if fields[j] in rows:
                        yield fields[0],fields[j],float(fields[j+1])
    retained=set(base.getAttr('ConstrName',old_rows))
    old_stream=original_entries(HERE/'pairs11/master/no_good.mps.gz',retained)
    new_stream=original_entries(HERE/'s1master18/master/no_good.mps.gz',retained)
    original_nonzeros=0
    for left,right in itertools.zip_longest(old_stream,new_stream):
        assert left==right,(left,right)
        original_nonzeros+=1
    assert base.NumGenConstrs==changed.NumGenConstrs
    def indicator_rows(model):
        result={}
        for row in model.getGenConstrs():
            assert row.GenConstrType==gp.GRB.GENCONSTR_INDICATOR
            flag,value,e,sense,rhs=model.getGenConstrIndicator(row)
            result[row.GenConstrName]=(flag.VarName,value,sense,rhs,
                {e.getVar(i).VarName:e.getCoeff(i) for i in range(e.size())})
        return result
    assert indicator_rows(base)==indicator_rows(changed)
    expected_rows={};added_vars=set()
    site='S1';site_index=sorted({o.satellite for o in problem.orders}).index(site)
    site_tasks=[(o.id,k) for o in problem.orders if o.satellite==site for k in ('d','p')]
    positions={key:i for i,key in enumerate(site_tasks)};masks={}
    for position,key in enumerate(site_tasks):
        row=base.getConstrByName(f'joint_cover_{site_index}_{position}')
        assert row.Sense=='=' and row.RHS==1
        e=base.getRow(row)
        for entry in range(e.size()):
            column=e.getVar(entry).index
            assert e.getCoeff(entry)==1 and names[column].startswith(f'joint_route_{site_index}_')
            masks[column]=masks.get(column,0)|(1<<position)
    certificate=json.loads((HERE/'s1full16/unconditional.json').read_text())
    physical=json.loads((HERE/'s1full16/independent_validation.json').read_text())
    assert physical['replays']['unconditional']['status']==gp.GRB.OPTIMAL
    groups=certificate['trip_groups'];triggers=[]
    assert {tuple(key) for group in groups for key in group}==set(site_tasks)
    for i,group in enumerate(groups):
        flag=f's1master18_cut_group_{i}';added_vars.add(flag);triggers.append(flag)
        mask=sum(1<<positions[tuple(key)] for key in group)
        terms={names[j]:-1 for j,value in masks.items() if value==mask};terms[flag]=1
        expected_rows[f's1master18_cut_group_match_{i}']=('=',0,terms)
    active='s1master18_cut_active';added_vars.add(active)
    for i,flag in enumerate(triggers):
        expected_rows[f's1master18_cut_and_upper_{i}']=('<',0,{active:1,flag:-1})
    expected_rows['s1master18_cut_and_lower']=('>',1-len(triggers),{active:1,**{flag:-1 for flag in triggers}})
    floor=451.71429479447573;delta=certificate['bound']-floor
    assert delta>0
    expected_rows['s1master18_cut_cost_floor']=('>',floor,{'physical_cost_S1':1,active:-delta})
    assert {v.VarName for v in new_vars[n:]}==added_vars
    for name in added_vars:
        v=changed.getVarByName(name)
        assert (v.LB,v.UB,v.VType,v.Obj)==(0,1,gp.GRB.BINARY,0)
    observed={r.ConstrName:row_value(changed,r) for r in new_rows[m:]}
    assert observed.keys()==expected_rows.keys()
    assert all(observed[name]==value for name,value in expected_rows.items())
    matrix_proof=dict(original_variables=n,original_rows=m,original_nonzeros=original_nonzeros,
        original_matrix_exactly_identical=True,original_indicators_checked=base.NumGenConstrs,
        added_variables=len(added_vars),added_rows_checked=len(expected_rows),
        exact_groups_from_original_cover_rows=True,all_previous_objectives_floors_and_no_goods_preserved=True,
        actual_cost_epigraph_target=True,exact_AND_checked=True)
finally:base.dispose()

trial=json.loads((HERE/'s1master18/master/no_good.json').read_text())
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
    initial=json.loads((HERE/'partial8/initial_result.json').read_text())
    assert audit_solution(problem,initial)['valid']
    set_joint_projection(problem,context,initial)
    context['physical_costs']={s:changed.getVarByName('physical_cost_'+s) for s in context['routes']}
    context['physical_cover_rows']={s:changed.getConstrByName('physical_cover_cost_'+s) for s in context['routes']}
    context['e1']['supplies']={(site,bid):changed.getVarByName(f'supply_{site}_{bid}')
        for (site,bid),count in problem.demands.items() if count}
    from models.routing.solvers.exact.multibatch_physical_cuts import project_physical_costs
    project_physical_costs(problem,context,initial)
    actual={frozenset((e['order_id'],e['kind']) for e in t['events'])
        for t in initial['e2_trips'] if t['satellite_id']=='S3'}
    returned=sum(e['quantity'] for t in initial['e2_trips'] if t['satellite_id']=='S3'
        and t['return_complete']<=problem.batch_map['b2'].cutoff for e in t['events'] if e['kind']=='p')
    for folder,prefix in (('groups7','groups7_cut'),('partial8','partial8_cut'),('newpoint9','newpoint9_cut')):
        desired=json.loads((HERE/folder/'maximum_b2_zero.json').read_text())['trip_groups']
        flags=[]
        for g,group in enumerate(desired):
            value=int(frozenset(tuple(k) for k in group) in actual);flags.append(value)
            changed.getVarByName(f'{prefix}_group_{g}').Start=value
        changed.getVarByName(f'{prefix}_maximum_returns_b2').Start=int(returned==0)
        changed.getVarByName(f'{prefix}_active').Start=int(returned==0 and all(flags))
    desired=certificate['trip_groups']
    actual_s1={frozenset((e['order_id'],e['kind']) for e in trip['events'])
        for trip in initial['e2_trips'] if trip['satellite_id']=='S1'}
    flags=[]
    for i,group in enumerate(desired):
        value=int(frozenset(tuple(k) for k in group) in actual_s1);flags.append(value)
        changed.getVarByName(f's1master18_cut_group_{i}').Start=value
    changed.getVarByName('s1master18_cut_active').Start=int(all(flags))
    from models.routing.solvers.exact.multibatch_task_pairs import project_task_pairs
    pairs=json.loads((HERE/'pairs11/master/pairs.json').read_text())
    records=[dict(site='S1',tasks=[tuple(k) for k in pair],flag=changed.getVarByName(f'pairs11_{i}'))
        for i,pair in enumerate(pairs)]
    project_task_pairs(problem,initial,records)
    changed.optimize()
    bound=float(changed.ObjBound);supported=bound>=candidate-1e-7
    replay=dict(status=int(changed.Status),raw_bound=bound,candidate_supported=supported,
        candidate=candidate,objective=float(changed.ObjVal) if changed.SolCount else None,
        runtime=changed.Runtime,independent_bound_stop=candidate)
changed.dispose()
result=dict(matrix=matrix_proof,prior_global_certificate=str(HERE/'global_lower_newpoint9_certificate.json'),physical_conditional_cost_proof=physical,
    candidate_global_lower=candidate,old_global_lower=old_lower,independent_replay=replay,
    promote_supported=supported,full_proven_optimal=False,runtime=time.perf_counter()-begun)
(HERE/'s1master18/master/independent_validation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
