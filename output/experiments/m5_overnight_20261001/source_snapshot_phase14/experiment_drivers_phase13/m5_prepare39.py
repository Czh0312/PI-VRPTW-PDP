from pathlib import Path
import json,hashlib
H=Path('/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001');(H/'master39').mkdir()
s=(H/'master36.py').read_text().replace('master36','master39').replace('core35','core38').replace('reduce35','reduce38').replace("HERE/'master33/master.mps.gz'","HERE/'master36/master.mps.gz'").replace("HERE/'master33/certificate_pending_validation.json'","HERE/'master36/certificate_pending_validation.json'")
extra="""oldcore35=json.loads((HERE/'core35/certificate_pending_validation.json').read_text())['trip_groups'];oldcore35flags=[]
 for i,g in enumerate(oldcore35):
  value=int(frozenset(tuple(k) for k in g) in actual['S1']);oldcore35flags.append(value);model.getVarByName(f'core35_cut_group_{i}').Start=value
 model.getVarByName('core35_cut_active').Start=int(all(oldcore35flags))
 """
s=s.replace("model.write(str(out/'master.mps.gz'))",extra+"model.write(str(out/'master.mps.gz'))")
(H/'master39.py').write_text(s)
paths=[H/'master39.py',H/'master36/master.mps.gz',H/'master36/certificate_pending_validation.json',H/'core38/certificate_pending_validation.json',H/'core38/independent_validation.json',H/'reduce38/summary.json',H/'medium25/0/inputs/case_relocated.json',H/'medium25/0/inputs/initial_result.json']
(H/'manifest_master39_input.json').write_text(json.dumps({'sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}},indent=2)+'\n')
s=(H/'validators_phase13/master36.py').read_text().replace("out=HERE/'master36'","out=HERE/'master39'").replace('core35_cut','core38_cut').replace("HERE/'core35/certificate_pending_validation.json'","HERE/'core38/certificate_pending_validation.json'").replace("HERE/'core35/independent_validation.json'","HERE/'core38/independent_validation.json'").replace("HERE/'master33/master.mps.gz'","HERE/'master36/master.mps.gz'").replace("HERE/'master33/independent_validation.json'","HERE/'master36/independent_validation.json'").replace('original_master33','original_master36')
extra="""oldcore35=json.loads((HERE/'core35/certificate_pending_validation.json').read_text())['trip_groups'];oldcore35flags=[]
 for i,g in enumerate(oldcore35):
  value=int(frozenset(tuple(k) for k in g) in actual['S1']);oldcore35flags.append(value);model.getVarByName(f'core35_cut_group_{i}').Start=value
 model.getVarByName('core35_cut_active').Start=int(all(oldcore35flags))
 """
s=s.replace('model.update();fixed=0',extra+'model.update();fixed=0')
(H/'validators_phase13/master39.py').write_text(s)
