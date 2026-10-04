from pathlib import Path
import json,hashlib
H=Path('/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001');(H/'master42').mkdir()
s=(H/'master39.py').read_text().replace('master39','master42').replace('core38','site40').replace("HERE/'master36/master.mps.gz'","HERE/'master39/master.mps.gz'").replace("HERE/'master36/certificate_pending_validation.json'","HERE/'master39/certificate_pending_validation.json'")
s=s.replace('conditional_infeasibility_independently_replayed','conditional_cost_bound_independently_replayed')
s=s.replace("90-json.loads((HERE/'reduce38/summary.json').read_text())['runtime']","90-oracle['runtime']")
s=s.replace('site_only_group_no_good=True','site_only_group_no_good=False,conditional_site_cost_floor=True,group_count=6').replace('experimental_complete_joint_master_with_verified_partial_S1_group_no_good','experimental_complete_joint_master_with_verified_S1_group_cost_floor')
extra="""oldcore38=json.loads((HERE/'core38/certificate_pending_validation.json').read_text())['trip_groups'];oldcore38flags=[]
 for i,g in enumerate(oldcore38):
  value=int(frozenset(tuple(k) for k in g) in actual['S1']);oldcore38flags.append(value);model.getVarByName(f'core38_cut_group_{i}').Start=value
 model.getVarByName('core38_cut_active').Start=int(all(oldcore38flags))
 """
s=s.replace("model.write(str(out/'master.mps.gz'))",extra+"model.write(str(out/'master.mps.gz'))")
(H/'master42.py').write_text(s)
paths=[H/'master42.py',H/'master39/master.mps.gz',H/'master39/certificate_pending_validation.json',H/'site40/certificate_pending_validation.json',H/'site40/independent_validation.json',H/'medium25/0/inputs/case_relocated.json',H/'medium25/0/inputs/initial_result.json']
(H/'manifest_master42_input.json').write_text(json.dumps({'sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}},indent=2)+'\n')
s=(H/'validators_phase13/master39.py').read_text().replace("out=HERE/'master39'","out=HERE/'master42'").replace('core38_cut','site40_cut').replace("HERE/'core38/certificate_pending_validation.json'","HERE/'site40/certificate_pending_validation.json'").replace("HERE/'core38/independent_validation.json'","HERE/'site40/independent_validation.json'").replace("HERE/'master36/master.mps.gz'","HERE/'master39/master.mps.gz'").replace("HERE/'master36/independent_validation.json'","HERE/'master39/independent_validation.json'").replace('original_master36','original_master39')
s=s.replace('conditional_infeasibility_independently_replayed','conditional_cost_bound_independently_replayed')
s=s.replace("expected_rows['site40_cut_no_good']=('<',0,{active:1})","bound=json.loads((HERE/'site40/certificate_pending_validation.json').read_text())['bound'];expected_rows['site40_cut_cost_floor']=('>',0,{'physical_cost_S1':1,active:-bound})")
s=s.replace('exact_AND_and_local_no_good_checked','exact_AND_and_actual_cost_floor_checked')
s=s.replace(';assert not all(localflags)','').replace('local_active=0,all_site_active=0','local_active=int(all(localflags)),all_site_active=int(all(allflags))')
extra="""oldcore38=json.loads((HERE/'core38/certificate_pending_validation.json').read_text())['trip_groups'];oldcore38flags=[]
 for i,g in enumerate(oldcore38):
  value=int(frozenset(tuple(k) for k in g) in actual['S1']);oldcore38flags.append(value);model.getVarByName(f'core38_cut_group_{i}').Start=value
 model.getVarByName('core38_cut_active').Start=int(all(oldcore38flags))
 """
s=s.replace('model.update();fixed=0',extra+'model.update();fixed=0')
(H/'validators_phase13/master42.py').write_text(s)
