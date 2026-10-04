from pathlib import Path
from collections import defaultdict
import sys,json,time,math,hashlib
import gurobipy as gp
HERE=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(HERE/'source_snapshot_phase13'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_joint_inventory import set_joint_projection
from warm_context import context_for_export
started=time.perf_counter();out=HERE/'master36'
assert json.loads((HERE/'master33/independent_validation.json').read_text())['promote_supported']
assert json.loads((HERE/'core35/independent_validation.json').read_text())['conditional_infeasibility_independently_replayed']
problem=load_instance(*load_routing_case(str(HERE/'medium25/0/inputs/case_relocated.json')))
groups=json.loads((HERE/'group27/groups.json').read_text());oldlocal=sorted(tuple(sorted(tuple(k) for k in g)) for g in groups['S1']);local=sorted(tuple(sorted(tuple(k) for k in g)) for g in __import__('json').loads((HERE/'core35/certificate_pending_validation.json').read_text())['trip_groups'])
certificate=json.loads((out/'certificate_pending_validation.json').read_text());base=gp.read(str(HERE/'master33/master.mps.gz'));model=gp.read(str(out/'master.mps.gz'))
def expression(e):return {e.getVar(i).VarName:e.getCoeff(i) for i in range(e.size())}
def row(m,r):return r.Sense,r.RHS,expression(m.getRow(r))
def indicators(m):
 d={}
 for r in m.getGenConstrs():
  v,value,e,sense,rhs=m.getGenConstrIndicator(r);d[r.GenConstrName]=(v.VarName,value,sense,rhs,expression(e))
 return d
try:
 n,m=base.NumVars,base.NumConstrs;a,b=base.getVars(),model.getVars()
 for attr in ['VarName','LB','UB','VType','Obj']:assert base.getAttr(attr,a)==model.getAttr(attr,b[:n])
 assert base.ObjCon==model.ObjCon and base.ModelSense==model.ModelSense
 for x,y in zip(base.getConstrs(),model.getConstrs()[:m]):assert x.ConstrName==y.ConstrName and row(base,x)==row(model,y)
 assert indicators(base)==indicators(model)
 tasks=[(o.id,k) for o in problem.orders if o.satellite=='S1' for k in ('d','p')];members=defaultdict(set)
 for i,key in enumerate(tasks):
  r=base.getConstrByName(f'joint_cover_0_{i}');assert r.Sense=='=' and r.RHS==1;e=base.getRow(r)
  for j in range(e.size()):assert e.getCoeff(j)==1;members[e.getVar(j).VarName].add(key)
 expected_vars={};expected_rows={};names=[]
 for i,g in enumerate(local):
  name=f'core35_cut_group_{i}';names.append(name);expected_vars[name]=(0,1,'B',0)
  expected_rows[f'core35_cut_group_match_{i}']=('=',0,{name:1,**{v:-1 for v,k in members.items() if k==set(g)}})
 active='core35_cut_active';expected_vars[active]=(0,1,'B',0)
 for i,name in enumerate(names):expected_rows[f'core35_cut_and_upper_{i}']=('<',0,{active:1,name:-1})
 expected_rows['core35_cut_and_lower']=('>',1-len(names),{active:1,**{name:-1 for name in names}})
 expected_rows['core35_cut_no_good']=('<',0,{active:1})
 assert {v.VarName:(v.LB,v.UB,v.VType,v.Obj) for v in b[n:]}==expected_vars
 assert {r.ConstrName:row(model,r) for r in model.getConstrs()[m:]}==expected_rows
 matrix=dict(original_master33_variables=n,original_master33_rows=m,all_original_attributes_rows_objective_indicators_unchanged=True,
  added_variables=len(expected_vars),added_rows_checked=len(expected_rows),group_flags_decoded_from_original_task_cover_rows=True,
  exact_AND_and_local_no_good_checked=True)
 initial=json.loads((HERE/'medium25/0/inputs/initial_result.json').read_text());audit=audit_solution(problem,initial);assert audit['valid']
 directory=HERE.parent/'m5_timed_integrality_20261001/verified_medium_180s/00_42_service_models'
 context=context_for_export(problem,model,directory);set_joint_projection(problem,context,initial)
 actual={s:{frozenset((e['order_id'],e['kind']) for e in t['events']) for t in initial['e2_trips'] if t['satellite_id']==s} for s in groups};allflags=[];localflags=[]
 for s in sorted(groups):
  total=0.0
  for t in initial['e2_trips']:
   if t['satellite_id']!=s:continue
   places=[s]+[problem.order_map[e['order_id']].customer for e in t['events']]+[s]
   total+=math.fsum(problem.distance(x,y) for x,y in zip(places,places[1:]))
  model.getVarByName('physical_cost_'+s).Start=total
  for i,g in enumerate(sorted(tuple(sorted(tuple(k) for k in g)) for g in groups[s])):
   value=int(frozenset(g) in actual[s]);allflags.append(value);model.getVarByName(f'all_groups27_{s}_group_{i}').Start=value
   if s=='S1':model.getVarByName(f'site29_cut_group_{i}').Start=value
 model.getVarByName('all_groups27_active').Start=int(all(allflags))
 model.getVarByName('site29_cut_active').Start=int(all(frozenset(g) in actual['S1'] for g in oldlocal))
 for i,g in enumerate(local):
  value=int(frozenset(g) in actual['S1']);localflags.append(value);model.getVarByName(f'core35_cut_group_{i}').Start=value
 model.getVarByName(active).Start=int(all(localflags));assert not all(localflags)
 oldcore=json.loads((HERE/'core32/certificate_pending_validation.json').read_text())['trip_groups'];oldcoreflags=[]
 for i,g in enumerate(oldcore):
  value=int(frozenset(tuple(k) for k in g) in actual['S1']);oldcoreflags.append(value);model.getVarByName(f'core32_cut_group_{i}').Start=value
 model.getVarByName('core32_cut_active').Start=int(all(oldcoreflags))
 model.update();fixed=0
 for v in model.getVars():
  if math.isfinite(v.Start) and abs(v.Start)<1e100:v.LB=v.UB=v.Start;fixed+=1
 assert fixed>0;model.Params.Threads=1;model.Params.TimeLimit=30;model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9;model.optimize()
 assert model.Status==2 and abs(model.ObjVal-audit['objective'])<1e-6
 model.write(str(out/'fixed_initial_projection.mps.gz'))
 projection=dict(status=int(model.Status),objective=model.ObjVal,upper=audit['objective'],local_active=0,all_site_active=0,fixed_variables=fixed,complete_initial_projects=True)
finally:base.dispose();model.dispose()
model=gp.read(str(out/'master.mps.gz'))
try:
 model.Params.Threads=1;model.Params.Seed=43;model.Params.TimeLimit=45;model.Params.MIPGap=model.Params.MIPGapAbs=0
 model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9;model.optimize()
 assert model.Status==2 and model.ObjBound+1e-6>=certificate['candidate_global_lower']
 replay=dict(status=int(model.Status),raw_bound=model.ObjBound,objective=model.ObjVal,candidate_supported=True,runtime=model.Runtime)
finally:model.dispose()
result=dict(matrix=matrix,projection=projection,replay=replay,candidate_global_lower=certificate['candidate_global_lower'],
 previous_global_lower=certificate['old_global_lower'],all_conditional_proofs_and_matrix_checked=True,promote_supported=True,
 full_proven_optimal=False,full_proven_infeasible=False,runtime=time.perf_counter()-started)
(out/'independent_validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
