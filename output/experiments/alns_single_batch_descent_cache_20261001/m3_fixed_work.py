"""New M3-only confirmation on distinct sources and a new seed."""
from pathlib import Path
import json
import os
import statistics
import subprocess
import sys

OUT = Path(__file__).resolve().parent
cases = [OUT/'benchmark_inputs'/name/'case.json' for name in
         ('zhou_Set2_100-5-3_scenario02', 'zhou_Set2_100-10-2_scenario02')]
rows = []
for repeat in range(3):
    for case in cases:
        sources = ('before_source', 'm3_source') if repeat % 2 == 0 else ('m3_source', 'before_source')
        for source in sources:
            process = subprocess.run([sys.executable, str(OUT/'fixed_work68.py'), str(OUT/source), '3', str(case)],
                capture_output=True, text=True, timeout=80, env={**os.environ, 'PYTHONHASHSEED': '0'})
            (OUT/f'm3_fixed_{repeat}_{case.parent.name}_{source}.log').write_text(process.stdout+process.stderr)
            value = json.loads(process.stdout) if process.returncode == 0 else dict(valid=False, periodic=False, error=process.stderr)
            value.update(repeat=repeat, source=source, model=3, family=case.parent.name, seed=68)
            rows.append(value)
            (OUT/'m3_fixed_work_runs.json').write_text(json.dumps(rows, indent=2))
            print(repeat, case.parent.name, source, value.get('cpu'), value.get('objective'), flush=True)
summary = []
for case in cases:
    group = [r for r in rows if r['family'] == case.parent.name]
    valid = all(r.get('valid') and r.get('periodic') and r.get('iterations') == 300 for r in group)
    same = valid and all(all(r[k] == group[0][k] for k in ('objective', 'iterations', 'e1', 'e2', 'assignment')) for r in group)
    a = [r for r in group if r['source'] == 'before_source' and r.get('cpu') is not None]
    b = [r for r in group if r['source'] == 'm3_source' and r.get('cpu') is not None]
    ac = statistics.median(r['cpu'] for r in a) if a else None
    bc = statistics.median(r['cpu'] for r in b) if b else None
    reduction = 100*(1-bc/ac) if same else None
    summary.append(dict(family=case.parent.name, model=3, valid=valid, identical=same,
        before_cpu=ac, after_cpu=bc, cpu_reduction_percent=reduction, eligible=same and reduction>=2))
(OUT/'m3_fixed_work_summary.json').write_text(json.dumps(summary, indent=2))
(OUT/'m3_primary_decision.json').write_text(json.dumps(dict(eligible=all(r['eligible'] for r in summary), summary=summary), indent=2))
print(json.dumps(summary, indent=2))
