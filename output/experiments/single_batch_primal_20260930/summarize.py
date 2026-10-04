"""Describe the final whole phase, never select best rows across candidates."""
import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
phases = {p: json.loads((OUT/p/'summary.json').read_text()) for p in ('before','candidate','capacity','final')}
counts = {p: dict(completed=len(rows), valid=sum(r['valid'] for r in rows),
                 optimal=sum(r['proven_optimal'] for r in rows),
                 errors=sum(r['status'] in ('ERROR','OUTER_TIMEOUT') for r in rows)) for p, rows in phases.items()}
pairs = []
for a,b in zip(phases['before'], phases['final']):
    assert a['run'] == b['run']
    relation = 'new_incumbent' if b['valid'] and not a['valid'] else 'lost_incumbent' if a['valid'] and not b['valid'] else 'no_incumbent'
    if a['valid'] and b['valid']:
        delta = b['objective']-a['objective']
        relation = 'equal' if abs(delta)<1e-6 else 'improved' if delta<0 else 'worse'
    pairs.append(dict(run=a['run'], before=a, after=b, relation=relation))
source_before=json.loads((OUT/'before/source_hashes.json').read_text())
source_final=json.loads((OUT/'final/source_hashes.json').read_text())
changed=[k for k in sorted(set(source_before)|set(source_final)) if source_before.get(k)!=source_final.get(k)]
exact=['models/routing/solvers/exact/'+name for name in ('primal.py','single_batch_primal.py','assignment.py','single_batch_formulation.py','solver.py','assignment_tree.py','strengthening.py')]
match={k:hashlib.sha256((ROOT/k).read_bytes()).hexdigest()==source_final.get(k) for k in exact}
summary=dict(protocol='24 frozen cases; each solve 10 seconds, seed42, Gurobi one thread; outer allowance20s. Single run per phase.',counts=counts,
    relations={r:sum(p['relation']==r for p in pairs) for r in ('improved','equal','worse','new_incumbent','lost_incumbent')},pairs=pairs,
    changed_sources=changed,final_exact_sources_match_current=match,
    limitations=['Diagnostic sample, not all93 B1 cases. No statistical or universal speedup claim.',
                 'Several phases were tried; final phase chosen as a whole. All outcomes retained.',
                 'Other chats modified ALNS and multibatch primal files concurrently; B1 exact imports no ALNS and does not enter multibatch primal.',
                 'Wall-clock budgets are machine-load sensitive; some small cases took longer and some lower bounds weakened.',
                 'Candidate capacity activation was valid but did not improve the diagnostic phase; removed from default and production code.'])
(OUT/'comparison.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
def num(x):return '—' if x is None else f'{x:.3f}'
def gap(x):return '—' if x is None else f'{100*x:.2f}%'
lines=['| 案例 | 模型 | 原距离 | 新距离 | 原 Gap | 新 Gap | 原墙钟/s | 新墙钟/s |',
       '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
for pair in pairs:
 a,b=pair['before'],pair['after'];family=pair['run'].rsplit('_M',1)[0]
 lines.append(f"| {family} | M{a['model']} | {num(a['objective'])} | {num(b['objective'])} | {gap(a['mip_gap'])} | {gap(b['mip_gap'])} | {num(a['wall_time'])} | {num(b['wall_time'])} |")
(OUT/'comparison_table.md').write_text('\n'.join(lines)+'\n')
print(json.dumps(dict(counts=counts,relations=summary['relations'],source_match=match),ensure_ascii=False,indent=2))
