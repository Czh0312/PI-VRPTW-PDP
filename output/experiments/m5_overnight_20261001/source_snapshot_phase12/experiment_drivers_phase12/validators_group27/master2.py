"""Independently decode group flags from original task-cover matrix rows."""
from pathlib import Path
from collections import defaultdict
import sys,json,hashlib,time,math
import gurobipy as gp
HERE=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(HERE/'source_snapshot_group28'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_joint_inventory import set_joint_projection
from warm_context import context_for_export
started=time.perf_counter();out=HERE/'group28'
root=HERE.parent/'m5_timed_integrality_20261001';directory=root/'verified_medium_180s/00_42_service_models';base_path=directory/'joint/joint_inventory.mps.gz'
report=json.loads((root/'validation.json').read_text());assert hashlib.sha256((root/'validation.json').read_bytes()).hexdigest()=='59c34db9a3e25c676c1d876a31a9fc2380e7f85e84c392d90e2760d233d05f98'
assert hashlib.sha256(base_path.read_bytes()).hexdigest()==report['artifact_sha256'][str(base_path.relative_to(root))]
assert json.loads((HERE/'group27/independent_validation.json').read_text())['independent_conditional_infeasibility_replay']
problem=load_instance(*load_routing_case(str(HERE/'medium25/0/inputs/case_relocated.json')))
certificate=json.loads((out/'certificate_pending_validation.json').read_text())
base=gp.read(str(base_path));changed=gp.read(str(out/'master.mps.gz'))
def expr(e):return {e.getVar(i).VarName:e.getCoeff(i) for i in range(e.size())}
def row(m,r):return r.Sense,r.RHS,expr(m.getRow(r))
def indicators(m):
 d={}
 for r in m.getGenConstrs():
  v,value,e,sense,rhs=m.getGenConstrIndicator(r);d[r.GenConstrName]=(v.VarName,value,sense,rhs,expr(e))
 return d
try:
 n,m=base.NumVars,base.NumConstrs;old,new=base.getVars(),changed.getVars()
 assert changed.ModelSense==base.ModelSense==gp.GRB.MINIMIZE and changed.ObjCon==base.ObjCon
 for attr in ['VarName','LB','UB','VType']:assert base.getAttr(attr,old)==changed.getAttr(attr,new[:n])
 for a,b in zip(base.getConstrs(),changed.getConstrs()[:m]):assert a.ConstrName==b.ConstrName and row(base,a)==row(changed,b)
 assert indicators(base)==indicators(changed)
 groups=json.loads((HERE/'group27/groups.json').read_text());costs={};masks={};expected_vars={};expected_rows={};flag_names=[]
 for si,site in enumerate(sorted(groups)):
  tasks=[(o.id,k) for o in problem.orders if o.satellite==site for k in ('d','p')];members=defaultdict(set)
  for ti,key in enumerate(tasks):
   r=base.getConstrByName(f'joint_cover_{si}_{ti}');assert r.Sense=='=' and r.RHS==1
   e=base.getRow(r)
   for j in range(e.size()):
    v=e.getVar(j);assert e.getCoeff(j)==1 and v.VarName.startswith(f'joint_route_{si}_');members[v.VarName].add(key)
  costs[site]={k:base.getVarByName(k).Obj for k in members};masks[site]=members
  theta='physical_cost_'+site;expected_vars[theta]=(0,math.inf,'C',1)
  expected_rows['physical_cover_cost_'+site]=('>',0,{theta:1,**{k:-v for k,v in costs[site].items()}})
  normalized=sorted(tuple(sorted(tuple(k) for k in g)) for g in groups[site]);flat=[k for g in normalized for k in g]
  assert len(set(flat))==len(flat) and set(flat)==set(tasks)
  for gi,group in enumerate(normalized):
   name=f'all_groups27_{site}_group_{gi}';flag_names.append(name);expected_vars[name]=(0,1,'B',0)
   matched={k:-1 for k,keys in members.items() if keys==set(group)}
   expected_rows[f'all_groups27_{site}_match_{gi}']=('=',0,{name:1,**matched})
 active='all_groups27_active';expected_vars[active]=(0,1,'B',0)
 for i,name in enumerate(flag_names):expected_rows[f'all_groups27_and_upper_{i}']=('<',0,{active:1,name:-1})
 expected_rows['all_groups27_and_lower']=('>',1-len(flag_names),{active:1,**{name:-1 for name in flag_names}})
 expected_rows['all_groups27_no_good']=('=',0,{active:1})
 observed={v.VarName:(v.LB,v.UB,v.VType,v.Obj) for v in new[n:]};assert observed==expected_vars
 observed={r.ConstrName:row(changed,r) for r in changed.getConstrs()[m:]};assert observed==expected_rows
 all_route={k for c in costs.values() for k in c}
 for a,b in zip(old,new[:n]):assert b.Obj==(0 if a.VarName in all_route else a.Obj)
 matrix=dict(original_variables=n,original_rows=m,original_indicators=base.NumGenConstrs,
  original_matrix_and_bounds_unchanged=True,original_objective_changes_only_route_cost_to_actual_site_epigraph=True,
  added_variables=len(expected_vars),added_rows_checked=len(expected_rows),all_flags_from_original_cover_rows=True,
  exact_AND_and_no_good_checked=True,full_proven_optimal=False)
 # Independent full incumbent flags/costs, fixed with all original schedule starts.
 initial=json.loads((HERE/'medium25/0/inputs/initial_result.json').read_text());audit=audit_solution(problem,initial);assert audit['valid']
 context=context_for_export(problem,changed,directory);set_joint_projection(problem,context,initial)
 actual={site:{frozenset((e['order_id'],e['kind']) for e in t['events']) for t in initial['e2_trips'] if t['satellite_id']==site} for site in groups};flags=[]
 for site in sorted(groups):
  distance=0.0
  for t in initial['e2_trips']:
   if t['satellite_id']!=site:continue
   places=[site]+[problem.order_map[e['order_id']].customer for e in t['events']]+[site]
   distance+=math.fsum(problem.distance(a,b) for a,b in zip(places,places[1:]))
  changed.getVarByName('physical_cost_'+site).Start=distance
  for gi,group in enumerate(sorted(tuple(sorted(tuple(k) for k in g)) for g in groups[site])):
   value=int(frozenset(group) in actual[site]);flags.append(value);changed.getVarByName(f'all_groups27_{site}_group_{gi}').Start=value
 changed.getVarByName(active).Start=int(all(flags));assert not all(flags)
 for v in changed.getVars():
  if math.isfinite(v.Start) and abs(v.Start)<1e100:v.LB=v.UB=v.Start
 changed.Params.Threads=1;changed.Params.TimeLimit=30;changed.Params.IntFeasTol=changed.Params.FeasibilityTol=1e-9;changed.optimize()
 assert changed.Status==2 and abs(changed.ObjVal-audit['objective'])<1e-6
 changed.write(str(out/'fixed_initial_projection.mps.gz'))
 projection=dict(status=int(changed.Status),objective=changed.ObjVal,upper=audit['objective'],active=0,full_calendar_projection=True)
finally:base.dispose();changed.dispose()
model=gp.read(str(out/'master.mps.gz'))
try:
 model.Params.Threads=1;model.Params.Seed=43;model.Params.TimeLimit=45
 model.Params.MIPGap=model.Params.MIPGapAbs=0;model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9;model.optimize()
 assert model.Status==2 and model.ObjBound+1e-6>=certificate['candidate_global_lower']
 replay=dict(status=int(model.Status),raw_bound=model.ObjBound,objective=model.ObjVal,candidate_supported=True,runtime=model.Runtime)
finally:model.dispose()
result=dict(matrix=matrix,projection=projection,replay=replay,
 candidate_global_lower=certificate['candidate_global_lower'],previous_global_lower=certificate['old_global_lower'],
 conditional_domain_and_infeasibility_independently_validated=True,promote_supported=True,
 full_proven_optimal=False,full_proven_infeasible=False,runtime=time.perf_counter()-started)
(out/'independent_validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
