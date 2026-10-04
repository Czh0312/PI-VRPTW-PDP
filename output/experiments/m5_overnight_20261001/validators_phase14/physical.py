"""Independent exact/partial groups relative to the frozen ungrouped physical model."""
from pathlib import Path
import sys,json,hashlib,time
import gurobipy as gp
HERE=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(HERE/'source_snapshot_phase14'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
started=time.perf_counter();name=sys.argv[1];out=HERE/name;cert=json.loads((out/'certificate_pending_validation.json').read_text());assert cert['status']==3 and cert['subproblem_infeasible']
freeze=json.loads((HERE/'manifest_site45_base.json').read_text())['sha256']
for f in [HERE/'site45/unconditional.mps.gz',HERE/'site45/route_schema.json']:
 assert hashlib.sha256(f.read_bytes()).hexdigest()==freeze[str(f)]
problem=load_instance(*load_routing_case(str(HERE/'medium25/0/inputs/case_relocated.json')))
groups=tuple(sorted(tuple(sorted(tuple(k) for k in g)) for g in cert['trip_groups']));partial=cert['partial_groups']
expected_nodes=[(o.id,k) for o in problem.orders if o.satellite=='S3' for k in ('d','p')];membership={k:i for i,g in enumerate(groups) for k in g}
assert len(membership)==sum(map(len,groups)) and set(membership)<=set(expected_nodes)
if not partial:assert set(membership)==set(expected_nodes)
base=gp.read(str(HERE/'site45/unconditional.mps.gz'));changed=gp.read(str(out/'physical.mps.gz'));schema=json.loads((HERE/'site45/route_schema.json').read_text())
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
  label=f"e2_S3_{r['vehicle']}_{r['slot']}";assert r['label']==label and [tuple(k) for k in r['nodes']]==expected_nodes
  assert 0<=r['vehicle']<problem.fleet.e2_per_sat_count and 0<=r['slot']<problem.operating_policy.e2_max_trips_per_vehicle
  assignments={g:f'physical_group_{i}_{g}' for g in range(len(groups))};expected_vars.update(assignments.values())
  expected_rows[f'physical_group_used_{i}']=('>' if partial else '=',0,{r['used']:1,**{v:-1 for v in assignments.values()}})
  for j,key in enumerate(expected_nodes):
   if key in membership:expected_rows[f'physical_group_visit_{i}_{j}']=('=',0,{r['visits'][str(j)]:1,assignments[membership[key]]:-1})
   else:expected_rows[f'physical_group_visit_{i}_{j}']=('<',0,{r['visits'][str(j)]:1,r['used']:-1,**{n:1 for n in assignments.values()}})
  for a in r['arcs']:
   if a['a']>=0 and a['b']>=0 and membership.get(expected_nodes[a['a']])!=membership.get(expected_nodes[a['b']]):forced.add(a['name'])
 for g in range(len(groups)):expected_rows[f'physical_group_once_{g}']=('=',1,{f'physical_group_{i}_{g}':1 for i in range(len(schema))})
 assert new.keys()-old.keys()==expected_vars
 for n,v in old.items():assert (v.LB,0 if n in forced else v.UB,v.VType,v.Obj)==(new[n].LB,new[n].UB,new[n].VType,new[n].Obj)
 for n in expected_vars:assert (new[n].LB,new[n].UB,new[n].VType,new[n].Obj)==(0,1,'B',0)
 a,b=linear(base),linear(changed);assert all(b[k]==v for k,v in a.items());assert {k:v for k,v in b.items() if k not in a}==expected_rows
 assert indicators(base)==indicators(changed) and base.ObjCon==changed.ObjCon
 changed.Params.Threads=1;changed.Params.Seed=43;changed.Params.TimeLimit=45
 changed.Params.IntFeasTol=changed.Params.FeasibilityTol=1e-9;changed.optimize();assert changed.Status==3
 proof=dict(base_variables=len(old),added_group_variables=len(expected_vars),group_rows_checked=len(expected_rows),
 cross_group_arc_bounds_checked=len(forced),all_original_attributes_and_rows_preserved_except_cross_group_bounds=True,
 all_within_group_orders_and_unassigned_task_paths_preserved=True,original_site_orders_vehicles_trip_limits_retained=True,
 conditional_infeasibility_independently_replayed=True,partial_groups=partial,declared_groups=len(groups),
 status=int(changed.Status),full_proven_infeasible=False,runtime=time.perf_counter()-started)
finally:base.dispose();changed.dispose()
(out/'independent_validation.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof,indent=2))
