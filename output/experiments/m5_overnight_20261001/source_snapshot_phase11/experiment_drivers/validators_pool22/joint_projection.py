from pathlib import Path
import json,sys,math,time,hashlib
import gurobipy as gp
HERE=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(HERE/'source_snapshot_subset19'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_joint_inventory import set_joint_projection
from models.routing.solvers.exact.multibatch_physical_cuts import project_physical_costs
from warm_context import context_for_export
begun=time.perf_counter();out=HERE/'pool22/physical_proposals/validation'
certificate=json.loads((HERE/'global_lower_newpoint9_certificate.json').read_text())
master=Path(certificate['master']);assert hashlib.sha256(master.read_bytes()).hexdigest()==certificate['master_sha256']
problem=load_instance(*load_routing_case(str(HERE/'inputs/case.json')))
initial=json.loads((out/'reproduced_candidate.json').read_text());audit=audit_solution(problem,initial);assert audit['valid']
model=gp.read(str(master));context=context_for_export(problem,model,HERE/'inputs/master')
context['physical_costs']={s:model.getVarByName('physical_cost_'+s) for s in context['routes']}
context['physical_cover_rows']={s:model.getConstrByName('physical_cover_cost_'+s) for s in context['routes']}
context['e1']['supplies']={(site,bid):model.getVarByName(f'supply_{site}_{bid}') for (site,bid),count in problem.demands.items() if count}
set_joint_projection(problem,context,initial);project_physical_costs(problem,context,initial)
actual={frozenset((e['order_id'],e['kind']) for e in t['events']) for t in initial['e2_trips'] if t['satellite_id']=='S3'}
returned=sum(e['quantity'] for t in initial['e2_trips'] if t['satellite_id']=='S3' and t['return_complete']<=problem.batch_map['b2'].cutoff for e in t['events'] if e['kind']=='p')
triggers=[]
for folder,prefix in [('groups7','groups7_cut'),('partial8','partial8_cut'),('newpoint9','newpoint9_cut')]:
 groups=json.loads((HERE/folder/'maximum_b2_zero.json').read_text())['trip_groups'];flags=[]
 for i,group in enumerate(groups):
  value=int(frozenset(tuple(k) for k in group) in actual);flags.append(value);model.getVarByName(f'{prefix}_group_{i}').Start=value
 model.getVarByName(prefix+'_maximum_returns_b2').Start=int(returned==0)
 active=int(returned==0 and all(flags));model.getVarByName(prefix+'_active').Start=active
 triggers.append(dict(prefix=prefix,groups=flags,maximum_b2_zero=returned==0,active=active))
model.update();fixed=0
for var in model.getVars():
 value=var.Start
 if math.isfinite(value) and abs(value)<1e100:var.LB=var.UB=value;fixed+=1
model.Params.Threads=1;model.Params.TimeLimit=45
model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9;model.optimize()
assert model.Status==2 and abs(model.ObjVal-audit['objective'])<1e-6
model.write(str(out/'fixed_best_lower_master.mps.gz'))
result=dict(status=int(model.Status),objective=model.ObjVal,full_upper=audit['objective'],fixed_variables=fixed,
 actual_physical_epigraph_costs=True,all_existing_cut_triggers=triggers,
 best_lower_master_sha256=certificate['master_sha256'],full_upper_projects_to_certified_master=True,
 full_proven_optimal=False,runtime=time.perf_counter()-begun)
model.dispose();(out/'best_lower_projection.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
