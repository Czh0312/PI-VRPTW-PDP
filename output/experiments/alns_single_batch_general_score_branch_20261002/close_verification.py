"""Close the entire failed quality trial without altering its registered gates."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import re

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
PRIOR = OUT.parent / 'alns_single_batch_static_route_metadata_20261002'
DIAG = OUT.parent / 'alns_single_batch_model_hotpath_20261002'
PROFILE = OUT.parent / 'alns_single_batch_route_path_cache_20261002'
SCORER = 'models/routing/solvers/alns/single_batch_scoring.py'
TEST = 'tests/routing/test_single_batch_general_score_branch.py'


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(base, manifest):
    return all((base / file).is_file() and sha(base / file) == digest
               for file, digest in read(manifest).items())


checks = {}
for source in ('before_source', 'after_source', 'disabled_source', 'restored_source'):
    checks[source] = verify(OUT / source, OUT / (source + '_manifest.json'))
checks['inputs'] = verify(OUT / 'benchmark_inputs', OUT / 'input_hashes.json')
checks['initial_registration'] = verify(OUT, OUT / 'initial_registration_hashes.json')
checks['final_registration'] = verify(OUT, OUT / 'registration_hashes.json')
checks['completed_pre_quality_evidence'] = verify(OUT, OUT / 'completed_pre_quality_hashes.json')
preflight = read(OUT / 'input_preflight.json')
checks['input_preflight16'] = len(preflight) == 16 and all(
    row['passed'] and row['precheck_passed'] and row['all_paths_closed'] for row in preflight)
checks['root_owned_restored'] = verify(ROOT, OUT / 'restored_owned_hashes.json')
checks['candidate_snapshot_preserved'] = verify(OUT / 'candidate_owned', OUT / 'candidate_owned_hashes.json')
checks['only_new_test_archived'] = (sha(OUT / 'rejected_tests' / TEST) ==
    read(OUT / 'candidate_owned_hashes.json')[TEST] and not (ROOT / TEST).exists())
before = read(OUT / 'before_source_manifest.json')
after = read(OUT / 'after_source_manifest.json')
restored = read(OUT / 'restored_source_manifest.json')
checks['only_scorer_source_changed'] = [file for file, digest in before.items()
    if after[file] != digest] == [SCORER]
checks['frozen_restore_exact_before'] = restored == before
scope = read(OUT / 'source_scope.json')
checks['original_general_fallback_preserved'] = scope['original_general_fallback_ast_unchanged']
checks['entire_lazy_pickup_preserved'] = scope['lazy_pickup_entire_class_ast_unchanged']
disabled = read(OUT / 'disabled_equivalence.json')
checks['disabled4_exact_audited'] = len(disabled) == 4 and all(
    row['exact'] and row['valid'] for row in disabled)
cpu = read(OUT / 'primary_decision.json')
checks['cpu24_exact_audited'] = cpu['runs'] == 24 and len(cpu['groups']) == 4 and all(
    row['identical'] and row['all_valid'] for row in cpu['groups'])
checks['cpu_all4_original_gates_passed'] = cpu['eligible'] and all(
    row['cpu_reduction_percent'] >= 2 for row in cpu['groups'])
quality = read(OUT / 'quality_comparison.json')
checks['quality16_pairs_complete'] = quality['pairs'] == 16 and len(quality['pair_results']) == 16
checks['quality32_original_audits_valid'] = quality['all32_valid']
checks['whole_quality_failure_retained'] = not quality['eligible'] and any(
    not row['eligible'] for row in quality['summary'])
checks['both_models_all4_strata_retained'] = len(quality['summary']) == 8 and all(
    {row['stratum'] for row in quality['summary'] if row['model'] == model} ==
    {'short', 'long', 'short50', 'short100'} for model in (3, 4))
fresh = read(OUT / 'fresh_decode.json')
checks['fresh32_uncached_full_audits_equal'] = len(fresh) == 32 and all(
    row['valid'] and row['equal'] for row in fresh)
checks['fresh32_raw_decoded_results_saved'] = len(list((OUT / 'fresh_decodes').glob('*.json'))) == 32
regression_log = (OUT / 'full_regression.log').read_text()
regression = re.search(r'(\d+ passed, \d+ warnings in [^\n]+)', regression_log)
checks['fresh_restored_full_regression'] = regression is not None and 'FAILED ' not in regression_log
public = read(OUT / 'live_equivalence.json')
checks['public8groups24_exact_audited'] = len(public) == 8 and all(
    row['identical'] and row['valid'] and row['source'] == 'restored_source' for row in public)
checks['predecessor_closure_bytes'] = verify(PRIOR, PRIOR / 'closure_hashes.json')
checks['predecessor_raw_bytes'] = verify(PRIOR, PRIOR / 'raw_result_hashes.json')
checks['predecessor_verified'] = read(PRIOR / 'decision.json')['verification'] == 'PASSED'
checks['model_diagnostic_closure_bytes'] = verify(DIAG, DIAG / 'closure_hashes.json')
checks['model_diagnostic_raw_bytes'] = verify(DIAG, DIAG / 'raw_result_hashes.json')
checks['model_diagnostic_verified'] = read(DIAG / 'decision.json')['verification'] == 'PASSED'
checks['path_diagnostic_raw_bytes'] = verify(PROFILE, PROFILE / 'raw_result_hashes.json')

result = dict(checks=checks, all_passed=all(checks.values()),
    regression_summary=regression.group(1) if regression else None,
    source_files=len(before), input_files=len(read(OUT / 'input_hashes.json')),
    predecessor_closure_files=len(read(PRIOR / 'closure_hashes.json')),
    predecessor_raw_files=len(read(PRIOR / 'raw_result_hashes.json')),
    diagnostic_raw_files=len(read(DIAG / 'raw_result_hashes.json')),
    previous_verified_archives=19,
    verification_limit='Direct predecessor/diagnostic immutable SHA preservation; not a fresh rerun of all historical trials or the 2.2GB joint diagnostic.',
    updated=datetime.now(timezone.utc).isoformat(), load=os.getloadavg(),
    shared_source_drift=[file for file, digest in before.items()
        if (ROOT / file).is_file() and sha(ROOT / file) != digest])
(OUT / 'final_verification.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
assert result['all_passed']
