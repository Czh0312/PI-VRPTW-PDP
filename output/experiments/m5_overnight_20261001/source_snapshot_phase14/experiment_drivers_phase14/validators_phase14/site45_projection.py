from pathlib import Path
from dataclasses import replace
import sys,json,time
import gurobipy as gp
H=Path(__file__).resolve().parent.parent;sys.path.insert(0,str(H/'source_snapshot_phase14'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance,MultiBatchInstance
from models.routing.solvers.exact.formulation import build_model
start=time.perf_counter();p=load_instance(*load_routing_case(str(H/'medium25/0/inputs/case_relocated.json')));orders=tuple(o for o in p.orders if o.satellite=='S3');total=sum(o.count for o in orders)
local=MultiBatchInstance(p.network,orders,replace(p.operating_policy,container_pool_size=max(p.operating_policy.container_pool_size,total)),model=5)
original,ctx=build_model(local,e2_capacity_cuts=False,compress_multibatch_routes=True);base=gp.read(str(H/'site45/unconditional.mps.gz'))
def exp(e):return {e.getVar(i).VarName:e.getCoeff(i) for i in range(e.size())}
def ind(m,r):
 v,val,e,sense,rhs=m.getGenConstrIndicator(r);return v.VarName,val,sense,rhs,exp(e)
try:
 removed=set()
 def collect(x):
  if isinstance(x,gp.Var):removed.add(x.VarName)
  elif isinstance(x,gp.LinExpr):
   for i in range(x.size()):collect(x.getVar(i))
  elif isinstance(x,dict):
   for y in x.values():collect(y)
  elif isinstance(x,(list,tuple)):
   for y in x:collect(y)
 for route in ctx['e1']:collect(route)
 prefixes=tuple(r['label']+'_rank[' for r in ctx['e1'])
 for v in original.getVars():
  if v.VarName.startswith(prefixes):removed.add(v.VarName)
 expected={v.VarName:(v.LB,v.UB,v.VType,v.Obj) for v in original.getVars() if v.VarName not in removed}
 assert expected=={v.VarName:(v.LB,v.UB,v.VType,v.Obj) for v in base.getVars()}
 kept,deleted={},[]
 for r in original.getConstrs():
  e=exp(original.getRow(r))
  if set(e)&removed:deleted.append(r.ConstrName)
  else:kept[r.ConstrName]=(r.Sense,r.RHS,e)
 assert kept=={r.ConstrName:(r.Sense,r.RHS,exp(base.getRow(r))) for r in base.getConstrs()}
 kept_ind,deleted_ind={},[]
 for r in original.getGenConstrs():
  item=ind(original,r)
  if item[0] in removed or set(item[-1])&removed:deleted_ind.append(r.GenConstrName)
  else:kept_ind[r.GenConstrName]=item
 assert kept_ind=={r.GenConstrName:ind(base,r) for r in base.getGenConstrs()}
 assert original.ObjCon==base.ObjCon and original.ModelSense==base.ModelSense
 proof=json.loads((H/'site45/projection_proof.json').read_text());assert removed==set(proof['removed_variables']) and set(deleted)==set(proof['removed_linear_rows']) and set(deleted_ind)==set(proof['removed_indicators'])
 assert proof['original_pool']==p.operating_policy.container_pool_size and proof['local_relaxed_pool']==local.operating_policy.container_pool_size>=total
 original.write(str(H/'site45/original_local_full.mps.gz'))
 r=dict(original_full_local_variables=original.NumVars,remaining_variables=base.NumVars,all_remaining_original_attributes_rows_indicators_objective_checked=True,only_E1_variables_and_touching_rows_removed=True,original_network_fleet_order_rules_retained=True,container_pool_relaxed_not_restricted=True,site='S3',orders=len(orders),local_container_demand=total,original_container_pool=p.operating_policy.container_pool_size,local_container_pool=local.operating_policy.container_pool_size,full_global_infeasibility_claim=False,runtime=time.perf_counter()-start)
finally:original.dispose();base.dispose()
(H/'site45/original_projection_validation.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
