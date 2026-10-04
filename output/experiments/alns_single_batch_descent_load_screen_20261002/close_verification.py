"""Verify active/restored owned files and immutable evidence including failures."""
from pathlib import Path
import hashlib, json, re
OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
OWN = ['models/routing/solvers/alns/single_batch.py',
       'models/routing/solvers/alns/single_batch_descent.py']
TEST = 'tests/routing/test_single_batch_descent_load_screen.py'
QUEUE_CONFIRM = OUT.parent/'alns_single_batch_descent_queue_confirm_20261002'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def hashes(base):
    return {str(p.relative_to(base)): sha(p) for p in sorted(base.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts}
def frozen(folder):
    checks = {}
    for source in ('before_source', 'after_source', 'disabled_source', 'adopted_source', 'restored_source'):
        path = folder/(source+'_manifest.json')
        if path.exists(): checks[source] = hashes(folder/source) == json.loads(path.read_text())
    path = folder/'input_hashes_completed.json'
    if not path.exists(): path = folder/'input_hashes.json'
    checks['inputs'] = hashes(folder/'benchmark_inputs') == json.loads(path.read_text())
    path = folder/'registration_hashes.json'
    if path.exists(): checks['registration'] = all(sha(folder/f) == h for f, h in json.loads(path.read_text()).items())
    return checks
decision = json.loads((OUT/'decision.json').read_text())
rejected = decision['reversal_required']
checks = {'current': frozen(OUT)}
checks['two_owned_source_differences'] = sorted(json.loads((OUT/'source_scope.json').read_text())['source_differences']) == sorted(OWN)
origins = json.loads((OUT/'input_origins.json').read_text())
origin_checks = []
for name, origin in origins.items():
    if not origin['path_only']:
        origin_checks.append(sha(OUT/name) == origin['sha256']); continue
    before = json.loads(Path(origin['source']).read_text())
    after = json.loads((OUT/name).read_text())
    keys = {'data', 'orders', 'routing_config', 'pool_config', 'container_pool_config', 'dataset_metadata'}
    origin_checks.append(set(before) == set(after) and all(v == before[k] or (k in keys and isinstance(v, str) and Path(v).is_file() and str(Path(v).resolve()).startswith(str(OUT))) for k, v in after.items()))
checks['unchanged_business_origins'] = all(origin_checks)
checks['preflight'] = all(r['passed'] for r in json.loads((OUT/'input_preflight.json').read_text()))
disabled = json.loads((OUT/'disabled_equivalence.json').read_text())
checks['disabled_two100_exact_audited'] = len(disabled) == 2 and all(r['exact'] and r['valid'] for r in disabled)
rows = json.loads((OUT/'quality/summary.json').read_text())
checks['all16_results_saved'] = len(rows) == 16 and all((OUT/'quality'/('%03d_%s.log' % (r['index'], r['source']))).is_file() for r in rows)
checks['quality_audit_gate_consistent'] = json.loads((OUT/'quality_comparison.json').read_text())['all16_valid'] == all(r.get('valid') and r.get('periodic') for r in rows)
fresh = json.loads((OUT/'fresh_decode.json').read_text())
checks['all16_uncached_redecode_equal_audited'] = len(fresh) == 16 and all(r['valid'] and r['equal'] for r in fresh)
checks['decision_matches_gates'] = rejected == (not (decision['quality_gate_passed'] and decision['mechanism_gate_passed'] and decision['fresh_decode_gate_passed']))
expected = json.loads((OUT/'owned_hashes.json').read_text())
if rejected:
    checks['only_owned_modules_restored'] = all(sha(ROOT/f) == sha(OUT/'before_source'/f) for f in OWN)
    checks['restored_snapshot_exact'] = json.loads((OUT/'restored_source_manifest.json').read_text()) == json.loads((OUT/'before_source_manifest.json').read_text())
    checks['new_test_archived'] = not (ROOT/TEST).exists() and sha(OUT/'rejected_tests'/Path(TEST).name) == expected[TEST]
else:
    checks['candidate_owned_preserved'] = all(sha(ROOT/f) == h for f, h in expected.items())
    checks['adopted_snapshot_exact'] = json.loads((OUT/'adopted_source_manifest.json').read_text()) == json.loads((OUT/'after_source_manifest.json').read_text())
accepted = json.loads((OUT/'accepted_owned_hashes.json').read_text())
checks['late_pickup_test_preserved'] = sha(ROOT/'tests/routing/test_single_batch_late_pickup.py') == accepted['tests/routing/test_single_batch_late_pickup.py']
checks['accepted_late_pickup_exact_control'] = sha(OUT/'before_source'/OWN[0]) == accepted[OWN[0]]
checks['rejected_queue_test_stays_archived'] = not (ROOT/'tests/routing/test_single_batch_descent_queue.py').exists()
live = json.loads((OUT/'live_equivalence.json').read_text())
checks['all8_public_groups'] = len(live) == 8 and all(r['identical'] and r['valid'] if rejected else r['candidate_live_exact'] and r['valid'] and (r['model'] == 3 or r['baseline_equal']) for r in live)
regression = json.loads((OUT/'regression_result.json').read_text())
summary = re.search(r'\d+ passed(?:, \d+ warnings)? in [0-9.]+s', (OUT/'full_regression.log').read_text())
checks['fresh_full_regression'] = regression['exit_code'] == 0 and summary is not None
names = ['descent_queue_confirm', 'descent_queue', 'late_pickup_confirm', 'late_pickup', 'e1_trials', 'vehicle_seed']
archives = [(n, OUT.parent/('alns_single_batch_'+n+'_20261002')) for n in names]
archives += [(n, OUT.parent/('alns_single_batch_'+n+'_20261001')) for n in ('audited_tail', 'terminal_tail', 'lazy_pickup', 'route_recombine', 'compact_repair', 'route_group', 'improving_groups', 'delivery_prefix', 'event_capacity', 'direct_graph', 'direct_relax')]
history = {f: set() for f in OWN}
for folder in [OUT]+[p for _, p in archives]:
    for source in ('before_source', 'after_source', 'adopted_source', 'restored_source'):
        path = folder/(source+'_manifest.json')
        if path.exists():
            manifest = json.loads(path.read_text())
            for f in OWN:
                if f in manifest: history[f].add(manifest[f])
def present_owned(f, expected_hash):
    if (ROOT/f).exists(): return sha(ROOT/f) == expected_hash
    choices = [OUT/'rejected_tests'/Path(f).name, QUEUE_CONFIRM/'rejected_tests'/Path(f).name]
    return any(p.is_file() and sha(p) == expected_hash for p in choices)
for label, folder in archives:
    c = frozen(folder)
    path = folder/'restored_owned_hashes.json'
    restored = path.exists()
    if not restored: path = folder/'owned_hashes.json'
    if not path.exists(): path = folder/'live_owned_hashes.json'
    old = json.loads(path.read_text())
    c['other_owned_preserved'] = all(present_owned(f, h) for f, h in old.items() if f not in OWN)
    c['overlapping_source_history_preserved'] = all(old[f] in history[f] for f in OWN if f in old)
    if restored:
        c['archived_tests'] = all(sha(folder/'rejected_tests'/Path(f).name) == h for f, h in json.loads((folder/'owned_hashes.json').read_text()).items() if f.startswith('tests/'))
    checks[label] = c
joint = OUT.parent/'alns_single_batch_joint_supply_diagnostic_20261002'
checks['joint_diagnostic_archive'] = dict(
    source=hashes(joint/'frozen_source') == json.loads((joint/'source_manifest.json').read_text()),
    inputs=hashes(joint/'benchmark_inputs') == json.loads((joint/'input_hashes.json').read_text()),
    incumbents=hashes(joint/'incumbents') == json.loads((joint/'incumbent_hashes.json').read_text()),
    closure=all(sha(joint/f) == h for f, h in json.loads((joint/'closure_hashes.json').read_text()).items()),
    all_raw_paths_present=all((joint/f).is_file() for f in json.loads((joint/'raw_result_hashes.json').read_text())))
def valid(value): return all(valid(v) for v in value.values()) if isinstance(value, dict) else value is True
checks['all_passed'] = valid(checks)
(OUT/'final_verification.json').write_text(json.dumps(checks, indent=2))
print(json.dumps(checks, indent=2))
assert checks['all_passed']
decision.update(verification='PASSED', regression_summary=summary.group(0), regression_scope='Fresh active or restored full suite', prior_archives_preserved=18)
(OUT/'decision.json').write_text(json.dumps(decision, indent=2))
