"""Freeze two owned production changes, disabled source, and preserved tests."""
from pathlib import Path
import hashlib, json, shutil

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
OWN = ['models/routing/solvers/alns/single_batch.py',
       'models/routing/solvers/alns/single_batch_descent.py']
TEST = 'tests/routing/test_single_batch_descent_load_screen.py'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def hashes(base):
    return {str(p.relative_to(base)): sha(p) for p in sorted(base.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts}
def write(name, value): (OUT/name).write_text(json.dumps(value, indent=2))
before = json.loads((OUT/'before_source_manifest.json').read_text())
assert hashes(OUT/'before_source') == before
assert all(sha(ROOT/f) == h for f, h in before.items() if f not in OWN), 'Shared source drift: freeze a new context, never overwrite'
assert sha(ROOT/'tests/routing/test_single_batch_late_pickup.py') == json.loads((OUT/'accepted_owned_hashes.json').read_text())['tests/routing/test_single_batch_late_pickup.py']
shutil.copytree(OUT/'before_source', OUT/'after_source')
for f in OWN: shutil.copy2(ROOT/f, OUT/'after_source'/f)
after = hashes(OUT/'after_source')
changes = [f for f in before if before[f] != after[f]]
assert sorted(changes) == sorted(OWN)
write('source_scope.json', dict(source_differences=changes, owned_production=OWN))
write('after_source_manifest.json', after)
shutil.copytree(OUT/'after_source', OUT/'disabled_source')
module = OUT/'disabled_source'/OWN[0]
s = module.read_text()
assert s.count('_descent_load_screen=True') == 1
module.write_text(s.replace('_descent_load_screen=True', '_descent_load_screen=False'))
write('disabled_source_manifest.json', hashes(OUT/'disabled_source'))
owned = {f: sha(ROOT/f) for f in OWN+[TEST]}
write('owned_hashes.json', owned)
write('current_owned_hashes.json', {**owned, 'tests/routing/test_single_batch_late_pickup.py': sha(ROOT/'tests/routing/test_single_batch_late_pickup.py')})
import difflib
(OUT/'candidate.diff').write_text(''.join(''.join(difflib.unified_diff((OUT/'before_source'/f).read_text().splitlines(True), (ROOT/f).read_text().splitlines(True), fromfile='before/'+f, tofile='after/'+f)) for f in OWN))
print(json.dumps(dict(source_files=len(after), differences=changes)))
