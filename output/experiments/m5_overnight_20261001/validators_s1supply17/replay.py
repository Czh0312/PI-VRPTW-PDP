from pathlib import Path
import json,hashlib,time
import gurobipy as gp
HERE=Path(__file__).resolve().parent.parent
started=time.perf_counter()
manifest=json.loads((HERE/'manifest_s1supply17_input.json').read_text())['sha256']
for path,digest in manifest.items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest
prior=json.loads((HERE/'s1full16/independent_validation.json').read_text())
assert prior['replays']['unconditional']['status']==2
floor=json.loads((HERE/'s1full16/unconditional.json').read_text())['bound']
def expr(e):return {e.getVar(i).VarName:e.getCoeff(i) for i in range(e.size())}
def matrix(model):return {r.ConstrName:(r.Sense,r.RHS,expr(model.getRow(r))) for r in model.getConstrs()}
def indicators(model):
    result={}
    for r in model.getGenConstrs():
        flag,value,e,sense,rhs=model.getGenConstrIndicator(r)
        result[r.GenConstrName]=(flag.VarName,value,expr(e),sense,rhs)
    return result
results={}
for name,thresholds in (('supply_b3',{'b3':59893}),('all_supplies',{'b1':31093,'b2':45493,'b3':59893})):
    base=gp.read(str(HERE/'s1full16/maximum_b2_ten.mps.gz'))
    changed=gp.read(str(HERE/'s1supply17'/(name+'.mps.gz')))
    source=json.loads((HERE/'s1supply17'/(name+'.json')).read_text())
    try:
        assert source['supply_thresholds']==thresholds and source['maximum_returns']=={'b2':10}
        assert [v.VarName for v in base.getVars()]==[v.VarName for v in changed.getVars()]
        modified=[]
        for old,new in zip(base.getVars(),changed.getVars()):
            lb=max(old.LB,thresholds.get(old.VarName.replace('delivery_S1_',''),old.LB)) if old.VarName.startswith('delivery_S1_') else old.LB
            assert (lb,old.UB,old.VType,old.Obj)==(new.LB,new.UB,new.VType,new.Obj)
            if new.LB!=old.LB:modified.append(new.VarName)
        assert base.ObjCon==changed.ObjCon and base.ModelSense==changed.ModelSense==gp.GRB.MINIMIZE
        old_rows,new_rows=matrix(base),matrix(changed)
        assert old_rows.keys()==new_rows.keys()
        expected=dict(old_rows)
        sense,rhs,terms=expected['physical_certified_cost_floor']
        expected['physical_certified_cost_floor']=(sense,floor,terms)
        assert new_rows==expected
        assert indicators(base)==indicators(changed)
        changed.Params.Threads=1;changed.Params.Seed=42;changed.Params.DualReductions=0
        changed.Params.IntFeasTol=changed.Params.FeasibilityTol=1e-9
        changed.Params.MIPGap=changed.Params.MIPGapAbs=0;changed.Params.TimeLimit=30
        changed.optimize()
        if source['subproblem_infeasible']:assert changed.Status==gp.GRB.INFEASIBLE
        else:assert changed.Status!=gp.GRB.INFEASIBLE and float(changed.ObjBound)+1e-6>=source['bound']
        results[name]=dict(original_matrix_except_certified_floor_identical=True,
            original_variables=base.NumVars,original_rows=base.NumConstrs,original_indicators_checked=base.NumGenConstrs,
            changed_supply_lower_bounds=modified,certified_conditional_floor=floor,
            status=int(changed.Status),raw_bound=float(changed.ObjBound) if changed.Status!=gp.GRB.INFEASIBLE else None,
            objective=float(changed.ObjVal) if changed.SolCount else None,
            new_condition_supported=True,full_proven_optimal=False,runtime=changed.Runtime)
    finally:base.dispose();changed.dispose()
result=dict(variants=results,source_hashes_checked=len(manifest),runtime=time.perf_counter()-started)
(HERE/'s1supply17/independent_validation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
