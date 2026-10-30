"""Recheck every frozen returned value for the size-scoped release."""
from argparse import Namespace, ArgumentParser
import os
from pathlib import Path
import subprocess
import sys
from run import HERE, ROOT, worker, read, save, sha

parser = ArgumentParser()
parser.add_argument('--case')
args = parser.parse_args()
if args.case:
    worker(Namespace(variant='released', case=args.case, mode='replay', seconds=5., seed=42, phase='release'))
else:
    hashes = {str(p.relative_to(HERE / 'sources' / 'released')): sha(p)
              for p in (HERE / 'sources' / 'released').rglob('*.py')}
    save(HERE / 'released_source_hashes.json', hashes)
    rows = []
    for case in ['S02', 'S03', 'S09', 'M01', 'L01']:
        command = [sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), '--case', case]
        completed = subprocess.run(command, cwd=ROOT, env={**os.environ, 'PYTHONHASHSEED': '0'},
            capture_output=True, text=True, encoding='utf-8', timeout=240)
        if completed.returncode:
            raise RuntimeError(completed.stdout+completed.stderr)
        print(completed.stdout.strip(), flush=True)
        reference = read(HERE / 'replays' / 'baseline' / f'{case}.json')
        result = read(HERE / 'replays' / 'released' / f'{case}.json')
        assert reference['value_sha256'] == result['value_sha256']
        rows.append(dict(case=case, evaluations=result['records'], identical=True))
    assert all(sha(HERE / 'sources' / 'released' / name) == digest for name, digest in hashes.items())
    save(HERE / 'released_replay_validation.json', dict(rows=rows,
        total_equal_evaluations=sum(r['evaluations'] for r in rows),
        published_scope='Single-audit sizing for at most nine orders; original evaluation above nine.',
        original_62_trials_scope='Initial candidate applied the shortcut to all sizes; retained unchanged.'))
