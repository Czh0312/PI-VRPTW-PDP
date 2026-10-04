"""M2 cache ablation: exact fixed-work outputs and uninstrumented CPU time."""
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
cases = [ROOT/'output/experiments/alns_large_status_20260930/benchmark_inputs/zhou_Set2_100-5-1b/case.json',
         ROOT/'output/experiments/alns_single_batch_descent_20260930/benchmark_inputs/zhou_Set2_100-10-1/case.json']
rows = []
for repeat in range(3):
    for case in cases:
        versions = ('before_source', 'context_source') if repeat % 2 == 0 else ('context_source', 'before_source')
        for source in versions:
            done = subprocess.run([sys.executable, str(OUT/'fixed_work.py'), str(OUT/source), '2', str(case)],
                capture_output=True, text=True, check=True, timeout=80, env={**os.environ, 'PYTHONHASHSEED': '0'})
            row = json.loads(done.stdout)
            row.update(repeat=repeat, source=source, family=case.parent.name)
            rows.append(row)
            (OUT/'context_fixed_work_runs.json').write_text(json.dumps(rows, indent=2))
            print(repeat, source, case.parent.name, row['cpu'], row['objective'], flush=True)
summary = []
for case in cases:
    a = [r for r in rows if r['family'] == case.parent.name and r['source'] == 'before_source']
    b = [r for r in rows if r['family'] == case.parent.name and r['source'] == 'context_source']
    identical = all(all(r[k] == a[0][k] for k in ('e1', 'e2', 'objective', 'assignment', 'iterations')) for r in a+b)
    assert identical and all(r['valid'] and r['iterations'] == 300 for r in a+b)
    before, after = statistics.median(r['cpu'] for r in a), statistics.median(r['cpu'] for r in b)
    summary.append(dict(model=2, family=case.parent.name, identical=identical, before_cpu=before, after_cpu=after,
        cpu_reduction_percent=100*(1-after/before)))
adopt = all(r['cpu_reduction_percent'] >= 2 for r in summary)
(OUT/'context_fixed_work_summary.json').write_text(json.dumps(dict(summary=summary, adopted=adopt), indent=2))
print(json.dumps(summary, indent=2)); print('adopted', adopt)
