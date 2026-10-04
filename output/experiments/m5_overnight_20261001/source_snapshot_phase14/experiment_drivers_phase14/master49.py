from pathlib import Path
import sys,json,time,hashlib,os,math
import gurobipy as gp
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'source_snapshot_phase14'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.solvers.exact.multibatch_joint_inventory import set_joint_projection
from models.routing.solvers.exact.multibatch_physical_cuts import add_physical_cut,project_physical_costs
from models.routing.solvers.exact.multibatch_group_cuts import add_group_schedule_cut,project_group_schedule_cut
from warm_context import context_for_export
for p,d in json.loads((HERE/'manifest_master49_input.json').read_text())['sha256'].items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==d
started=time.perf_counter();out=HERE/'master49';oracle=json.loads((HERE/'core47/certificate_pending_validation.json').read_text())
assert json.loads((HERE/'core47/independent_validation.json').read_text())['conditional_infeasibility_independently_replayed']
problem=load_instance(*load_routing_case(str(HERE/'medium25/0/inputs/case_relocated.json')))
initial=json.loads((HERE/'medium25/0/inputs/initial_result.json').read_text());groups=json.loads((HERE/'group27/groups.json').read_text())
directory=HERE.parent/'m5_timed_integrality_20261001/verified_medium_180s/00_42_service_models'
model=gp.read(str(HERE/'master48/master.mps.gz'))
c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='running_master49',active_process=os.getpid());(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n')
(out/'parameters.json').write_text(json.dumps(dict(time_limit=90,oracle_time_charged=oracle['runtime'],seed=42,threads=1,
 original_input=True,original_initial=True,complete_route_choice_integrality=True,all_original_columns_preserved=True,old_all_site_cut_preserved=True,
 reused_control_already_proved_optimal=True,no_new_matched_timing_benchmark=True),indent=2)+'\n')
try:
 context=context_for_export(problem,model,directory)
 context['physical_costs']={s:model.getVarByName('physical_cost_'+s) for s in groups}
 context['physical_cover_rows']={s:model.getConstrByName('physical_cover_cost_'+s) for s in groups}
 context['e1']['supplies']={(s,b):model.getVarByName(f'supply_{s}_{b}') for (s,b),count in problem.demands.items() if count}
 integer_variables=[]
 for data in context['routes'].values():
  for v in data['choices'].values():
   assert v.LB==0 and v.UB==1 and v.VType=='C';v.VType='B';integer_variables.append(v.VarName)
 model.update()
 (out/'integer_variables.json').write_text(json.dumps(sorted(integer_variables),indent=2)+'\n')
 set_joint_projection(problem,context,initial);projection=project_physical_costs(problem,context,initial,cuts=[])
 actual={s:{frozenset((e['order_id'],e['kind']) for e in t['events']) for t in initial['e2_trips'] if t['satellite_id']==s} for s in groups};flags=[]
 for s in sorted(groups):
  for i,g in enumerate(sorted(tuple(sorted(tuple(k) for k in g)) for g in groups[s])):
   value=int(frozenset(g) in actual[s]);flags.append(value);model.getVarByName(f'all_groups27_{s}_group_{i}').Start=value
 model.getVarByName('all_groups27_active').Start=int(all(flags))
 oldlocal=json.loads((HERE/'site29/certificate_pending_validation.json').read_text())['trip_groups'];oldflags=[]
 for i,g in enumerate(oldlocal):
  value=int(frozenset(tuple(k) for k in g) in actual['S1']);oldflags.append(value);model.getVarByName(f'site29_cut_group_{i}').Start=value
 model.getVarByName('site29_cut_active').Start=int(all(oldflags))
 oldcore=json.loads((HERE/'core32/certificate_pending_validation.json').read_text())['trip_groups'];oldcoreflags=[]
 for i,g in enumerate(oldcore):
  value=int(frozenset(tuple(k) for k in g) in actual['S1']);oldcoreflags.append(value);model.getVarByName(f'core32_cut_group_{i}').Start=value
 model.getVarByName('core32_cut_active').Start=int(all(oldcoreflags))
 oldcore35=json.loads((HERE/'core35/certificate_pending_validation.json').read_text())['trip_groups'];oldcore35flags=[]
 for i,g in enumerate(oldcore35):
  value=int(frozenset(tuple(k) for k in g) in actual['S1']);oldcore35flags.append(value);model.getVarByName(f'core35_cut_group_{i}').Start=value
 model.getVarByName('core35_cut_active').Start=int(all(oldcore35flags))
 oldcore38=json.loads((HERE/'core38/certificate_pending_validation.json').read_text())['trip_groups'];oldcore38flags=[]
 for i,g in enumerate(oldcore38):
  value=int(frozenset(tuple(k) for k in g) in actual['S1']);oldcore38flags.append(value);model.getVarByName(f'core38_cut_group_{i}').Start=value
 model.getVarByName('core38_cut_active').Start=int(all(oldcore38flags))
 oldsite40=json.loads((HERE/'site40/certificate_pending_validation.json').read_text())['trip_groups'];oldsite40flags=[]
 for i,g in enumerate(oldsite40):
  value=int(frozenset(tuple(k) for k in g) in actual['S1']);oldsite40flags.append(value);model.getVarByName(f'site40_cut_group_{i}').Start=value
 model.getVarByName('site40_cut_active').Start=int(all(oldsite40flags))
 oldall43=json.loads((HERE/'group43/groups.json').read_text());oldall43flags=[]
 for site in sorted(oldall43):
  for i,g in enumerate(sorted(tuple(sorted(tuple(k) for k in g)) for g in oldall43[site])):
   value=int(frozenset(g) in actual[site]);oldall43flags.append(value);model.getVarByName(f'all_groups43_{site}_group_{i}').Start=value
 model.getVarByName('all_groups43_active').Start=int(all(oldall43flags))
 model.getVarByName('core47_cut_group_0').Start=int(frozenset(tuple(k) for k in oracle['trip_groups'][0]) in actual['S3'])
 model.write(str(out/'master.mps.gz'))
 (out/'initial_projection.json').write_text(json.dumps(projection,indent=2)+'\n')
 model.Params.Threads=1;model.Params.Seed=42;model.Params.MIPGap=model.Params.MIPGapAbs=0
 model.Params.IntFeasTol=model.Params.FeasibilityTol=1e-9
 model.Params.TimeLimit=max(0,90-(time.perf_counter()-started));model.optimize()
 raw=float(model.ObjBound);margin=1e-7*max(1,abs(raw));old=json.loads((HERE/'master48/certificate_pending_validation.json').read_text())['candidate_global_lower'];lower=max(old,raw-margin)
 selected=[dict(satellite=s,tasks=d['rows'][i]['column']['tasks'],cutoff=b,value=v.X,cost=d['rows'][i]['column']['cost'])
  for s,d in context['routes'].items() for (i,b),v in d['choices'].items() if model.SolCount and v.X>1e-6]
 (out/'selected_columns.json').write_text(json.dumps(selected,indent=2)+'\n')
 certificate=dict(scope='experimental_complete_joint_master_with_all_route_choice_integrality',candidate_global_lower=lower,
  raw_bound=raw,margin=margin,status=int(model.Status),relaxation_optimal=model.Status==2,
  old_global_lower=old,upper=1437.473775767574,full_proven_optimal=False,full_proven_infeasible=False,
  master_sha256=hashlib.sha256((out/'master.mps.gz').read_bytes()).hexdigest(),variables=model.NumVars,rows=model.NumConstrs,
  indicators=model.NumGenConstrs,runtime=time.perf_counter()-started,solver_runtime=model.Runtime)
 (out/'certificate_pending_validation.json').write_text(json.dumps(certificate,indent=2)+'\n')
finally:model.dispose()
c=json.loads((HERE/'checkpoint.json').read_text());c.update(status='master49_pending_validation',active_process=None);(HERE/'checkpoint.json').write_text(json.dumps(c,indent=2)+'\n');print(json.dumps(certificate,indent=2))
