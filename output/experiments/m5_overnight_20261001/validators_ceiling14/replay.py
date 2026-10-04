from pathlib import Path
import hashlib,json,sys,time
import gurobipy as gp
HERE=Path(__file__).resolve().parent.parent
started=time.perf_counter()
manifest=json.loads((HERE/'manifest_ceiling14_input.json').read_text())['sha256']
for p,digest in manifest.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==digest
previous=json.loads((HERE/'s1groups13/independent_validation.json').read_text())
assert previous['original_matrix_unchanged_except_declared_cross_group_bounds']
def expr(e):return {e.getVar(i).VarName:e.getCoeff(i) for i in range(e.size())}
def linear(model):return {r.ConstrName:(r.Sense,r.RHS,expr(model.getRow(r))) for r in model.getConstrs()}
def indicators(model):
    result={}
    for r in model.getGenConstrs():
        flag,value,e,sense,rhs=model.getGenConstrIndicator(r)
        result[r.GenConstrName]=(flag.VarName,value,expr(e),sense,rhs)
    return result
results={}
for label in ('unconditional','maximum_b2_ten'):
    base=gp.read(str(HERE/'s1groups13'/(label+'.mps.gz')))
    changed=gp.read(str(HERE/'ceiling14'/(label+'.mps.gz')))
    source=json.loads((HERE/'ceiling14'/(label+'.json')).read_text())
    try:
        for attribute in ('VarName','LB','UB','VType','Obj'):
            assert base.getAttr(attribute,base.getVars())==changed.getAttr(attribute,changed.getVars())
        assert base.ModelSense==changed.ModelSense==gp.GRB.MINIMIZE and base.ObjCon==changed.ObjCon
        old,new=linear(base),linear(changed)
        assert all(new[name]==row for name,row in old.items())
        assert new.keys()-old.keys()=={'physical_objective_ceiling'}
        ceiling=source['objective_ceiling'];assert ceiling==451.87357953248807
        coefficients={v.VarName:v.Obj for v in base.getVars() if v.Obj}
        assert new['physical_objective_ceiling']==('<',ceiling-base.ObjCon,coefficients)
        assert indicators(base)==indicators(changed)
        assert not source['subproblem_infeasible'] and not source['proven_infeasible']
        replay=None
        if source['bound']>451.71429479447573+1e-6:
            changed.Params.Threads=1;changed.Params.Seed=42;changed.Params.DualReductions=0
            changed.Params.IntFeasTol=changed.Params.FeasibilityTol=1e-9
            changed.Params.MIPGap=changed.Params.MIPGapAbs=0;changed.Params.TimeLimit=90
            changed.optimize()
            if changed.Status==gp.GRB.INFEASIBLE:bound=ceiling
            else:bound=min(ceiling,float(changed.ObjBound))
            assert bound+1e-6>=source['bound']
            replay=dict(status=int(changed.Status),truncated_bound=bound,runtime=changed.Runtime)
        results[label]=dict(original_variables=base.NumVars,original_rows=base.NumConstrs,
            original_indicators_checked=base.NumGenConstrs,original_matrix_identical=True,
            one_exact_objective_ceiling_row_checked=True,conditional_lower_bound=source['bound'],
            inference_checked='Schedules removed by ceiling have cost greater than ceiling; retained ones satisfy restricted lower bound',
            independent_replay=replay,full_proven_optimal=False,conditional_infeasible_not_claimed=True)
    finally:base.dispose();changed.dispose()
result=dict(variants=results,source_hashes_checked=len(manifest),runtime=time.perf_counter()-started)
(HERE/'ceiling14/independent_validation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
