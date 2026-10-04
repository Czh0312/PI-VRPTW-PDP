from pathlib import Path
import json,hashlib
H=Path('/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001');(H/'master49').mkdir()
s=(H/'master48.py').read_text().replace('master48','master49').replace("HERE/'master44/master.mps.gz'","HERE/'master48/master.mps.gz'").replace("HERE/'master44/certificate_pending_validation.json'","HERE/'master48/certificate_pending_validation.json'")
s=s.replace("cut=add_physical_cut(problem,model,context,oracle,name='core47_cut')","integer_variables=[]\n for data in context['routes'].values():\n  for v in data['choices'].values():\n   assert v.LB==0 and v.UB==1 and v.VType=='C';v.VType='B';integer_variables.append(v.VarName)\n model.update()\n (out/'integer_variables.json').write_text(json.dumps(sorted(integer_variables),indent=2)+'\\n')").replace('projection=project_physical_costs(problem,context,initial,cuts=[cut])','projection=project_physical_costs(problem,context,initial,cuts=[])')
s=s.replace('partial_S3_single_group_no_good=True,group_count=1','complete_route_choice_integrality=True,all_original_columns_preserved=True').replace('experimental_complete_joint_master_with_verified_S3_single_group_no_good','experimental_complete_joint_master_with_all_route_choice_integrality')
s=s.replace("90-json.loads((HERE/'reduce46/summary.json').read_text())['runtime']","90").replace("runtime=time.perf_counter()-started+oracle['runtime']","runtime=time.perf_counter()-started")
s=s.replace("model.write(str(out/'master.mps.gz'))","model.getVarByName('core47_cut_group_0').Start=int(frozenset(tuple(k) for k in oracle['trip_groups'][0]) in actual['S3'])\n model.write(str(out/'master.mps.gz'))")
(H/'master49.py').write_text(s)
paths=[H/'master49.py',H/'master48/master.mps.gz',H/'master48/certificate_pending_validation.json',H/'master48/independent_validation.json',H/'core47/certificate_pending_validation.json',H/'core47/independent_validation.json',H/'medium25/0/inputs/case_relocated.json',H/'medium25/0/inputs/initial_result.json']
(H/'manifest_master49_input.json').write_text(json.dumps({'sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}},indent=2)+'\n')
s=(H/'validators_phase14/master48.py').read_text().replace("out=HERE/'master48'","out=HERE/'master49'").replace("HERE/'master44/master.mps.gz'","HERE/'master48/master.mps.gz'").replace("HERE/'master44/independent_validation.json'","HERE/'master48/independent_validation.json'")
a=s.index(" for attr in ['VarName'");b=s.index(' initial=json.loads',a)
s=s[:a]+""" allchoices=set();tasks_checked=0
 for si,site in enumerate(sorted(groups)):
  tasks=[(o.id,k) for o in p.orders if o.satellite==site for k in ('d','p')]
  for i,key in enumerate(tasks):
   r=base.getConstrByName(f'joint_cover_{si}_{i}');assert r.Sense=='=' and r.RHS==1;e=base.getRow(r)
   for j in range(e.size()):assert e.getCoeff(j)==1;allchoices.add(e.getVar(j).VarName)
   tasks_checked+=1
 assert len(allchoices)==7268 and allchoices==set(json.loads((out/'integer_variables.json').read_text()))
 assert base.NumVars==model.NumVars and base.NumConstrs==model.NumConstrs
 for x,y in zip(a,b):
  assert x.VarName==y.VarName and (x.LB,x.UB,x.Obj)==(y.LB,y.UB,y.Obj)
  assert (x.VType=='C' and y.VType=='B') if x.VarName in allchoices else x.VType==y.VType
 assert base.ObjCon==model.ObjCon and base.ModelSense==model.ModelSense and indicators(base)==indicators(model)
 for x,y in zip(base.getConstrs(),model.getConstrs()):assert x.ConstrName==y.ConstrName and row(base,x)==row(model,y)
 matrix=dict(original_variables=n,original_rows=m,all_original_rows_objective_indicators_and_bounds_unchanged=True,only_complete_cover_choice_types_changed=True,binary_choices_checked=len(allchoices),original_task_cover_rows_checked=tasks_checked,complete_domain_preserved=True)
 local=json.loads((HERE/'core47/certificate_pending_validation.json').read_text())['trip_groups']
"""+s[b:]
(H/'validators_phase14/master49.py').write_text(s)
