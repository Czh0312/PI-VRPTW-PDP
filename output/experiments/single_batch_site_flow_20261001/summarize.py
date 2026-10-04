"""Summarize whole phases without replacing individual runs."""
import json
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
before = json.loads((OUT/'wall_before/summary.json').read_text())
phase = sys.argv[1] if len(sys.argv) > 1 else 'wall_selected'
selected = json.loads((OUT/phase/'summary.json').read_text())
assert len(before) == len(selected) == 24
assert [r['run'] for r in before] == [r['run'] for r in selected]
changed = dict(better=0, equal=0, worse=0)
table = ['| 案例 | 原距离 | 新距离 | 原下界 | 新下界 | 原 Gap | 新 Gap | 原证明 | 新证明 |',
         '| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |']
details = []
for a, b in zip(before, selected):
    assert a['valid'] and b['valid'] and a['periodic_replay'] and b['periodic_replay']
    diff = b['objective']-a['objective']
    change = 'better' if diff < -1e-6 else 'worse' if diff > 1e-6 else 'equal'
    changed[change] += 1
    info_a = json.loads((OUT/'wall_before'/a['run']/'result.json').read_text())['info']
    info_b = json.loads((OUT/phase/b['run']/'result.json').read_text())['info']
    details.append(dict(run=a['run'], change=change, before=info_a, selected=info_b))
    gap = lambda r: '—' if r['mip_gap'] is None else f"{r['mip_gap']*100:.2f}%"
    table.append(f"| {a['run']} | {a['objective']:.3f} | {b['objective']:.3f} | "
                 f"{a['best_bound']:.3f} | {b['best_bound']:.3f} | {gap(a)} | {gap(b)} | "
                 f"{a['proven_optimal']} | {b['proven_optimal']} |")
summary = dict(candidate_phase=phase, distance_changes=changed, before_valid=sum(r['valid'] for r in before),
               selected_valid=sum(r['valid'] for r in selected),
               before_optimal=sum(r['proven_optimal'] for r in before),
               selected_optimal=sum(r['proven_optimal'] for r in selected),
               rows=details)
prefix = '' if phase == 'wall_selected' else 'final_'
(OUT/f'{prefix}comparison.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
(OUT/f'{prefix}comparison_table.md').write_text('\n'.join(table)+'\n')
print(json.dumps({k:v for k,v in summary.items() if k != 'rows'}, indent=2))
