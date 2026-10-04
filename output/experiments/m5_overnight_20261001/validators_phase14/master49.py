from pathlib import Path
from collections import defaultdict
import sys,json,math,time,hashlib
import gurobipy as gp
HERE=Path(__file__).resolve().parent.parent;sys.path.insert(0,str(HERE/'source_snapshot_phase14'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_joint_inventory import set_joint_projection
from warm_context import context_for_export
started=time.perf_counter();out=HERE/'master49';cert=json.loads((out/'certificate_pending_validation.json').read_text())
assert json.loads((HERE/'master48/independent_validation.json').read_text())['promote_supported']
assert json.loads((HERE/'core47/independent_validation.json').read_text())['conditional_infeasibility_independently_replayed']
p=load_instance(*load_routing_case(str(HERE/'medium25/0/inputs/case_relocated.json')))
groups=json.loads((HERE/'group43/outcome.json').read_text())['certificate']['groups']
base=gp.read(str(HERE/'master48/master.mps.gz'));model=gp.read(str(out/'master.mps.gz'))
def expression(e):return {e.getVar(i).VarName:e.getCoeff(i) for i in range(e.size())}
def row(m,r):return r.Sense,r.RHS,expression(m.getRow(r))
def indicators(m):
 d={}
 for r in m.getGenConstrs():
  v,val,e,sense,rhs=m.getGenConstrIndicator(r);d[r.GenConstrName]=(v.VarName,val,sense,rhs,expression(e))
 return d
try:
 n,m=base.NumVars,base.NumConstrs;a,b=base.getVars(),model.getVars()
 allchoices=set();tasks_checked=0
 for si,site in enumerate(sorted(groups)):
  tasks=[(o.id,k) for o in p.orders if o.satellite==site for k in ('d','p')]
  for i,key in enumerate(tasks):
   r=base.getConstrByName(f'joint_cover_{si}_{i}');assert r.Sense=='=' and r.RHS==1;e=base.getRow(r)
   for j in range(e.size()):assert e.getCoeff(j)==1;allchoices.add(e.getVar(j).VarName)
   tasks_checked+=1
 assert len(allchoices)==7268 and allchoices==set(json.loads((out/'integer_variables.json').read_text()))
 assert base.NumVars==model.NumVars and base.NumConstrs==model.NumConstrs
 for x,y in zip(a,b):
  assert x.VarName==y.VarName and (x.LB,x.UB,x.Obj)==(y.LB,y.UB,y.Obj)
  assert (x.VType=='C' and y.VType=='B') if x.VarName in allchoices else x.VType==y.VType
 assert base.ObjCon==model.ObjCon and base.ModelSense==model.ModelSense and indicators(base)==indicators(model)
 for x,y in zip(base.getConstrs(),model.getConstrs()):assert x.ConstrName==y.ConstrName and row(base,x)==row(model,y)
 matrix=dict(original_variables=n,original_rows=m,all_original_rows_objective_indicators_and_bounds_unchanged=True,only_complete_cover_choice_types_changed=True,binary_choices_checked=len(allchoices),original_task_cover_rows_checked=tasks_checked,complete_domain_preserved=True)
 local=json.loads((HERE/'core47/certificate_pending_validation.json').read_text())['trip_groups']
 initial=json.loads((HERE/'medium25/0/inputs/initial_result.json').read_text());audit=audit_solution(p,initial);assert audit['valid']
 directory=HERE.parent/'m5_timed_integrality_20261001/verified_medium_180s/00_42_service_models';ctx=context_for_export(p,model,directory);set_joint_projection(p,ctx,initial)
 actual={s:{frozenset((e['order_id'],e['kind']) for e in t['events']) for t in initial['e2_trips'] if t['satellite_id']==s} for s in groups}
 for site in groups:
  total=0
  for t in initial['e2_trips']:
   if t['satellite_id']!=site:continue
   places=[site]+[p.order_map[e['order_id']].customer for e in t['events']]+[site];total+=math.fsum(p.distance(x,y) for x,y in zip(places,places[1:]))
  model.getVarByName('physical_cost_'+site).Start=total
 for prefix,folder in [('all_groups27','group27'),('all_groups43','group43')]:
  gs=json.loads((HERE/folder/'groups.json').read_text());flags=[]
  for site in sorted(gs):
   for i,g in enumerate(sorted(tuple(sorted(tuple(k) for k in g)) for g in gs[site])):
    flag=int(frozenset(g) in actual[site]);flags.append(flag);model.getVarByName(f'{prefix}_{site}_group_{i}').Start=flag
  model.getVarByName(prefix+'_active').Start=int(all(flags))
 for prefix,folder in [('site29_cut','site29'),('core32_cut','core32'),('core35_cut','core35'),('core38_cut','core38'),('site40_cut','site40')]:
  gs=json.loads((HERE/folder/'certificate_pending_validation.json').read_text())['trip_groups'];flags=[]
  for i,g in enumerate(gs):
   flag=int(frozenset(tuple(k) for k in g) in actual['S1']);flags.append(flag);model.getVarByName(f'{prefix}_group_{i}').Start=flag
  model.getVarByName(prefix+'_active').Start=int(all(flags))
 model.getVarByName('core47_cut_group_0').Start=int(frozenset(tuple(k) for k in local[0]) in actual['S3'])
 model.update();fixed=0
 for v in model.getVars():
  if math.isfinite(v.Start) and abs(v.Start)<1e100:v.LB=v.UB=v.Start;fixed+=1
 assert fixed>0;model.Params.Threads=1;model.Params.TimeLimit=30;model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9;model.optimize()
 assert model.Status==2 and abs(model.ObjVal-audit['objective'])<1e-6
 model.write(str(out/'fixed_initial_projection.mps.gz'));projection=dict(complete_initial_projects=True,fixed_variables=fixed,status=int(model.Status),objective=model.ObjVal,upper=audit['objective'])
finally:base.dispose();model.dispose()
model=gp.read(str(out/'master.mps.gz'))
try:
 model.Params.Threads=1;model.Params.Seed=43;model.Params.TimeLimit=45;model.Params.MIPGap=model.Params.MIPGapAbs=0;model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9;model.optimize()
 assert model.Status==2 and model.ObjBound+1e-6>=cert['candidate_global_lower']
 replay=dict(status=int(model.Status),raw_bound=model.ObjBound,objective=model.ObjVal,candidate_supported=True,runtime=model.Runtime)
finally:model.dispose()
r=dict(matrix=matrix,projection=projection,replay=replay,candidate_global_lower=cert['candidate_global_lower'],previous_global_lower=cert['old_global_lower'],promote_supported=True,full_proven_optimal=False,full_proven_infeasible=False,runtime=time.perf_counter()-started)
(out/'independent_validation.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
