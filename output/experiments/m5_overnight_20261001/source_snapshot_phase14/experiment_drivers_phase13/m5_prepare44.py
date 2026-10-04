from pathlib import Path
import json,hashlib
H=Path('/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001');(H/'master44').mkdir()
s=(H/'master42.py').read_text().replace('master42','master44').replace("HERE/'master39/master.mps.gz'","HERE/'master42/master.mps.gz'").replace("HERE/'master39/certificate_pending_validation.json'","HERE/'master42/certificate_pending_validation.json'")
s=s.replace("oracle=json.loads((HERE/'site40/certificate_pending_validation.json').read_text())","oracle=json.loads((HERE/'group43/outcome.json').read_text())['certificate']").replace("HERE/'site40/independent_validation.json'","HERE/'group43/independent_validation.json'").replace('conditional_cost_bound_independently_replayed','independent_conditional_infeasibility_replay')
s=s.replace('from warm_context import context_for_export','from models.routing.solvers.exact.multibatch_group_cuts import add_group_schedule_cut,project_group_schedule_cut\nfrom warm_context import context_for_export')
s=s.replace("cut=add_physical_cut(problem,model,context,oracle,name='site40_cut')","cut=add_group_schedule_cut(problem,model,context,oracle,name='all_groups43')").replace('projection=project_physical_costs(problem,context,initial,cuts=[cut])','projection=project_group_schedule_cut(problem,context,initial,cut)')
s=s.replace('site_only_group_no_good=False,conditional_site_cost_floor=True,group_count=6','complete_all_site_group_no_good=True,group_count=13').replace('experimental_complete_joint_master_with_verified_S1_group_cost_floor','experimental_complete_joint_master_with_verified_all_path_group_no_good')
extra="""oldsite40=json.loads((HERE/'site40/certificate_pending_validation.json').read_text())['trip_groups'];oldsite40flags=[]
 for i,g in enumerate(oldsite40):
  value=int(frozenset(tuple(k) for k in g) in actual['S1']);oldsite40flags.append(value);model.getVarByName(f'site40_cut_group_{i}').Start=value
 model.getVarByName('site40_cut_active').Start=int(all(oldsite40flags))
 """
s=s.replace("model.write(str(out/'master.mps.gz'))",extra+"model.write(str(out/'master.mps.gz'))")
(H/'master44.py').write_text(s)
paths=[H/'master44.py',H/'master42/master.mps.gz',H/'master42/certificate_pending_validation.json',H/'group43/outcome.json',H/'group43/independent_validation.json',H/'medium25/0/inputs/case_relocated.json',H/'medium25/0/inputs/initial_result.json']
(H/'manifest_master44_input.json').write_text(json.dumps({'sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}},indent=2)+'\n')
