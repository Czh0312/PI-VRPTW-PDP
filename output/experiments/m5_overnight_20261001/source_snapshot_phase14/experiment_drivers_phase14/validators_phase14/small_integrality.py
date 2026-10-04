from pathlib import Path
import sys,json,math,time,hashlib
import gurobipy as gp
H=Path(__file__).resolve().parent.parent;sys.path.insert(0,str(H/'source_snapshot_phase14'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_joint_inventory import set_joint_projection
from warm_context import context_for_export
D=H.parent/'m5_timed_integrality_20261001/verified_small_5s';jobs=[r['job'] for r in json.loads((D/'summary.json').read_text()) if r['job']['variant']=='timed'];out=H/'small_integrality52';out.mkdir();results=[];start=time.perf_counter()
for job in jobs:
 folder=out/job['id'];folder.mkdir();p=load_instance(*load_routing_case(job['case']));initial=json.loads(Path(job['initial_result']).read_text());a=audit_solution(p,initial);assert a['valid'] and initial['info']['proven_optimal'] and abs(a['objective']-job['frozen_upper_bound'])<1e-6
 directory=D/(job['id']+'_models');source=directory/'joint/joint_inventory.mps.gz';model=gp.read(str(source));ctx=context_for_export(p,model,directory)
 try:
  names={v.VarName for data in ctx['routes'].values() for v in data['choices'].values()};attrs={v.VarName:(v.LB,v.UB,v.VType,v.Obj) for v in model.getVars()}
  cover_names=set()
  for r in model.getConstrs():
   if r.ConstrName.startswith('joint_cover_'):
    assert r.Sense=='=' and r.RHS==1;e=model.getRow(r)
    for i in range(e.size()):assert e.getCoeff(i)==1;cover_names.add(e.getVar(i).VarName)
  assert cover_names==names
  for name in names:v=model.getVarByName(name);assert v.LB==0 and v.UB==1;v.VType='B'
  model.update();assert {v.VarName:(v.LB,v.UB,v.VType,v.Obj) for v in model.getVars()}=={n:(lb,ub,'B' if n in names else typ,cost) for n,(lb,ub,typ,cost) in attrs.items()}
  model.write(str(folder/'integer_master.mps.gz'));model.Params.Threads=1;model.Params.Seed=43;model.Params.TimeLimit=5;model.Params.MIPGap=model.Params.MIPGapAbs=0;model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9;model.optimize();assert model.Status==2 and model.ObjBound<=a['objective']+1e-6;bound=model.ObjBound
  set_joint_projection(p,ctx,initial);model.update();fixed=0
  for v in model.getVars():
   if math.isfinite(v.Start) and abs(v.Start)<1e100:
    if v.VarName in names:assert v.Start in (0,1)
    v.LB=v.UB=v.Start;fixed+=1
  assert fixed>0;model.optimize();assert model.Status==2 and model.ObjVal<=a['objective']+1e-6
  model.write(str(folder/'known_optimal_projection.mps.gz'));results.append(dict(case=job['id'],known_full_optimum=a['objective'],raw_joint_integer_bound=bound,full_optimum_projection_objective=model.ObjVal,fixed_variables=fixed,route_choices_binary=len(names),safe_bound_and_full_projection=True,input_sha256={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in [source,Path(job['case']),Path(job['initial_result'])]},full_proven_optimal=False))
 finally:model.dispose()
(H/'small_integrality52/validation.json').write_text(json.dumps(dict(known_independent_full_optimal_cases=len(results),results=results,all_passed=True,runtime=time.perf_counter()-start),indent=2)+'\n');print('validated',len(results),'known full optima and integer full projections')
