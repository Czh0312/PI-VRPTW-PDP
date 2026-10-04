"""Restore only trial-owned files after the complete registered quality failure."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import shutil

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
SCORER = 'models/routing/solvers/alns/single_batch_scoring.py'
TEST = 'tests/routing/test_single_batch_general_score_branch.py'
PROTECTED = ('models/routing/solvers/alns/single_batch.py',
             'models/routing/solvers/alns/single_batch_descent.py',
             'tests/routing/test_single_batch_late_pickup.py')


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


quality = read(OUT / 'quality_comparison.json')
fresh = read(OUT / 'fresh_decode.json')
assert quality['pairs'] == 16 and len(quality['pair_results']) == 16
assert not quality['eligible'], 'No registered failure; do not withdraw.'
assert len(fresh) == 32, 'Finish all registered fresh decodes first.'
assert len(read(OUT / 'quality/summary.json')) == 32
owned = read(OUT / 'candidate_owned_hashes.json')
assert set(owned) == {SCORER, TEST}
assert all(sha(ROOT / file) == digest for file, digest in owned.items()), 'Owned files changed externally.'
assert all(sha(OUT / 'candidate_owned' / file) == digest for file, digest in owned.items())
before = read(OUT / 'before_source_manifest.json')
assert sha(OUT / 'before_source' / SCORER) == before[SCORER]
protected = {file: sha(ROOT / file) for file in PROTECTED}
baseline_scorer = (OUT / 'before_source' / SCORER).read_bytes()
archive = OUT / 'rejected_tests' / TEST
assert not archive.exists() and not (OUT / 'restored_source').exists()
archive.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(ROOT / TEST, archive)
assert sha(archive) == owned[TEST]
(ROOT / SCORER).write_bytes(baseline_scorer)
(ROOT / TEST).unlink()
assert sha(ROOT / SCORER) == before[SCORER]
assert all(sha(ROOT / file) == digest for file, digest in protected.items())
for file, digest in before.items():
    source, target = OUT / 'before_source' / file, OUT / 'restored_source' / file
    assert sha(source) == digest
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
(OUT / 'restored_source_manifest.json').write_text(json.dumps(before, indent=2))
restored = {SCORER: sha(ROOT / SCORER), **protected}
(OUT / 'restored_owned_hashes.json').write_text(json.dumps(restored, indent=2))
decision = dict(decision='REJECTED_QUALITY_GATE', verification='RESTORED_VERIFICATION_PENDING',
    cpu=read(OUT / 'primary_decision.json'), quality=quality,
    fresh32_valid_equal=all(row['valid'] and row['equal'] for row in fresh),
    restored_only=[SCORER], archived_only=[TEST], protected=protected,
    quality_started=True, longer_confirmation_started=False,
    limit='Four CPU gates passed, but registered quality gate failed. No subset adoption, reruns, reseeding, threshold changes or stable speed/quality/GAP claim.',
    updated=datetime.now(timezone.utc).isoformat())
(OUT / 'decision.json').write_text(json.dumps(decision, indent=2))
print(json.dumps({key: value for key, value in decision.items() if key not in ('quality', 'cpu')}, indent=2))
