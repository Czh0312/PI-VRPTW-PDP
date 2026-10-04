from pathlib import Path
import json,hashlib
H=Path('/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001');(H/'master36').mkdir()
s=(H/'master33.py').read_text().replace('master33','master36').replace('core32','core35').replace('reduce32','reduce35').replace("HERE/'site30/master.mps.gz'","HERE/'master33/master.mps.gz'").replace("HERE/'site30/certificate_pending_validation.json'","HERE/'master33/certificate_pending_validation.json'")
extra="""oldcore=json.loads((HERE/'core32/certificate_pending_validation.json').read_text())['trip_groups'];oldcoreflags=[]
 for i,g in enumerate(oldcore):
  value=int(frozenset(tuple(k) for k in g) in actual['S1']);oldcoreflags.append(value);model.getVarByName(f'core32_cut_group_{i}').Start=value
 model.getVarByName('core32_cut_active').Start=int(all(oldcoreflags))
 """
s=s.replace("model.write(str(out/'master.mps.gz'))",extra+"model.write(str(out/'master.mps.gz'))")
(H/'master36.py').write_text(s)
paths=[H/'master36.py',H/'master33/master.mps.gz',H/'master33/certificate_pending_validation.json',H/'core35/certificate_pending_validation.json',H/'core35/independent_validation.json',H/'reduce35/summary.json',H/'medium25/0/inputs/case_relocated.json',H/'medium25/0/inputs/initial_result.json']
(H/'manifest_master36_input.json').write_text(json.dumps({'sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}},indent=2)+'\n')
s=(H/'validators_phase13/master33.py').read_text().replace("out=HERE/'master33'","out=HERE/'master36'").replace('core32_cut','core35_cut').replace("HERE/'core32/certificate_pending_validation.json'","HERE/'core35/certificate_pending_validation.json'").replace("HERE/'core32/independent_validation.json'","HERE/'core35/independent_validation.json'").replace("HERE/'site30/master.mps.gz'","HERE/'master33/master.mps.gz'").replace("HERE/'site30/independent_validation.json'","HERE/'master33/independent_validation.json'").replace('original_site30','original_master33')
extra="""oldcore=json.loads((HERE/'core32/certificate_pending_validation.json').read_text())['trip_groups'];oldcoreflags=[]
 for i,g in enumerate(oldcore):
  value=int(frozenset(tuple(k) for k in g) in actual['S1']);oldcoreflags.append(value);model.getVarByName(f'core32_cut_group_{i}').Start=value
 model.getVarByName('core32_cut_active').Start=int(all(oldcoreflags))
 """
s=s.replace('model.update();fixed=0',extra+'model.update();fixed=0')
(H/'validators_phase13/master36.py').write_text(s)
