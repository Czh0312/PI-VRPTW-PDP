"""Verify restoration, original registration and all preserved evidence."""
import hashlib, json, subprocess, sys
from pathlib import Path

out = Path(__file__).resolve().parent
root = out.parents[2]
checks = {}
for name in ['route_recombine','lazy_pickup','compact_repair','route_group','improving_groups',
             'delivery_prefix','event_capacity','direct_graph','direct_relax']:
    previous = out.parent/('alns_single_batch_'+name+'_20261001')
    p = subprocess.run([sys.executable,str(previous/'verify_hashes.py')],
                       capture_output=True,text=True,check=True)
    checks[name] = json.loads(p.stdout)
checks['registration_preserved'] = all(
    hashlib.sha256((out/f).read_bytes()).hexdigest() == sha
    for f,sha in json.loads((out/'registration_hashes.json').read_text()).items())
initial = json.loads((out/'input_hashes.json').read_text())
checks['initial_inputs_preserved'] = all(
    hashlib.sha256((out/'benchmark_inputs'/f).read_bytes()).hexdigest() == sha for f,sha in initial.items())
checks['restored_source_equals_before'] = (json.loads((out/'restored_source_manifest.json').read_text()) ==
                                          json.loads((out/'before_source_manifest.json').read_text()))
live = json.loads((out/'live_equivalence.json').read_text())
checks['all8_public_model_groups'] = len(live) == 8 and all(r['valid'] and r['identical'] for r in live)
checks['full_regression'] = '2202 passed, 14 warnings in 59.15s' in (out/'full_regression.log').read_text()
decision = json.loads((out/'decision.json').read_text())
addendum = json.loads((out/'input_repair_addendum.json').read_text())
checks['rejection_precedes_supplement'] = decision['created'] < addendum['created'] and decision['status'] == 'REJECTED'
original = json.loads((out/'quality/summary.json').read_text())
supplement = json.loads((out/'quality_missing_inputs/summary.json').read_text())
checks['original_failures_preserved'] = (len(original) == 16 and sum(r.get('valid',False) for r in original) == 10 and
                                        all('FileNotFoundError' in r.get('error','') for r in original if not r.get('valid')))
checks['completed16_audits'] = (len(supplement) == 6 and
                               all(r.get('valid') and r.get('periodic') for r in original if r['index'] >= 3) and
                               all(r.get('valid') and r.get('periodic') for r in supplement))
checks['completed_gate_failed'] = not json.loads((out/'quality_completed_comparison.json').read_text())['eligible']
def valid(value):
    return all(valid(v) for v in value.values()) if isinstance(value,dict) else value is True
checks['all_passed'] = valid(checks)
(out/'final_verification.json').write_text(json.dumps(checks,indent=2))
print(json.dumps(checks,indent=2))
assert checks['all_passed']
decision['final_verification'] = 'PASSED'
decision['completed_quality_descriptive'] = json.loads((out/'quality_completed_comparison.json').read_text())
(out/'decision.json').write_text(json.dumps(decision,indent=2))
