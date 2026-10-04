"""Reuse the registered baseline dependencies; overwrite only M5-owned edits."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
name = sys.argv[1]
source = OUT/name/'source'
source.parent.mkdir(exist_ok=False)
shutil.copytree(OUT/'before/source', source)
owned = ['models/routing/solvers/alns/solver.py',
         'models/routing/solvers/alns/multibatch_repair.py']
for path in owned:
    shutil.copy2(ROOT/path, source/path)
test = ROOT/'tests/routing/test_model5_regret_repair.py'
shutil.copy2(test, source.parent/test.name)
hashes = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in sorted(source.rglob('*.py'))}
record = dict(candidate=name, owned_files=owned, source_sha256=hashes,
    test_sha256=hashlib.sha256(test.read_bytes()).hexdigest(),
    dependency_policy='All dependencies from the round6 baseline; only listed M5 files updated')
(source.parent/'freeze.json').write_text(json.dumps(record, indent=2)+'\n')
print(name, 'frozen; only', owned, 'updated')
