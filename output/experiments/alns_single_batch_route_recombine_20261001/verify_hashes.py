"""Verify immutable sources, frozen inputs and the owned workspace edits."""
import hashlib
import json
from pathlib import Path

out = Path(__file__).resolve().parent
root = out.parents[2]

def hashes(base):
    return {str(p.relative_to(base)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(base.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}

checks = {}
for source in ('before_source', 'after_source', 'adopted_source', 'restored_source'):
    expected = out/(source+'_manifest.json')
    if expected.exists():
        checks[source] = hashes(out/source) == json.loads(expected.read_text())
checks['inputs'] = hashes(out/'benchmark_inputs') == json.loads((out/'input_hashes_completed.json').read_text())
expected_owned = out/'restored_owned_hashes.json'
if not expected_owned.exists():
    expected_owned = out/'owned_hashes.json'
checks['owned'] = all(hashlib.sha256((root/f).read_bytes()).hexdigest() == sha
                      for f, sha in json.loads(expected_owned.read_text()).items())
if (out/'restored_owned_hashes.json').exists():
    checks['archived_tests'] = all(
        hashlib.sha256((out/'rejected_tests'/Path(f).name).read_bytes()).hexdigest() == sha
        for f, sha in json.loads((out/'owned_hashes.json').read_text()).items()
        if f.startswith('tests/'))
(out/'verification.json').write_text(json.dumps(checks,indent=2))
print(json.dumps(checks,indent=2))
assert all(checks.values())
