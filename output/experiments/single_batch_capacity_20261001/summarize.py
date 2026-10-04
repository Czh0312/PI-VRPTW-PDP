"""Report whole phases, including unchanged objectives and every regression."""
import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
phases = {name: json.loads((OUT/name/'summary.json').read_text())
          for name in ('wall_before', 'selected')}
assert all(len(rows) == 24 for rows in phases.values())
pairs = []
for before, after in zip(phases['wall_before'], phases['selected']):
    assert before['run'] == after['run']
    relation = 'unpaired'
    if before['valid'] and after['valid']:
        difference = after['objective']-before['objective']
        relation = 'equal' if abs(difference) < 1e-6 else 'improved' if difference < 0 else 'worse'
    features = {}
    for phase in phases:
        info = json.loads((OUT/phase/after['run']/'result.json').read_text())['info']
        features[phase] = {key: info.get(key) for key in (
            'capacity_cut_candidates', 'capacity_cuts_added', 'mip_work', 'mip_node_count',
            'variables', 'constraints', 'formulation_features')}
    pairs.append(dict(run=before['run'], before=before, after=after, relation=relation, details=features))
counts = {name: dict(valid=sum(row['valid'] for row in rows),
                    optimal=sum(row['proven_optimal'] for row in rows),
                    periodic_replay=sum(row['periodic_replay'] for row in rows),
                    errors=sum(row['status'] in ('ERROR', 'OUTER_TIMEOUT') for row in rows))
          for name, rows in phases.items()}
files = ('single_batch_cuts.py', 'single_batch_formulation.py', 'strengthening.py', 'assignment.py',
         'assignment_tree.py', 'primal.py', 'single_batch_primal.py', 'domains.py', 'bounds.py')
hashes = json.loads((OUT/'selected/source_hashes.json').read_text())
matches = {name: hashlib.sha256((ROOT/'models/routing/solvers/exact'/name).read_bytes()).hexdigest() ==
           hashes['models/routing/solvers/exact/'+name] for name in files}
result = dict(counts=counts, relations={name: sum(pair['relation'] == name for pair in pairs)
    for name in ('improved', 'equal', 'worse', 'unpaired')}, pairs=pairs, source_match=matches,
    limitations=['One wall-time run per phase; no general speedup or significance claim.',
                 'Development cases are not a held-out benchmark.',
                 'M5 concurrent changes to shared files are recorded in the source snapshots.'])
execution = OUT/'full_tests_execution.json'
if execution.exists():
    result['test_execution'] = json.loads(execution.read_text())
(OUT/'comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
def number(value):
    return '—' if value is None else f'{value:.3f}'
def gap(value):
    return '—' if value is None else f'{100*value:.2f}%'
lines = ['| 案例 | 原距离 | 新距离 | 原 Gap | 新 Gap | 原最优证明 | 新最优证明 | 新提交割数 |',
         '| --- | ---: | ---: | ---: | ---: | --- | --- | ---: |']
for pair in pairs:
    a, b = pair['before'], pair['after']
    cuts = pair['details']['selected']['capacity_cuts_added']
    lines.append(f"| {pair['run']} | {number(a['objective'])} | {number(b['objective'])} | "
                 f"{gap(a['mip_gap'])} | {gap(b['mip_gap'])} | {a['proven_optimal']} | "
                 f"{b['proven_optimal']} | {cuts} |")
(OUT/'comparison_table.md').write_text('\n'.join(lines)+'\n')
print(json.dumps({key: result[key] for key in ('counts', 'relations', 'source_match')}, indent=2))
