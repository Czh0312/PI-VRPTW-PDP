"""Compare exported conditional matrices against the unchanged physical base."""
from pathlib import Path
import hashlib
import json
import sys
import time

HERE=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(HERE/'source_snapshot_maximum6'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
import gurobipy as gp

def expression(row):
    return {row.getVar(i).VarName:row.getCoeff(i) for i in range(row.size())}

def indicator(model,row):
    flag,value,e,sense,rhs=model.getGenConstrIndicator(row)
    return flag.VarName,value,expression(e),sense,rhs

begun=time.perf_counter()
root=HERE.parents[2]
manifest=json.loads((HERE/'manifest_maximum6.json').read_text())['sha256']
assert all(hashlib.sha256((root/p).read_bytes()).hexdigest()==digest for p,digest in manifest.items())
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
best=json.loads((HERE/'best_full_result.json').read_text())
audit=audit_solution(problem,best);assert audit['valid']
certificate=json.loads((HERE/'combined_certificate.json').read_text())
assert hashlib.sha256((HERE/'best_full_result.json').read_bytes()).hexdigest()==certificate['upper_sha256']
assert abs(audit['objective']-certificate['upper_bound'])<1e-8

folder=HERE/'maximum6/physical'
base=gp.read(str(folder/'unconditional.mps.gz'))
conditional=gp.read(str(folder/'maximum_b2_zero.mps.gz'))
try:
    base_vars={v.VarName:v for v in base.getVars()}
    new_vars={v.VarName:v for v in conditional.getVars()}
    for name,v in base_vars.items():
        other=new_vars[name]
        assert (v.LB,v.UB,v.VType,v.Obj)==(other.LB,other.UB,other.VType,other.Obj)
    assert base.ObjCon==conditional.ObjCon and base.ModelSense==conditional.ModelSense
    old_rows={r.ConstrName:r for r in base.getConstrs()}
    new_rows={r.ConstrName:r for r in conditional.getConstrs()}
    for name,row in old_rows.items():
        other=new_rows[name]
        assert (row.Sense,row.RHS,expression(base.getRow(row)))==(
            other.Sense,other.RHS,expression(conditional.getRow(other)))
    old_indicators={r.GenConstrName:indicator(base,r) for r in base.getGenConstrs()}
    new_indicators={r.GenConstrName:indicator(conditional,r) for r in conditional.getGenConstrs()}
    assert all(new_indicators[name]==value for name,value in old_indicators.items())
    cutoff=problem.batch_map['b2'].cutoff
    coefficients={};constant=0;expected_variables=set();expected_indicators={}
    for order in problem.orders:
        if order.satellite!='S3':continue
        # Original formulation's pickup_returns variable uses this stable name.
        returned=base.getVarByName('pickup_return_'+order.id)
        assert returned is not None and returned.VType==gp.GRB.INTEGER
        if returned.UB<=cutoff:constant+=order.count
        elif returned.LB>cutoff:continue
        else:
            name=f'physical_early_{order.id}_b2'
            expected_variables.add(name);coefficients[name]=order.count
            var=new_vars[name]
            assert (var.LB,var.UB,var.VType,var.Obj)==(0,1,gp.GRB.BINARY,0)
            expected_indicators[f'physical_early_yes_{order.id}_b2']=(name,1,{returned.VarName:1},'<',cutoff)
            expected_indicators[f'physical_early_no_{order.id}_b2']=(name,0,{returned.VarName:1},'>',cutoff+1)
    assert new_vars.keys()-base_vars.keys()==expected_variables
    assert new_indicators.keys()-old_indicators.keys()==expected_indicators.keys()
    assert all(new_indicators[name]==value for name,value in expected_indicators.items())
    assert new_rows.keys()-old_rows.keys()=={'physical_maximum_returns_b2'}
    row=new_rows['physical_maximum_returns_b2']
    assert row.Sense=='<' and row.RHS==-constant
    assert expression(conditional.getRow(row))==coefficients
    checked=dict(original_variables=len(base_vars),original_rows=len(old_rows),
        original_indicators=len(old_indicators),added_variables=len(expected_variables),
        added_exact_indicators=len(expected_indicators),added_count_rows=1,
        base_matrix_identical=True,both_classification_directions_checked=True)
finally:
    base.dispose();conditional.dispose()

fixed=[]
projections=json.loads((HERE/'maximum6/projections/summary.json').read_text())
for proof in projections:
    model=gp.read(str(HERE/'maximum6/projections'/('fixed_'+proof['satellite']+'.mps.gz')))
    try:
        model.Params.Threads=1;model.Params.TimeLimit=30
        model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9
        model.optimize();assert model.Status==gp.GRB.OPTIMAL
        assert abs(model.ObjVal-proof['original_site_cost'])<1e-6
        fixed.append(dict(satellite=proof['satellite'],objective=float(model.ObjVal),status=int(model.Status)))
    finally:model.dispose()
result=dict(manifest_hashes_verified=len(manifest),best_full_audit=True,
    lower_unchanged=certificate['lower_bound'],upper_unchanged=audit['objective'],
    conditional_matrix=checked,fixed_projection_replays=fixed,
    conditional_scope_only=True,full_proven_optimal=False,runtime=time.perf_counter()-begun)
(HERE/'maximum6/independent_validation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
