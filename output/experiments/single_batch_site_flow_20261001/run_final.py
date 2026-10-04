"""One complete final-default phase; preserve all earlier candidate results."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from experiments.runs.run_multibatch_cycle import source_snapshot, run_worker, write_json

OUT = Path(__file__).resolve().parent
out = OUT/'final'
out.mkdir()
write_json(out/'source_hashes.json', source_snapshot(out))
manifest = json.loads((OUT/'manifest.json').read_text())
rows = []
for job in manifest['jobs']:
    row = run_worker(job, out, 20)
    rows.append(row)
    write_json(out/'summary.json', rows)
    print(job['run'], row['status'], row['objective'], row['mip_gap'],
          round(row['wall_time'], 2), flush=True)
