from pathlib import Path
H=Path('/Users/chenzeheng/Desktop/2E-VRPTW-PDD/output/experiments/m5_overnight_20261001')
s=(H/'validators_group27/site30.py').read_text()
s=s.replace("source_snapshot_group28","source_snapshot_phase13").replace("out=HERE/'site30'","out=HERE/'master33'")
s=s.replace("(HERE/'group28/independent_validation.json').read_text())['promote_supported']","(HERE/'site30/independent_validation.json').read_text())['promote_supported']")
s=s.replace("(HERE/'site29/independent_validation.json').read_text())['independent_conditional_infeasibility_replay']","(HERE/'core32/independent_validation.json').read_text())['conditional_infeasibility_independently_replayed']")
s=s.replace("local=sorted(tuple(sorted(tuple(k) for k in g)) for g in groups['S1'])","oldlocal=sorted(tuple(sorted(tuple(k) for k in g)) for g in groups['S1']);local=sorted(tuple(sorted(tuple(k) for k in g)) for g in __import__('json').loads((HERE/'core32/certificate_pending_validation.json').read_text())['trip_groups'])")
s=s.replace("HERE/'group28/master.mps.gz'","HERE/'site30/master.mps.gz'")
s=s.replace('site29_cut','core32_cut').replace('original_group28','original_site30')
s=s.replace("if s=='S1':localflags.append(value);model.getVarByName(f'core32_cut_group_{i}').Start=value","if s=='S1':model.getVarByName(f'site29_cut_group_{i}').Start=value")
s=s.replace("model.getVarByName('all_groups27_active').Start=int(all(allflags));model.getVarByName(active).Start=int(all(localflags));assert not all(localflags)","""model.getVarByName('all_groups27_active').Start=int(all(allflags))
 model.getVarByName('site29_cut_active').Start=int(all(frozenset(g) in actual['S1'] for g in oldlocal))
 for i,g in enumerate(local):
  value=int(frozenset(g) in actual['S1']);localflags.append(value);model.getVarByName(f'core32_cut_group_{i}').Start=value
 model.getVarByName(active).Start=int(all(localflags));assert not all(localflags)""")
(H/'validators_phase13/master33.py').write_text(s)
