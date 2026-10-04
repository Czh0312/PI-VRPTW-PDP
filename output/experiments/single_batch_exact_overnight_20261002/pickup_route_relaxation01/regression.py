"""One necessary full regression after new optional M3/M4 module, no timing overlap."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[4];OUT=Path(__file__).resolve().parent


def hashes():
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ('models','configuration','tests')
        for p in sorted((ROOT/folder).rglob('*.py'))}


def main():
    assert len(json.loads((OUT/'strength15_30/comparison.json').read_text()))==8
    assert not (OUT/'full_regression.log').exists()
    before=hashes();begun=time.perf_counter()
    with (OUT/'full_regression.log').open('w') as f:
        result=subprocess.run([sys.executable,'-m','pytest','-q'],cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,timeout=600)
    after=hashes();changed=[p for p in sorted(set(before)|set(after)) if before.get(p)!=after.get(p)]
    text=(OUT/'full_regression.log').read_text();match=re.search(r'(\d+) passed(?:, (\d+) warnings)? in ([\d.]+)s',text)
    owned=['models/routing/solvers/exact/single_batch_pickup_bound.py','tests/routing/test_single_batch_pickup_bound.py']
    frozen=json.loads((OUT/'strength15_30/manifest.json').read_text())['source_sha256']
    record=dict(exit_code=result.returncode,passed=int(match.group(1)) if match else None,
        warnings=int(match.group(2) or 0) if match else None,pytest_seconds=float(match.group(3)) if match else None,
        wall_seconds=time.perf_counter()-begun,before_sha256=before,after_sha256=after,changed_files=changed,
        owned_unchanged={p:before[p]==after[p] for p in owned},
        runtime_matches_frozen=after[owned[0]]==frozen[owned[0]],
        scope='Observed source state only; later/shared changes are not declared validated.')
    (OUT/'regression.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    print({k:v for k,v in record.items() if k not in ('before_sha256','after_sha256')})
    assert result.returncode==0 and all(record['owned_unchanged'].values()) and record['runtime_matches_frozen']


if __name__=='__main__':main()
