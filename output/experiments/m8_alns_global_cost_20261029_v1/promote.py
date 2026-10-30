"""Copy only the reviewed M8 changes after checking the frozen baseline."""
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


files = read(HERE/'changed_sources.json')
assert set(files) == {
    'models/routing/model_8/alns/calendar_polish.py',
    'models/routing/model_8/alns/chain_repair.py',
    'models/routing/model_8/alns/fixed_calendar_search.py',
    'models/routing/model_8/alns/search.py',
    'models/routing/model_8/alns/temporal_domains.py',
    'models/routing/model_8/alns/k_search/scheduler.py',
}
analysis = read(HERE/'analysis.json')['phases']['final']
assert analysis['variants']['bounded']['mean_gap_percent'] < analysis['variants']['baseline']['mean_gap_percent']
assert all(row['valid'] and row['periodic_valid'] for row in analysis['rows'])
expected = read(HERE/'final_plan.json')['variants']['bounded']
test_path = ROOT/'tests/routing/test_model8_chain_repair.py'
assert not test_path.exists(), str(test_path)
for relative in files:
    original, target, candidate = HERE/'sources/baseline'/relative, ROOT/relative, HERE/'sources/bounded'/relative
    assert sha(candidate) == expected[relative]
    if original.exists():
        assert target.exists() and sha(target) == sha(original), str(target)
    else:
        assert not target.exists(), str(target)
for relative in files:
    shutil.copy2(HERE/'sources/bounded'/relative, ROOT/relative)
shutil.copy2(HERE/'drafts/chain_checks.py', test_path)
manifest = {relative: sha(ROOT/relative) for relative in files}
manifest[test_path.relative_to(ROOT).as_posix()] = sha(test_path)
(HERE/'promotion.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(dict(promoted=files, new_tests=str(test_path)), ensure_ascii=False))
