"""Verify both rejected prototypes and the restored public M1-M4 solver."""
import hashlib,json,subprocess,sys
from pathlib import Path

out = Path(__file__).resolve().parent
root = out.parents[2]
checks = {}
names = ['audited_tail','terminal_tail','lazy_pickup','route_recombine','compact_repair',
         'route_group','improving_groups','delivery_prefix','event_capacity','direct_graph','direct_relax']
for name in names:
    folder = out.parent/('alns_single_batch_'+name+'_20261001')
    p = subprocess.run([sys.executable,str(folder/'verify_hashes.py')],capture_output=True,text=True,check=True)
    checks[name] = json.loads(p.stdout)
for name in ['audited_tail','terminal_tail']:
    folder = out.parent/('alns_single_batch_'+name+'_20261001')
    checks[name+'_registration_preserved'] = all(
        hashlib.sha256((folder/f).read_bytes()).hexdigest() == sha
        for f,sha in json.loads((folder/'registration_hashes.json').read_text()).items())
    checks[name+'_restoration_exact'] = (json.loads((folder/'restored_source_manifest.json').read_text()) ==
                                        json.loads((folder/'before_source_manifest.json').read_text()))
    rows = json.loads((folder/'quality/summary.json').read_text())
    checks[name+'_all16_audits_valid'] = len(rows) == 16 and all(r.get('valid') and r.get('periodic') for r in rows)
    checks[name+'_input_preflight'] = json.loads((folder/'input_preflight.json').read_text())['passed']
    checks[name+'_decision'] = (json.loads((folder/'decision.json').read_text())['status'] == 'REJECTED' and
                               not json.loads((folder/'quality_comparison.json').read_text())['eligible'])
live = json.loads((out/'live_equivalence.json').read_text())
checks['all8_public_model_groups'] = len(live) == 8 and all(r['identical'] and r['valid'] for r in live)
checks['full_regression'] = '2325 passed, 14 warnings in 83.68s' in (out/'full_regression.log').read_text()
checks['candidate_tests_removed'] = not (root/'tests/routing/test_single_batch_audited_tail.py').exists()
def valid(value):
    return all(valid(v) for v in value.values()) if isinstance(value,dict) else value is True
checks['all_passed'] = valid(checks)
(out/'final_verification.json').write_text(json.dumps(checks,indent=2))
print(json.dumps(checks,indent=2))
assert checks['all_passed']
for name in ['audited_tail','terminal_tail']:
    folder = out.parent/('alns_single_batch_'+name+'_20261001')
    decision = json.loads((folder/'decision.json').read_text())
    decision['final_verification'] = 'PASSED'
    decision['shared_restored_validation'] = str(out/'final_verification.json')
    (folder/'decision.json').write_text(json.dumps(decision,indent=2))
