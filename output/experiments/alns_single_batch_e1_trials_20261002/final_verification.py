"""Verify rejected trial cache and restoration; never rerun timing experiments."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
checks = {}
archives = [('e1_trials', OUT), ('vehicle_seed', OUT.parent / 'alns_single_batch_vehicle_seed_20261002')]
archives += [(name, OUT.parent / ('alns_single_batch_' + name + '_20261001'))
             for name in ('audited_tail', 'terminal_tail', 'lazy_pickup', 'route_recombine',
                          'compact_repair', 'route_group', 'improving_groups', 'delivery_prefix',
                          'event_capacity', 'direct_graph', 'direct_relax')]
for label, folder in archives:
    p = subprocess.run([sys.executable, str(folder / 'verify_hashes.py')],
                       capture_output=True, text=True, check=True)
    checks[label] = json.loads(p.stdout)
checks['registration_preserved'] = all(
    hashlib.sha256((OUT / f).read_bytes()).hexdigest() == sha
    for f, sha in json.loads((OUT / 'registration_hashes.json').read_text()).items())
checks['restoration_exact'] = (json.loads((OUT / 'before_source_manifest.json').read_text()) ==
                               json.loads((OUT / 'restored_source_manifest.json').read_text()))
rows = json.loads((OUT / 'quality/summary.json').read_text())
checks['all16_audits_and_replays'] = len(rows) == 16 and all(r['valid'] and r['periodic'] for r in rows)
live = json.loads((OUT / 'live_equivalence.json').read_text())
checks['all8_public_groups'] = len(live) == 8 and all(r['identical'] and r['valid'] for r in live)
regression = json.loads((OUT / 'regression_result.json').read_text())
log = (OUT / 'full_regression.log').read_text()
summary = re.search(r'(\d+) passed, 14 warnings in ([0-9.]+)s', log)
checks['full_regression'] = regression['exit_code'] == 0 and summary is not None
checks['candidate_tests_archived'] = not (ROOT / 'tests/routing/test_single_batch_e1_trial_cache.py').exists()
checks['rejected_by_original_gates'] = not json.loads((OUT / 'quality_comparison.json').read_text())['eligible']
checks['primary_cpu_passed'] = json.loads((OUT / 'primary_decision.json').read_text())['eligible']
checks['input_preflight'] = all(r['passed'] for r in json.loads((OUT / 'input_preflight.json').read_text()))

def valid(value):
    return all(valid(v) for v in value.values()) if isinstance(value, dict) else value is True

checks['all_passed'] = valid(checks)
(OUT / 'final_verification.json').write_text(json.dumps(checks, indent=2))
print(json.dumps(checks, indent=2))
assert checks['all_passed']
decision = json.loads((OUT / 'decision.json').read_text())
decision.update(verification='PASSED_FULL_REGRESSION_AND_PUBLIC_EQUIVALENCE', final_verification='PASSED',
                restored_regression_summary=summary.group(0))
(OUT / 'decision.json').write_text(json.dumps(decision, indent=2))
