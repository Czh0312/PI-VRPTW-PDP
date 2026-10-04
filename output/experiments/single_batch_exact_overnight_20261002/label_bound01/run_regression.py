"""Retain one full observed-state verification after all timed workers finish."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
OWNED = ('models/routing/solvers/exact/single_batch_route_pricing.py',
         'models/routing/solvers/exact/single_batch_columns.py',
         'models/routing/solvers/exact/single_batch_label_bound.py',
         'tests/routing/test_single_batch_label_bound.py')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes():
    return {str(p.relative_to(ROOT)):sha(p) for directory in ('models','configuration','cli','experiments','reporting','tools','tests')
            for p in sorted((ROOT/directory).rglob('*.py'))}


def main():
    rows = json.loads((OUT/'full60_v2/comparison.json').read_text())
    assert len(rows)==8 and not (OUT/'full60_v2/failure.json').exists()
    log = OUT/'full_regression.log'
    assert not log.exists(),'Never overwrite or repeat without an actual new failure/change'
    before = hashes()
    snapshot = OUT/'test_source_snapshot';snapshot.mkdir()
    for path in before:
        if path.startswith('tests/'):
            target = snapshot/path;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ROOT/path,target)
    command = [sys.executable,'-m','pytest','-q']
    begun = time.perf_counter()
    with log.open('w') as stream:
        process = subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,timeout=600)
    after = hashes()
    changed = [p for p in sorted(set(before)|set(after)) if before.get(p)!=after.get(p)]
    count = re.search(r'(\d+) passed(?:, (\d+) warnings)? in ([\d.]+)s',log.read_text())
    frozen = json.loads((OUT/'full60_v2/manifest.json').read_text())['source_sha256']
    record = dict(command=command,exit_code=process.returncode,wall_seconds=time.perf_counter()-begun,
        passed=int(count.group(1)) if count else None,warnings=int(count.group(2) or 0) if count else None,
        pytest_seconds=float(count.group(3)) if count else None,before_sha256=before,after_sha256=after,
        changed_files=changed,owned_unchanged={p:before.get(p)==after.get(p) for p in OWNED},
        runtime_matches_frozen_v2={p:after.get(p)==frozen.get(p) for p in OWNED if not p.startswith('tests/')},
        test_snapshot=str(snapshot),scope='Observed source state; later/shared file edits are not claimed verified.')
    (OUT/'regression.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:record[k] for k in ('exit_code','passed','warnings','pytest_seconds','changed_files',
        'owned_unchanged','runtime_matches_frozen_v2')},ensure_ascii=False))
    assert process.returncode==0 and all(record['owned_unchanged'].values()) and all(record['runtime_matches_frozen_v2'].values())


if __name__=='__main__':
    main()
