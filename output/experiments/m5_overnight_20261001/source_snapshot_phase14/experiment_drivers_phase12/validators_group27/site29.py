from pathlib import Path
import sys,json,time,math
import gurobipy as gp
HERE=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(HERE/'source_snapshot_group28'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_physical_site import build_physical_site
started=time.perf_counter();out=HERE/'site29';problem=load_instance(*load_routing_case(str(HERE/'medium25/0/inputs/case_relocated.json')))
cert=json.loads((out/'certificate_pending_validation.json').read_text());assert cert['status']==3 and cert['subproblem_infeasible']
groups=tuple(sorted(tuple(sorted(tuple(k) for k in g)) for g in cert['trip_groups']))
expected_nodes=[(o.id,k) for o in problem.orders if o.satellite=='S1' for k in ('d','p')];membership={key:i for i,g in enumerate(groups) for key in g}
assert len(membership)==len(expected_nodes) and set(membership)==set(expected_nodes)
base,ctx=build_physical_site(problem,'S1',compress_routes=True)
base.write(str(out/'unconditional.mps.gz'))
schema=[dict(label=r['label'],vehicle=r['vehicle'],slot=r['slot'],used=r['used'].VarName,
 nodes=[(n['order'].id,n['kind']) for n in r['nodes']],visits={j:v.VarName for j,v in r['visit'].items()},
 arcs=[dict(a=a,b=b,name=v.VarName) for (a,b),v in r['x'].items()]) for r in ctx['e2']]
(out/'route_schema.json').write_text(json.dumps(schema,indent=2)+'\n')
changed=gp.read(str(out/'physical.mps.gz'))
def expression(e):return {e.getVar(i).VarName:e.getCoeff(i) for i in range(e.size())}
def linear(m):return {r.ConstrName:(r.Sense,r.RHS,expression(m.getRow(r))) for r in m.getConstrs()}
def indicators(m):
 d={}
 for r in m.getGenConstrs():
  v,value,e,sense,rhs=m.getGenConstrIndicator(r);d[r.GenConstrName]=(v.VarName,value,sense,rhs,expression(e))
 return d
try:
 old={v.VarName:v for v in base.getVars()};new={v.VarName:v for v in changed.getVars()};expected_vars=set();expected_rows={};forced=set()
 for i,r in enumerate(schema):
  label=f"e2_S1_{r['vehicle']}_{r['slot']}";assert r['label']==label and r['nodes']==expected_nodes
  assert 0<=r['vehicle']<problem.fleet.e2_per_sat_count and 0<=r['slot']<problem.operating_policy.e2_max_trips_per_vehicle
  assignments={g:f'physical_group_{i}_{g}' for g in range(len(groups))};expected_vars.update(assignments.values())
  expected_rows[f'physical_group_used_{i}']=('=',0,{r['used']:1,**{v:-1 for v in assignments.values()}})
  for j,key in enumerate(expected_nodes):expected_rows[f'physical_group_visit_{i}_{j}']=('=',0,{r['visits'][j]:1,assignments[membership[key]]:-1})
  for a in r['arcs']:
   if a['a']>=0 and a['b']>=0 and membership[expected_nodes[a['a']]]!=membership[expected_nodes[a['b']]]:forced.add(a['name'])
 for g in range(len(groups)):expected_rows[f'physical_group_once_{g}']=('=',1,{f'physical_group_{i}_{g}':1 for i in range(len(schema))})
 assert new.keys()-old.keys()==expected_vars
 for name,v in old.items():assert (v.LB,0 if name in forced else v.UB,v.VType,v.Obj)==(new[name].LB,new[name].UB,new[name].VType,new[name].Obj)
 for name in expected_vars:assert (new[name].LB,new[name].UB,new[name].VType,new[name].Obj)==(0,1,'B',0)
 a,b=linear(base),linear(changed);assert all(b[k]==v for k,v in a.items());assert {k:v for k,v in b.items() if k not in a}==expected_rows
 assert indicators(base)==indicators(changed) and base.ObjCon==changed.ObjCon
 changed.Params.Threads=1;changed.Params.Seed=43;changed.Params.TimeLimit=45
 changed.Params.IntFeasTol=changed.Params.FeasibilityTol=1e-9;changed.optimize();assert changed.Status==3
 proof=dict(base_variables=len(old),added_group_variables=len(expected_vars),group_rows_checked=len(expected_rows),
  cross_group_arc_bounds_checked=len(forced),all_original_variables_matrix_and_indicators_unchanged_except_declared_cross_group_bounds=True,
  all_within_group_orders_retained=True,complete_original_physical_vehicle_and_trip_limits=True,
  E1_removed_and_local_pool_not_restricted=True,independent_conditional_infeasibility_replay=True,
  status=int(changed.Status),full_proven_infeasible=False,runtime=time.perf_counter()-started)
finally:base.dispose();changed.dispose()
(out/'independent_validation.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof,indent=2))
