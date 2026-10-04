from pathlib import Path
import json,hashlib
H=Path('/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001');(H/'master48').mkdir()
s=(H/'master44.py').read_text().replace('master44','master48').replace("HERE/'master42/master.mps.gz'","HERE/'master44/master.mps.gz'").replace("HERE/'master42/certificate_pending_validation.json'","HERE/'master44/certificate_pending_validation.json'")
s=s.replace("oracle=json.loads((HERE/'group43/outcome.json').read_text())['certificate']","oracle=json.loads((HERE/'core47/certificate_pending_validation.json').read_text())").replace("HERE/'group43/independent_validation.json'","HERE/'core47/independent_validation.json'").replace('independent_conditional_infeasibility_replay','conditional_infeasibility_independently_replayed')
s=s.replace('source_snapshot_phase13','source_snapshot_phase14').replace("cut=add_group_schedule_cut(problem,model,context,oracle,name='all_groups43')","cut=add_physical_cut(problem,model,context,oracle,name='core47_cut')").replace('projection=project_group_schedule_cut(problem,context,initial,cut)','projection=project_physical_costs(problem,context,initial,cuts=[cut])')
s=s.replace('complete_all_site_group_no_good=True,group_count=13','partial_S3_single_group_no_good=True,group_count=1').replace('experimental_complete_joint_master_with_verified_all_path_group_no_good','experimental_complete_joint_master_with_verified_S3_single_group_no_good')
s=s.replace("90-oracle['runtime']","90-json.loads((HERE/'reduce46/summary.json').read_text())['runtime']")
extra="""oldall43=json.loads((HERE/'group43/groups.json').read_text());oldall43flags=[]
 for site in sorted(oldall43):
  for i,g in enumerate(sorted(tuple(sorted(tuple(k) for k in g)) for g in oldall43[site])):
   value=int(frozenset(g) in actual[site]);oldall43flags.append(value);model.getVarByName(f'all_groups43_{site}_group_{i}').Start=value
 model.getVarByName('all_groups43_active').Start=int(all(oldall43flags))
 """
s=s.replace("model.write(str(out/'master.mps.gz'))",extra+"model.write(str(out/'master.mps.gz'))")
(H/'master48.py').write_text(s)
paths=[H/'master48.py',H/'master44/master.mps.gz',H/'master44/certificate_pending_validation.json',H/'core47/certificate_pending_validation.json',H/'core47/independent_validation.json',H/'reduce46/summary.json',H/'medium25/0/inputs/case_relocated.json',H/'medium25/0/inputs/initial_result.json']
(H/'manifest_master48_input.json').write_text(json.dumps({'sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}},indent=2)+'\n')
s=(H/'validators_phase13/master44.py').read_text().replace('source_snapshot_phase13','source_snapshot_phase14').replace("out=HERE/'master44'","out=HERE/'master48'").replace("HERE/'master42/independent_validation.json'","HERE/'master44/independent_validation.json'").replace("HERE/'master42/master.mps.gz'","HERE/'master44/master.mps.gz'").replace("HERE/'group43/independent_validation.json'","HERE/'core47/independent_validation.json'").replace('independent_conditional_infeasibility_replay','conditional_infeasibility_independently_replayed')
a=s.index(' ev,er,names={},{},[]');b=s.index(' initial=json.loads',a)
s=s[:a]+""" local=json.loads((HERE/'core47/certificate_pending_validation.json').read_text())['trip_groups'];assert len(local)==1;members=defaultdict(set)
 tasks=[(o.id,k) for o in p.orders if o.satellite=='S3' for k in ('d','p')]
 for i,key in enumerate(tasks):
  r=base.getConstrByName(f'joint_cover_1_{i}');assert r.Sense=='=' and r.RHS==1;e=base.getRow(r)
  for j in range(e.size()):assert e.getCoeff(j)==1;members[e.getVar(j).VarName].add(key)
 name='core47_cut_group_0';ev={name:(0,1,'B',0)};er={'core47_cut_group_match_0':('=',0,{name:1,**{v:-1 for v,ks in members.items() if ks==set(tuple(k) for k in local[0])}}),'core47_cut_no_good':('<',0,{name:1})}
 assert {v.VarName:(v.LB,v.UB,v.VType,v.Obj) for v in b[n:]}==ev
 assert {r.ConstrName:row(model,r) for r in model.getConstrs()[m:]}==er
 matrix=dict(original_master44_variables=n,original_master44_rows=m,all_original_attributes_rows_objective_indicators_unchanged=True,added_variables=len(ev),added_rows_checked=len(er),exact_S3_group_flag_decoded_from_original_complete_cover_rows=True,local_single_group_no_good_checked=True)
"""+s[b:]
s=s.replace(' model.update();fixed=0',""" model.getVarByName('core47_cut_group_0').Start=int(frozenset(tuple(k) for k in local[0]) in actual['S3'])
 model.update();fixed=0""")
(H/'validators_phase14/master48.py').write_text(s)
