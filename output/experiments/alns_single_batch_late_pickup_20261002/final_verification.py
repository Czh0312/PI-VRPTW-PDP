"""Close an algorithm pilot without altering historical timing evidence."""
from pathlib import Path
import hashlib, json, re
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
OWN='models/routing/solvers/alns/single_batch.py'
def hashes(base):
    return {str(p.relative_to(base)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(base.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
def frozen(folder):
    checks={}
    for name in ('before_source','after_source','disabled_source','adopted_source','restored_source'):
        m=folder/(name+'_manifest.json')
        if m.exists(): checks[name]=hashes(folder/name)==json.loads(m.read_text())
    input_manifest=folder/'input_hashes_completed.json'
    if not input_manifest.exists(): input_manifest=folder/'input_hashes.json'
    checks['inputs']=hashes(folder/'benchmark_inputs')==json.loads(input_manifest.read_text())
    return checks
checks={'candidate':frozen(OUT)}
expected=json.loads((OUT/'owned_hashes.json').read_text())
checks['candidate_owned']=all(hashlib.sha256((ROOT/f).read_bytes()).hexdigest()==h for f,h in expected.items())
checks['registration']=all(hashlib.sha256((OUT/f).read_bytes()).hexdigest()==h
                           for f,h in json.loads((OUT/'registration_hashes.json').read_text()).items())
archives=[('e1_trials',OUT.parent/'alns_single_batch_e1_trials_20261002'),
          ('vehicle_seed',OUT.parent/'alns_single_batch_vehicle_seed_20261002')]
archives += [(n,OUT.parent/('alns_single_batch_'+n+'_20261001')) for n in
             ('audited_tail','terminal_tail','lazy_pickup','route_recombine','compact_repair',
              'route_group','improving_groups','delivery_prefix','event_capacity','direct_graph','direct_relax')]
for name,folder in archives:
    prior=frozen(folder)
    manifest=folder/'restored_owned_hashes.json'
    if not manifest.exists(): manifest=folder/'owned_hashes.json'
    old=json.loads(manifest.read_text())
    prior['nonoverlap_live_owned']=all(hashlib.sha256((ROOT/f).read_bytes()).hexdigest()==h
                                      for f,h in old.items() if f!=OWN)
    if OWN in old:
        prior['overlap_baseline_preserved']=hashlib.sha256((OUT/'before_source'/OWN).read_bytes()).hexdigest()==old[OWN]
    if (folder/'restored_owned_hashes.json').exists():
        prior['archived_tests']=all(hashlib.sha256((folder/'rejected_tests'/Path(f).name).read_bytes()).hexdigest()==h
            for f,h in json.loads((folder/'owned_hashes.json').read_text()).items() if f.startswith('tests/'))
    checks[name]=prior
rows=json.loads((OUT/'quality/summary.json').read_text())
checks['all16_independent_audits']=len(rows)==16 and all(r['valid'] and r['periodic'] for r in rows)
decision=json.loads((OUT/'decision.json').read_text())
checks['registered_quality_and_mechanism_passed']=decision['quality_gate_passed'] and decision['mechanism_gate_passed']
live=json.loads((OUT/'live_equivalence.json').read_text())
checks['all8_public_groups']=len(live)==8 and all(r['candidate_live_exact'] and r['valid'] and
                                                (r['model']==3 or r['baseline_equal']) for r in live)
disabled=json.loads((OUT/'disabled_equivalence.json').read_text())
checks['disabled_exact']=len(disabled)==2 and all(r['exact'] and r['valid'] for r in disabled)
regression=json.loads((OUT/'regression_result.json').read_text())
summary=re.search(r'\d+ passed(?:, \d+ warnings)? in [0-9.]+s',(OUT/'full_regression.log').read_text())
checks['full_regression']=regression['exit_code']==0 and summary is not None
def valid(value):
    return all(valid(v) for v in value.values()) if isinstance(value,dict) else value is True
checks['all_passed']=valid(checks)
(OUT/'final_verification.json').write_text(json.dumps(checks,indent=2))
print(json.dumps(checks,indent=2))
assert checks['all_passed']
decision.update(decision='ADOPTED_REGISTERED_PILOT',verification='PASSED_FULL_REGRESSION_AND_PUBLIC_EQUIVALENCE',
                regression_summary=summary.group(0),evidence_limit='Eight paired runs on selected50/100 inputs; no universal quality, causality or optimality/GAP claim')
(OUT/'decision.json').write_text(json.dumps(decision,indent=2))
