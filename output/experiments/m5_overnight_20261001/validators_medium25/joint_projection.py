from pathlib import Path
import sys,json,time,math,hashlib
import gurobipy as gp
HERE=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(HERE/'source_snapshot_medium25'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_joint_inventory import set_joint_projection
from warm_context import context_for_export
begun=time.perf_counter();out=HERE/'medium25/1/preserved/validation'
root=HERE.parent/'m5_timed_integrality_20261001'
validation=root/'validation.json';assert hashlib.sha256(validation.read_bytes()).hexdigest()=='59c34db9a3e25c676c1d876a31a9fc2380e7f85e84c392d90e2760d233d05f98'
report=json.loads(validation.read_text())
directory=root/'verified_medium_180s/01_42_service_models';master=directory/'joint/joint_inventory.mps.gz'
relative=str(master.relative_to(root));digest=report['artifact_sha256'][relative]
assert hashlib.sha256(master.read_bytes()).hexdigest()==digest
old=next(x for x in report['joint_checks'] if x['path']==str(master))
assert old['complete_joint_matrix_checked'] and old['reported_bound_supported'] and old['formal_relaxation_optimal']
problem=load_instance(*load_routing_case(str(HERE/'medium25/1/inputs/case_relocated.json')))
candidate=json.loads((out/'reproduced_candidate.json').read_text());audit=audit_solution(problem,candidate);assert audit['valid']
model=gp.read(str(master))
try:
 context=context_for_export(problem,model,directory);proof=set_joint_projection(problem,context,candidate)
 model.update();fixed=0
 for var in model.getVars():
  if math.isfinite(var.Start) and abs(var.Start)<1e100:var.LB=var.UB=var.Start;fixed+=1
 model.Params.Threads=1;model.Params.TimeLimit=45
 model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9;model.optimize()
 assert model.Status==2 and abs(model.ObjVal-audit['objective'])<1e-6
 model.write(str(out/'fixed_global_lower_projection.mps.gz'))
 result=dict(status=int(model.Status),objective=model.ObjVal,full_upper=audit['objective'],fixed_variables=fixed,
  previous_independent_matrix_report_sha256=hashlib.sha256(validation.read_bytes()).hexdigest(),
  original_certified_lower_master_sha256=digest,full_upper_projects_to_original_certified_master=True,
  prior_global_lower=1623.9783373672776,conservative_margin_retained=True,full_proven_optimal=False,runtime=time.perf_counter()-begun)
finally:model.dispose()
(out/'global_lower_projection.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
