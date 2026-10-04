"""Run all preregistered controlled diagnostics serially; retain failures."""
from pathlib import Path
import json
import os
import subprocess
import sys

OUT = Path(__file__).resolve().parent
jobs = json.loads((OUT / 'jobs.json').read_text())
(OUT / 'runs').mkdir(exist_ok=True)
rows = []
for index, job in enumerate(jobs):
    try:
        p = subprocess.run([sys.executable, str(OUT / 'worker.py'), str(index)],
                           capture_output=True, text=True, timeout=80,
                           env={**os.environ, 'PYTHONHASHSEED': '0'})
        (OUT / 'runs' / ('%02d.log' % index)).write_text(p.stdout + '\n' + p.stderr)
        if p.returncode:
            row = dict(index=index, job=job, exit_code=p.returncode, failed=True)
        else:
            row = json.loads(p.stdout)
            row.update(index=index, exit_code=0, failed=False)
    except subprocess.TimeoutExpired as e:
        row = dict(index=index, job=job, failed=True, timeout=True)
        (OUT / 'runs' / ('%02d.log' % index)).write_text(str(e))
    (OUT / 'runs' / ('%02d.json' % index)).write_text(json.dumps(row, indent=2))
    rows.append(row)
    (OUT / 'summary.json').write_text(json.dumps(rows, indent=2))
    print(index, job['family'], job['seed'], job['clock_scale'], job['source'],
          row.get('objective'), row.get('failed'), flush=True)
