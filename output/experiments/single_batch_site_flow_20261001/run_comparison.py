"""Whole 24-case phases; common snapshot differs only in B1 formulation."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from experiments.runs.run_multibatch_cycle import source_snapshot, run_worker, write_json

OUT = Path(__file__).resolve().parent
selected, before = OUT/'wall_selected', OUT/'wall_before'
selected.mkdir()
hashes = source_snapshot(selected)
write_json(selected/'source_hashes.json', hashes)
shutil.copytree(selected/'source_snapshot', before/'source_snapshot')
relative = 'models/routing/solvers/exact/single_batch_formulation.py'
shutil.copy2(OUT/'before/source_snapshot'/relative, before/'source_snapshot'/relative)
before_hashes = dict(hashes)
before_hashes[relative] = hashlib.sha256((before/'source_snapshot'/relative).read_bytes()).hexdigest()
write_json(before/'source_hashes.json', before_hashes)
previous = ROOT/'output/experiments/single_batch_strengthening_20260930'
manifest = json.loads((previous/'manifest.json').read_text())
write_json(OUT/'manifest.json', dict(jobs=manifest['jobs'], input_manifest=str(previous/'manifest.json'),
    protocol='24 frozen cases; 10s total solve budget; seed42; one thread; whole sequential phases; '
             '20s outer allowance; snapshots differ only in B1 formulation.'))
for phase in (before, selected):
    rows = []
    for job in manifest['jobs']:
        row = run_worker(job, phase, 20)
        rows.append(row)
        write_json(phase/'summary.json', rows)
        print(phase.name, job['run'], row['status'], row['objective'], row['mip_gap'],
              round(row['wall_time'], 2), flush=True)
