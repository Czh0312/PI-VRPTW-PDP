"""Frozen sequential 24-case/10-second diagnostic, preserving every outcome."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from experiments.runs.run_multibatch_cycle import source_snapshot, run_worker, write_json

OUT = Path(__file__).resolve().parent
previous = ROOT/'output/experiments/single_batch_strengthening_20260930'
phase = sys.argv[1]
out = OUT/phase
out.mkdir()
manifest = json.loads((previous/'manifest.json').read_text())
write_json(OUT/'manifest.json', dict(jobs=manifest['jobs'], protocol='Same frozen cases, seed42, one thread, 10s solve budget, 20s outer overhead. Sequential within each phase.', input_manifest=str(previous/'manifest.json')))
write_json(out/'source_hashes.json', source_snapshot(out))
rows = []
for job in manifest['jobs']:
    row = run_worker(job, out, 20)
    rows.append(row)
    write_json(out/'summary.json', rows)
    print(job['run'], row['status'], row['objective'], row['mip_gap'], round(row['wall_time'], 2), flush=True)
