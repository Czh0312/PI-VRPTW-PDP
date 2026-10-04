from pathlib import Path
import sys,json,time,os,hashlib,math
import gurobipy as gp
H=Path(__file__).resolve().parent;sys.path.insert(0,str(H/'source_snapshot_phase14'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.solvers.exact.multibatch_joint_inventory import set_joint_projection
from warm_context import context_for_export
p=load_instance(*load_routing_case(str(H/'medium25/0/inputs/case_relocated.json')));initial=json.loads((H/'medium25/0/inputs/initial_result.json').read_text());assert audit_solution(p,initial)['valid'];out=H/'control51';out.mkdir()
directory=H.parent/'m5_timed_integrality_20261001/verified_medium_180s/00_42_service_models';source=directory/'joint/joint_inventory.mps.gz'
(out/'parameters.json').write_text(json.dumps(dict(time_limit=90,seed=42,threads=1,same_original_input_and_initial=True,original_complete_7268_choice_domain=True,no_new_group_flags_or_no_goods=True,all_choice_integer_control=True),indent=2)+'\n')
(out/'input_sha256.json').write_text(json.dumps({str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in [source,H/'medium25/0/inputs/case_relocated.json',H/'medium25/0/inputs/initial_result.json',Path(__file__)]},indent=2)+'\n')
c=json.loads((H/'checkpoint.json').read_text());c.update(status='running_control51',active_process=os.getpid());(H/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n')
start=time.perf_counter();model=gp.read(str(source));ctx=context_for_export(p,model,directory)
try:
 names=[]
 for data in ctx['routes'].values():
  for v in data['choices'].values():assert (v.LB,v.UB,v.VType)==(0,1,'C');v.VType='B';names.append(v.VarName)
 assert len(names)==7268
 model.update();(out/'integer_variables.json').write_text(json.dumps(sorted(names),indent=2)+'\n');set_joint_projection(p,ctx,initial);model.write(str(out/'master.mps.gz'))
 model.Params.Threads=1;model.Params.Seed=42;model.Params.MIPGap=model.Params.MIPGapAbs=0;model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9;model.Params.TimeLimit=max(0,90-(time.perf_counter()-start));model.optimize()
 raw=float(model.ObjBound);margin=1e-7*max(1,abs(raw));cert=dict(scope='original_complete_joint_route_integrality_control_no_overnight_cuts',raw_bound=raw,margin=margin,candidate_global_lower=max(1426.648954231285,raw-margin),status=int(model.Status),relaxation_optimal=model.Status==2,full_proven_optimal=False,full_proven_infeasible=False,runtime=time.perf_counter()-start,solver_runtime=model.Runtime,master_sha256=hashlib.sha256((out/'master.mps.gz').read_bytes()).hexdigest(),variables=model.NumVars,rows=model.NumConstrs,indicators=model.NumGenConstrs)
 (out/'certificate_pending_validation.json').write_text(json.dumps(cert,indent=2)+'\n')
finally:model.dispose()
c=json.loads((H/'checkpoint.json').read_text());c.update(status='control51_pending_validation',active_process=None);(H/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n');print(json.dumps(cert,indent=2))
