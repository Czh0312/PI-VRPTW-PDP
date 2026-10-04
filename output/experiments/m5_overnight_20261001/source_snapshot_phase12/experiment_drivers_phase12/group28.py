from pathlib import Path
import sys,json,hashlib,math,time,os
import gurobipy as gp
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot_group28'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_joint_inventory import set_joint_projection
from models.routing.solvers.exact.multibatch_group_cuts import add_group_schedule_cut,project_group_schedule_cut
from warm_context import context_for_export
started=time.perf_counter();oracle=json.loads((HERE/'group27/outcome.json').read_text())['certificate']
assert json.loads((HERE/'group27/independent_validation.json').read_text())['independent_conditional_infeasibility_replay']
manifest=json.loads((HERE/'manifest_group28_input.json').read_text())['sha256']
for p,h in manifest.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==h
problem=load_instance(*load_routing_case(str(HERE/'medium25/0/inputs/case_relocated.json')))
initial=json.loads((HERE/'medium25/0/inputs/initial_result.json').read_text())
root=HERE.parent/'m5_timed_integrality_20261001';directory=root/'verified_medium_180s/00_42_service_models'
master=directory/'joint/joint_inventory.mps.gz';out=HERE/'group28'
params=dict(time_limit=90,oracle_time_charged=oracle['runtime'],seed=42,threads=1,same_frozen_input=True,
 same_initial=True,reused_control_is_already_proved_optimal=True,control_configured_budget=180,
 control_actual_total_seconds=1.941209333,not_a_new_matched_timing_benchmark=True)
(out/'parameters.json').write_text(json.dumps(params,indent=2)+'\n')
c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='running_group28',active_process=os.getpid());(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n')
model=gp.read(str(master))
try:
 context=context_for_export(problem,model,directory)
 context['e1']['supplies']={(site,bid):model.getVarByName(f'supply_{site}_{bid}') for (site,bid),count in problem.demands.items() if count}
 cut=add_group_schedule_cut(problem,model,context,oracle,name='all_groups27')
 set_joint_projection(problem,context,initial);projection=project_group_schedule_cut(problem,context,initial,cut)
 model.write(str(out/'master.mps.gz'))
 (out/'initial_projection.json').write_text(json.dumps(projection,indent=2)+'\n')
 model.Params.Threads=1;model.Params.Seed=42;model.Params.MIPGap=model.Params.MIPGapAbs=0
 model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9
 model.Params.TimeLimit=max(0,90-oracle['runtime']-(time.perf_counter()-started));model.optimize()
 raw=float(model.ObjBound);margin=1e-7*max(1,abs(raw));lower=max(1426.648954231285,raw-margin)
 selected=[dict(satellite=site,tasks=data['rows'][i]['column']['tasks'],cutoff=bid,value=v.X,cost=data['rows'][i]['column']['cost'])
  for site,data in context['routes'].items() for (i,bid),v in data['choices'].items() if model.SolCount and v.X>1e-6]
 (out/'selected_columns.json').write_text(json.dumps(selected,indent=2)+'\n')
 certificate=dict(scope='experimental_complete_joint_master_with_all_permutation_group_no_good',
  candidate_global_lower=lower,raw_bound=raw,margin=margin,status=int(model.Status),
  relaxation_optimal=model.Status==2,full_proven_optimal=False,full_proven_infeasible=False,
  old_global_lower=1426.648954231285,upper=1437.473775767574,initial_projection=projection,
  master_sha256=hashlib.sha256((out/'master.mps.gz').read_bytes()).hexdigest(),
  variables=model.NumVars,rows=model.NumConstrs,indicators=model.NumGenConstrs,
  runtime=time.perf_counter()-started+oracle['runtime'],solver_runtime=model.Runtime)
 (out/'certificate_pending_validation.json').write_text(json.dumps(certificate,indent=2)+'\n')
finally:model.dispose()
c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='group28_pending_independent_validation',active_process=None);(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n')
print(json.dumps(certificate,indent=2))
