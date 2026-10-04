"""Secondary fixed-work comparison, allowing search trajectory divergence."""
from pathlib import Path
import json
import os
import statistics
import subprocess
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
cases = [ROOT/'output/experiments/alns_large_status_20260930/benchmark_inputs/zhou_Set2_100-5-1b/case.json',
         ROOT/'output/experiments/alns_single_batch_descent_20260930/benchmark_inputs/zhou_Set2_100-10-1/case.json']
rows = []
for repeat in range(3):
    for case in cases:
        for model in range(1, 5):
            versions = ('before_source', 'after_source') if repeat % 2 == 0 else ('after_source', 'before_source')
            for version in versions:
                process = subprocess.run([sys.executable, str(OUT/'fixed_work.py'), str(OUT/version), str(model), str(case)],
                    capture_output=True, text=True, timeout=80, env={**os.environ, 'PYTHONHASHSEED': '0'})
                (OUT/f'efficiency_{repeat}_{case.parent.name}_M{model}_{version}.log').write_text(process.stdout+process.stderr)
                value = json.loads(process.stdout) if process.returncode == 0 else dict(valid=False, periodic=False, error=process.stderr)
                value.update(repeat=repeat, version=version, model=model, family=case.parent.name)
                rows.append(value)
                (OUT/'fixed_work_runs.json').write_text(json.dumps(rows, indent=2))
                print(repeat, case.parent.name, model, version, value.get('cpu'), value.get('objective'), flush=True)
summary = []
for case in cases:
    for model in range(1, 5):
        group = [r for r in rows if r['family'] == case.parent.name and r['model'] == model]
        valid = all(r.get('valid') and r.get('periodic') and r.get('iterations') == 300 for r in group)
        same = valid and all(all(r[k] == group[0][k] for k in ('objective', 'iterations', 'e1', 'e2', 'assignment')) for r in group)
        a = [r for r in group if r['version'] == 'before_source' and r.get('cpu') is not None]
        b = [r for r in group if r['version'] == 'after_source' and r.get('cpu') is not None]
        ac = statistics.median(r['cpu'] for r in a) if a else None
        bc = statistics.median(r['cpu'] for r in b) if b else None
        summary.append(dict(family=case.parent.name, model=model, all_valid=valid, identical_outputs=same,
            before_cpu=ac, after_cpu=bc, cpu_reduction_percent=100*(1-bc/ac) if same else None,
            before_objectives=[r['objective'] for r in a], after_objectives=[r['objective'] for r in b]))
(OUT/'fixed_work_summary.json').write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
