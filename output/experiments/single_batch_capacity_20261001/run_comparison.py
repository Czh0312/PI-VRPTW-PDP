"""Whole frozen 24-case phases, seed42, 10s budget, one solver thread."""
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from experiments.runs.run_multibatch_cycle import source_snapshot, run_worker, write_json

OUT = Path(__file__).resolve().parent
phase = sys.argv[1]
out = OUT/phase
out.mkdir()
if phase == 'wall_before':
    shutil.copytree(OUT/'before/source_snapshot', out/'source_snapshot')
    shutil.copy2(OUT/'before/source_hashes.json', out/'source_hashes.json')
else:
    write_json(out/'source_hashes.json', source_snapshot(out))
previous = ROOT/'output/experiments/single_batch_strengthening_20260930'
manifest = json.loads((previous/'manifest.json').read_text())
write_json(OUT/'manifest.json', dict(jobs=manifest['jobs'], input_manifest=str(previous/'manifest.json'),
    protocol='24 frozen cases; 10s total solve budget; seed42; one thread; sequential phases; 20s outer allowance.'))
rows = []
for job in manifest['jobs']:
    row = run_worker(job, out, 20)
    rows.append(row)
    write_json(out/'summary.json', rows)
    print(job['run'], row['status'], row['objective'], row['mip_gap'], round(row['wall_time'], 2), flush=True)
