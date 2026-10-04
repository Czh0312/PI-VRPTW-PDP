"""Read immutable evidence only; support the original nested closure schema."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
GENERAL = OUT.parent / 'alns_single_batch_general_score_branch_20261002'
LATE = OUT.parent / 'alns_single_batch_late_pickup_confirm_20261002'
PILOT = OUT.parent / 'alns_single_batch_late_pickup_20261002'
manifest_counts = {}


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(base, manifest_path):
    payload = read(manifest_path)
    if 'files' in payload:
        # The original 02:26 confirmation manifest includes capture metadata.
        assert set(payload) == {'captured_after_verification', 'files'}
        hashes = payload['files']
    else:
        hashes = payload
    assert all(isinstance(digest, str) and len(digest) == 64 for digest in hashes.values())
    manifest_counts[str(manifest_path.relative_to(ROOT))] = len(hashes)
    return all((base / file).is_file() and sha(base / file) == digest
               for file, digest in hashes.items())


checks = {
    'general_closed48files': verify(GENERAL, GENERAL / 'closure_hashes.json'),
    'general_raw174files': verify(GENERAL, GENERAL / 'raw_result_hashes.json'),
    'general_restored_owned4': verify(ROOT, GENERAL / 'restored_owned_hashes.json'),
    'accepted_late_confirmation_closure': verify(LATE, LATE / 'closure_hashes.json'),
    'accepted_late_current_owned': verify(ROOT, LATE / 'live_owned_hashes.json'),
    'accepted_late_confirmation_inputs': verify(LATE / 'benchmark_inputs', LATE / 'input_hashes.json'),
    'accepted_late_confirmation_registration': verify(LATE, LATE / 'registration_hashes.json'),
}
for source in ('before_source', 'after_source', 'disabled_source', 'adopted_source'):
    checks['late_confirmation_' + source] = verify(LATE / source, LATE / (source + '_manifest.json'))
    checks['late_pilot_' + source] = verify(PILOT / source, PILOT / (source + '_manifest.json'))
checks['accepted_late_pilot_inputs'] = verify(PILOT / 'benchmark_inputs', PILOT / 'input_hashes.json')
checks['accepted_late_pilot_registration'] = verify(PILOT, PILOT / 'registration_hashes.json')
quality, decision = read(LATE / 'quality_comparison.json'), read(LATE / 'decision.json')
checks['accepted_late_original_confirmation_passed'] = (quality['eligible'] and
    quality['all16_valid'] and decision['quality_gate_passed'] and
    decision['mechanism_gate_passed'] and decision['verification'] == 'PASSED')
pilot_quality = read(PILOT / 'quality_comparison.json')
checks['accepted_late_original_pilot_passed'] = (pilot_quality['eligible'] and
    pilot_quality['all16_valid'] and read(PILOT / 'decision.json')['verification'].startswith('PASSED'))
result = dict(updated=datetime.now(timezone.utc).isoformat(), checks=checks,
    all_passed=all(checks.values()), manifest_counts=manifest_counts,
    active_owned_process=None, general_raw_files=174, general_closure_files=48,
    accepted_only='M3 late E1pickup uphill veto; finite5/15 and30/60 matrices, no stable/causal/GAP claim',
    accepted_current_owned=read(LATE / 'live_owned_hashes.json'),
    schema_correction='First helper treated nested files+capture metadata as a flat map. Original first failure preserved; inspect literal files member, no historical manifest or results changed.',
    external_process_inventory='Prior sandbox ps restriction; load observation only, no isolation claim',
    load=os.getloadavg(),
    verification_limit='Immutable SHA checks, no fresh solver runs or rerun of entire historical/archive diagnostic chain.')
(OUT / 'wrapup_integrity_20261002.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
assert result['all_passed']
