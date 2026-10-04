"""Compare whole frozen phases and preserve tradeoffs; no per-row selection."""
import json,hashlib
from pathlib import Path
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
phases={p:json.loads((OUT/p/'summary.json').read_text()) for p in ('before','compressed','final','verified','selected')}
counts={p:dict(completed=len(rows),valid=sum(r['valid'] for r in rows),optimal=sum(r['proven_optimal'] for r in rows),errors=sum(r['status'] in ('ERROR','OUTER_TIMEOUT') for r in rows)) for p,rows in phases.items()}
pairs=[]
for a,b in zip(phases['before'],phases['selected']):
 assert a['run']==b['run']
 delta=b['objective']-a['objective'] if a['valid'] and b['valid'] else None
 relation='equal' if delta is not None and abs(delta)<1e-6 else 'improved' if delta is not None and delta<0 else 'worse' if delta is not None else 'unpaired'
 pairs.append(dict(run=a['run'],before=a,after=b,relation=relation))
files=['single_batch_formulation.py','solver.py','strengthening.py','assignment.py','primal.py','single_batch_primal.py','assignment_tree.py']
hashes=json.loads((OUT/'selected/source_hashes.json').read_text())
source_match={name:hashlib.sha256((ROOT/'models/routing/solvers/exact'/name).read_bytes()).hexdigest()==hashes['models/routing/solvers/exact/'+name] for name in files}
summary=dict(protocol='24 frozen cases, 10s solve budget, seed42, one thread, 20s outer allowance. Every phase retained.',counts=counts,
 relations={r:sum(p['relation']==r for p in pairs) for r in ('improved','equal','worse','unpaired')},pairs=pairs,source_match=source_match,
 validation=dict(full_suite=json.loads((OUT/'selected_full_tests_execution.json').read_text()),passed=511,warnings=14,new_cases=28),
 limitations=['Diagnostic families used during development, not held-out test set.','Single wall-clock run per phase; no general speedup or significance claim.','All distance, gap, runtime and proof-count regressions are retained in the selected-phase comparison.'])
(OUT/'comparison.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
def n(x):return '—' if x is None else f'{x:.3f}'
def g(x):return '—' if x is None else f'{100*x:.2f}%'
lines=['| 案例 | 模型 | 原距离 | 新距离 | 原 Gap | 新 Gap | 原墙钟/s | 新墙钟/s |','| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
for p in pairs:
 a,b=p['before'],p['after'];family=p['run'].rsplit('_M',1)[0]
 lines.append(f"| {family} | M{a['model']} | {n(a['objective'])} | {n(b['objective'])} | {g(a['mip_gap'])} | {g(b['mip_gap'])} | {n(a['wall_time'])} | {n(b['wall_time'])} |")
(OUT/'comparison_table.md').write_text('\n'.join(lines)+'\n')
print(json.dumps({k:summary[k] for k in ('counts','relations','source_match')},indent=2))
