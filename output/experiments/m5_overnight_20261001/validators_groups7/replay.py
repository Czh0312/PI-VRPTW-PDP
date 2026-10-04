"""Independent group matrix checks and conditional MPS proof replay."""
from pathlib import Path
import hashlib
import json
import sys
import time

HERE=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(HERE/'source_snapshot_groups7'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
import gurobipy as gp

def expression(e):return {e.getVar(i).VarName:e.getCoeff(i) for i in range(e.size())}
def linear(model):return {r.ConstrName:(r.Sense,r.RHS,expression(model.getRow(r))) for r in model.getConstrs()}
def indicators(model):
    result={}
    for row in model.getGenConstrs():
        flag,value,e,sense,rhs=model.getGenConstrIndicator(row)
        result[row.GenConstrName]=(flag.VarName,value,expression(e),sense,rhs)
    return result
def optimize(model):
    model.Params.Threads=1;model.Params.TimeLimit=60
    model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9
    model.Params.MIPGap=model.Params.MIPGapAbs=0;model.Params.DualReductions=0
    model.optimize()

begun=time.perf_counter();root=HERE.parents[2]
manifest=json.loads((HERE/'manifest_groups7.json').read_text())['sha256']
assert all(hashlib.sha256((root/p).read_bytes()).hexdigest()==h for p,h in manifest.items())
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
groups=tuple(sorted(tuple(sorted(tuple(k) for k in group))
    for group in json.loads((HERE/'groups7/groups.json').read_text())))
membership={key:g for g,group in enumerate(groups) for key in group}
assert len(membership)==2*sum(o.satellite=='S3' for o in problem.orders)
schema=json.loads((HERE/'groups7/route_schema.json').read_text())
base=gp.read(str(HERE/'maximum6/physical/unconditional.mps.gz'))
grouped=gp.read(str(HERE/'groups7/unconditional.mps.gz'))
conditional=gp.read(str(HERE/'groups7/maximum_b2_zero.mps.gz'))
try:
    old_vars={v.VarName:v for v in base.getVars()}
    group_vars={v.VarName:v for v in grouped.getVars()}
    forced=set();new_rows={};new_vars=set()
    expected_nodes=[(o.id,k) for o in problem.orders if o.satellite=='S3' for k in ('d','p')]
    for i,route in enumerate(schema):
        label=f"e2_S3_{route['vehicle']}_{route['slot']}"
        assert route['label']==label and route['used']==label+'_used'
        assert [tuple(k) for k in route['nodes']]==expected_nodes
        assignments={g:f'physical_group_{i}_{g}' for g in range(len(groups))}
        new_vars.update(assignments.values())
        new_rows[f'physical_group_used_{i}']=('=',0,{route['used']:1,**{n:-1 for n in assignments.values()}})
        for j,key in enumerate(expected_nodes):
            visit=label+f'_visit[{j}]'
            assert route['visits'][str(j)]==visit
            new_rows[f'physical_group_visit_{i}_{j}']=('=',0,{visit:1,assignments[membership[key]]:-1})
        for arc in route['arcs']:
            a,b=arc['a'],arc['b']
            if a>=0 and b>=0 and membership[expected_nodes[a]]!=membership[expected_nodes[b]]:
                forced.add(arc['name'])
    for g in range(len(groups)):
        new_rows[f'physical_group_once_{g}']=('=',1,{f'physical_group_{i}_{g}':1 for i in range(len(schema))})
    assert group_vars.keys()-old_vars.keys()==new_vars
    for name,v in old_vars.items():
        other=group_vars[name]
        assert (v.LB,0 if name in forced else v.UB,v.VType,v.Obj)==(other.LB,other.UB,other.VType,other.Obj)
    for name in new_vars:
        v=group_vars[name];assert (v.LB,v.UB,v.VType,v.Obj)==(0,1,gp.GRB.BINARY,0)
    old_matrix=linear(base);group_matrix=linear(grouped)
    assert all(group_matrix[name]==row for name,row in old_matrix.items())
    assert group_matrix.keys()-old_matrix.keys()==new_rows.keys()
    assert all(group_matrix[name]==row for name,row in new_rows.items())
    assert indicators(base)==indicators(grouped)
    assert base.ObjCon==grouped.ObjCon==conditional.ObjCon
    condition_vars={v.VarName:v for v in conditional.getVars()}
    for name,v in group_vars.items():
        other=condition_vars[name]
        assert (v.LB,v.UB,v.VType,v.Obj)==(other.LB,other.UB,other.VType,other.Obj)
    condition_matrix=linear(conditional)
    assert all(condition_matrix[name]==row for name,row in group_matrix.items())
    cutoff=problem.batch_map['b2'].cutoff;added=set();expected_indicators={};counts={};constant=0
    for order in problem.orders:
        if order.satellite!='S3':continue
        returned=group_vars['pickup_return_'+order.id]
        if returned.UB<=cutoff:constant+=order.count
        elif returned.LB>cutoff:continue
        else:
            name=f'physical_early_{order.id}_b2';added.add(name);counts[name]=order.count
            v=condition_vars[name];assert (v.LB,v.UB,v.VType,v.Obj)==(0,1,gp.GRB.BINARY,0)
            expected_indicators[f'physical_early_yes_{order.id}_b2']=(name,1,{returned.VarName:1},'<',cutoff)
            expected_indicators[f'physical_early_no_{order.id}_b2']=(name,0,{returned.VarName:1},'>',cutoff+1)
    assert condition_vars.keys()-group_vars.keys()==added
    assert condition_matrix.keys()-group_matrix.keys()=={'physical_maximum_returns_b2'}
    assert condition_matrix['physical_maximum_returns_b2']==('<',-constant,counts)
    assert indicators(conditional)=={**indicators(grouped),**expected_indicators}
    optimize(grouped);assert grouped.Status==gp.GRB.OPTIMAL
    source=json.loads((HERE/'groups7/unconditional.json').read_text())
    assert grouped.ObjBound+1e-6>=source['bound']
    optimum=float(grouped.ObjVal)
    optimize(conditional);assert conditional.Status==gp.GRB.INFEASIBLE
    result=dict(source_hashes_checked=len(manifest),base_physical_variables=len(old_vars),
        group_assignment_variables=len(new_vars),group_rows_checked=len(new_rows),
        cross_group_arc_bounds_checked=len(forced),all_within_group_arcs_unchanged=True,
        original_matrix_unchanged_except_declared_cross_group_bounds=True,
        maximum_count_indicators_checked=len(expected_indicators),
        group_optimal_objective=optimum,conditional_infeasible_status=int(conditional.Status),
        certified_scope='M5_conditional_whole_task_group_physical_site',full_proven_optimal=False,
        runtime=time.perf_counter()-begun)
finally:base.dispose();grouped.dispose();conditional.dispose()
(HERE/'groups7/independent_validation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
