"""Verify rejection, restored solvers and preserved evidence without rerunning timing."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
checks={}
for label,folder in [('vehicle_seed',OUT)]+[
    (name,OUT.parent/('alns_single_batch_'+name+'_20261001'))
    for name in ('audited_tail','terminal_tail','lazy_pickup','route_recombine','compact_repair',
                 'route_group','improving_groups','delivery_prefix','event_capacity','direct_graph','direct_relax')]:
    process=subprocess.run([sys.executable,str(folder/'verify_hashes.py')],capture_output=True,text=True,check=True)
    checks[label]=json.loads(process.stdout)
checks['registration_preserved']=all(hashlib.sha256((OUT/f).read_bytes()).hexdigest()==sha
    for f,sha in json.loads((OUT/'registration_hashes.json').read_text()).items())
checks['restoration_exact']=(json.loads((OUT/'before_source_manifest.json').read_text())==
                             json.loads((OUT/'restored_source_manifest.json').read_text()))
rows=json.loads((OUT/'quality/summary.json').read_text())
checks['all16_audits_and_replays']=len(rows)==16 and all(r['valid'] and r['periodic'] for r in rows)
live=json.loads((OUT/'live_equivalence.json').read_text())
checks['all8_public_model_groups']=len(live)==8 and all(r['identical'] and r['valid'] for r in live)
checks['full_regression']='2473 passed, 14 warnings in 66.79s' in (OUT/'full_regression.log').read_text()
checks['candidate_tests_archived']=not (ROOT/'tests/routing/test_single_batch_vehicle_seed.py').exists()
checks['rejected_by_original_gates']=not json.loads((OUT/'quality_comparison.json').read_text())['eligible']
checks['input_preflight']=all(r['passed'] for r in json.loads((OUT/'input_preflight.json').read_text()))
def valid(value):
    return all(valid(v) for v in value.values()) if isinstance(value,dict) else value is True
checks['all_passed']=valid(checks)
(OUT/'final_verification.json').write_text(json.dumps(checks,indent=2))
print(json.dumps(checks,indent=2))
assert checks['all_passed']
decision=json.loads((OUT/'decision.json').read_text())
decision.update(verification='PASSED_FULL_REGRESSION_AND_PUBLIC_EQUIVALENCE',final_verification='PASSED')
(OUT/'decision.json').write_text(json.dumps(decision,indent=2))
