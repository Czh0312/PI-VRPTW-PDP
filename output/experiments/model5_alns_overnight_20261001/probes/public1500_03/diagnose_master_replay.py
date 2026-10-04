"""Post-result diagnostic only; no solver report or validation promotion."""
from pathlib import Path
import json,sys,time,hashlib
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot'));sys.path.insert(0,str(HERE))
import gurobipy as gp
import validation_core as check
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_conditional_inventory import build_conditional_inventory,set_conditional_projection
reg=check.read(HERE/'master_replay_diagnostic_registration.json')
assert check.sha(Path(__file__))==reg['script_sha256']
plan=check.read(HERE/'plan.json')
assert check.sha(HERE/'plan.json')==reg['plan_sha256']
p=load_instance(*load_routing_case(plan['case'],use_pool_config=False))
out=check.read(HERE/'seed53_public1500.json')['experiment'];ref=out['reference_certificate'];cert=out['conditional_certificate']['masters'][0]
floors={r['satellite']:r['bound'] for r in ref['components']}
base=HERE/'seed53_public1500_models/conditional/master_0.mps.gz'
answers=[]
for use_start in [False,True]:
 model,ctx=build_conditional_inventory(p,floors,[],e1_bound=ref['e1_bound'])
 loaded=gp.read(str(base))
 try:
  model.update();loaded.update()
  a={v.VarName:v for v in model.getVars()};b={v.VarName:v for v in loaded.getVars()};assert set(a)==set(b)
  for name,v in a.items():
   w=b[name];assert v.VType==w.VType;check.close(v.LB,w.LB);check.close(v.UB,w.UB);check.close(v.Obj,w.Obj)
  check.close(model.ObjCon,loaded.ObjCon)
  assert model.NumConstrs==loaded.NumConstrs and model.NumGenConstrs==loaded.NumGenConstrs
  for x,y in zip(model.getConstrs(),loaded.getConstrs()):
   assert x.Sense==y.Sense;check.close(x.RHS,y.RHS);check.same_expr(model.getRow(x),loaded.getRow(y))
  for x,y in zip(model.getGenConstrs(),loaded.getGenConstrs()):
   xv,xl,xe,xs,xr=model.getGenConstrIndicator(x);yv,yl,ye,ys,yr=loaded.getGenConstrIndicator(y)
   assert xv.VarName==yv.VarName and xl==yl and xs==ys;check.close(xr,yr);check.same_expr(xe,ye)
  if use_start:
   set_conditional_projection(p,ctx,out['result']);model.update()
   for name,v in a.items():
    if v.Start<gp.GRB.UNDEFINED:b[name].Start=v.Start
  for label,m in [('rebuilt',model),('exported',loaded)]:
   m.Params.OutputFlag=0;m.Params.Threads=1;m.Params.Seed=53;m.Params.MIPGap=m.Params.MIPGapAbs=0;m.Params.DualReductions=0;m.Params.IntFeasTol=m.Params.FeasibilityTol=1e-9;m.Params.TimeLimit=30;m.optimize()
   row=dict(label=label,physical_start=use_start,status=m.Status,raw_bound=m.ObjBound,objective=m.ObjVal if m.SolCount else None,fingerprint=m.Fingerprint,runtime=m.Runtime,reported_original=cert['raw_bound'],same_complete_matrix_verified=True)
   if m.SolCount:
    m.write(str(HERE/f'master_replay_{label}_start{int(use_start)}.sol'))
   answers.append(row);print(row,flush=True)
 finally:model.dispose();loaded.dispose()
(HERE/'master_replay_diagnostic.json').write_text(json.dumps(dict(status='DIAGNOSTIC_ONLY',cases=answers,original_claim_not_promoted=True),indent=2)+'\n')
