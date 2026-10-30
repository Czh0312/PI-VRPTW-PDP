"""Freeze the existing ALNS implementation before any candidate edits."""
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for name in ['baseline', 'candidate']:
    destination = HERE / 'sources' / name / 'models'
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copytree(ROOT / 'models', destination,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
hashes = {str(p.relative_to(HERE / 'sources' / 'baseline')): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in (HERE / 'sources' / 'baseline').rglob('*.py')}
(HERE / 'baseline_source_hashes.json').write_text(json.dumps(hashes, indent=2), encoding='utf-8')
print({'frozen_python_sources': len(hashes), 'baseline_and_candidate_initially_identical': True})
