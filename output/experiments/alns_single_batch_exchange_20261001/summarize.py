"""Apply the registered quality gates without removing failures."""
import json
from pathlib import Path
import statistics
import sys

out = Path(__file__).resolve().parent
phase = sys.argv[1]
rows = json.loads((out/phase/'summary.json').read_text())
jobs = json.loads((out/(phase+'_manifest.json')).read_text())['jobs']
pairs = []
for i, job in enumerate(jobs):
    group = [r for r in rows if r['index'] == i]
    before = next((r for r in group if r['source'] == 'before_source'), None)
    after = next((r for r in group if r['source'] == 'after_source'), None)
    valid = len(group) == 2 and all(r['valid'] and r.get('periodic') for r in group)
    delta = (100*(after['objective']/before['objective']-1)
             if valid and before['objective'] > 0 else None)
    pairs.append(dict(job, valid=valid, delta=delta, before=before, after=after))
stats = []
for budget in sorted({j['budget'] for j in jobs}):
    group = [r for r in pairs if r['budget'] == budget]
    changes = [r['delta'] for r in group if r['delta'] is not None]
    valid = len(changes) == len(group) and all(r['valid'] for r in group)
    wins = sum(d < -1e-8 for d in changes)
    mean = statistics.mean(changes) if valid else None
    maximum = max(changes) if valid else None
    passed = valid and mean <= 0
    if phase == 'pilot' or budget == 5:
        passed = passed and wins >= len(group)/2
    if phase == 'formal':
        passed = passed and maximum <= 5
    stats.append(dict(budget=budget, pairs=len(group), valid=valid,
                      mean_delta=mean, max_delta=maximum, wins=wins,
                      ties=sum(abs(d) <= 1e-8 for d in changes),
                      losses=sum(d > 1e-8 for d in changes), passed=bool(passed)))
result = dict(phase=phase, passed=all(s['passed'] for s in stats), stats=stats, pairs=pairs)
(out/(phase+'_comparison.json')).write_text(json.dumps(result,indent=2))
print(json.dumps({k:v for k,v in result.items() if k != 'pairs'},indent=2))
