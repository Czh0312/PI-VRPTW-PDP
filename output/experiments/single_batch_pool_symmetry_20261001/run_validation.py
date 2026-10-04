"""One full regression, retaining test/source provenance amid shared edits."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
OWNED = ('models/routing/solvers/exact/single_batch_route_pool.py',
         'models/routing/solvers/exact/single_batch_columns.py',
         'experiments/runs/run_single_batch_columns.py',
         'tests/routing/test_single_batch_pool_symmetry.py')


def sha(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def hashes():
    return {str(p.relative_to(ROOT)):sha(p)
            for directory in ('models','configuration','cli','experiments','reporting','tools','tests')
            for p in sorted((ROOT/directory).rglob('*.py'))}


def main():
    log = OUT/'full_tests.log'
    assert not log.exists(), 'Do not overwrite or repeat a full verification'
    before = hashes()
    snapshot = OUT/'test_source_snapshot'
    snapshot.mkdir()
    for path in before:
        if path.startswith('tests/'):
            target = snapshot/path
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(ROOT/path, target)
            except FileNotFoundError:
                pass  # Shared changes are explicitly recorded below.
    command = [sys.executable, '-m', 'pytest', '-q']
    begin = time.perf_counter()
    with log.open('w') as stream:
        process = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=600)
    wall = time.perf_counter()-begin
    after = hashes()
    changes = [path for path in sorted(set(before)|set(after)) if before.get(path)!=after.get(path)]
    frozen = json.loads((OUT/'final_long/manifest.json').read_text())['source_sha256']
    output = log.read_text()
    passed = re.search(r'(\d+) passed(?:, (\d+) warnings)? in ([\d.]+)s', output)
    result = dict(command=command, exit_code=process.returncode, wall_time=wall,
        tests_passed=int(passed.group(1)) if passed else None,
        warnings=int(passed.group(2) or 0) if passed else None,
        pytest_runtime=float(passed.group(3)) if passed else None,
        before_sha256=before, after_sha256=after, changed_files=changes,
        owned_files_unchanged={p:before.get(p)==after.get(p) for p in OWNED},
        runtime_matches_final_frozen={p:after.get(p)==frozen.get(p) for p in OWNED if not p.startswith('tests/')},
        test_snapshot=str(snapshot),
        initial_targeted='326 passed in 2.32s', first_version_targeted='495 passed in 5.67s', final_targeted='496 passed in 5.77s',
        scope='Full run at observed source state; unrelated later/shared edits are not claimed verified.')
    (OUT/'validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('exit_code','tests_passed','warnings','pytest_runtime','changed_files','owned_files_unchanged','runtime_matches_final_frozen')},ensure_ascii=False))
    assert process.returncode == 0, 'Full verification failed; log and hashes retained'
    assert all(result['owned_files_unchanged'].values()) and all(result['runtime_matches_final_frozen'].values())


if __name__ == '__main__':
    main()
