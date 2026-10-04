from pathlib import Path
import json,hashlib
H=Path('/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001')
(H/'master33').mkdir(exist_ok=True)
s=(H/'site30.py').read_text()
s=s.replace("source_snapshot_group28","source_snapshot_phase13").replace("manifest_site30_input.json","manifest_master33_input.json")
s=s.replace("out=HERE/'site30';oracle=json.loads((HERE/'site29/certificate_pending_validation.json').read_text())","out=HERE/'master33';oracle=json.loads((HERE/'core32/certificate_pending_validation.json').read_text())")
s=s.replace("(HERE/'site29/independent_validation.json').read_text())['independent_conditional_infeasibility_replay']","(HERE/'core32/independent_validation.json').read_text())['conditional_infeasibility_independently_replayed']")
s=s.replace("HERE/'group28/master.mps.gz'","HERE/'site30/master.mps.gz'").replace("running_site30","running_master33").replace("site30_pending_validation","master33_pending_validation")
s=s.replace("name='site29_cut'","name='core32_cut'")
s=s.replace("old_all_site_cut_preserved=True,","old_all_site_and_S1_cuts_preserved=True,partial_group_count=2,\n greedy_reduction_runtime=json.loads((HERE/'reduce32/summary.json').read_text())['runtime'],") if "'runtime'" in (H/'reduce32/summary.json').read_text() else s
# budget charging includes reduction rather than only last oracle
s=s.replace("90-oracle['runtime']-(time.perf_counter()-started)","90-json.loads((HERE/'reduce32/summary.json').read_text())['total_seconds']-(time.perf_counter()-started)")
s=s.replace("HERE/'group28/certificate_pending_validation.json'","HERE/'site30/certificate_pending_validation.json'")
s=s.replace("experimental_complete_joint_master_with_verified_S1_group_no_good","experimental_complete_joint_master_with_verified_partial_S1_group_no_good")
s=s.replace("model.getVarByName('all_groups27_active').Start=int(all(flags))","""model.getVarByName('all_groups27_active').Start=int(all(flags))
 oldlocal=json.loads((HERE/'site29/certificate_pending_validation.json').read_text())['trip_groups'];oldflags=[]
 for i,g in enumerate(oldlocal):
  value=int(frozenset(tuple(k) for k in g) in actual['S1']);oldflags.append(value);model.getVarByName(f'site29_cut_group_{i}').Start=value
 model.getVarByName('site29_cut_active').Start=int(all(oldflags))""")
summary=json.loads((H/'reduce32/summary.json').read_text());print(summary.keys())
# resolve exact runtime field as present
s=s.replace("['total_seconds']","['"+next(k for k in summary if k in ('runtime','total_seconds','wall_seconds','elapsed_seconds'))+"']")
(H/'master33.py').write_text(s)
paths=[H/'master33.py',H/'site30/master.mps.gz',H/'site30/certificate_pending_validation.json',H/'core32/certificate_pending_validation.json',H/'core32/independent_validation.json',H/'reduce32/summary.json',H/'medium25/0/inputs/case_relocated.json',H/'medium25/0/inputs/initial_result.json']
(H/'manifest_master33_input.json').write_text(json.dumps({'sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}},indent=2)+'\n')
