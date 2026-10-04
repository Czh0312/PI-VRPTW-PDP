"""Retain every pair and apply the model-wise preregistered rule."""
import csv
import json
import statistics
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent
phase, candidate = sys.argv[1:3]
jobs = json.loads((OUT/(phase+'_manifest.json')).read_text())['jobs']
rows = json.loads((OUT/phase/'summary.json').read_text())
assert len(rows) == 2*len(jobs)
pairs = []
for index, job in enumerate(jobs):
    sources = {r['source']: r for r in rows if r['index'] == index}
    assert set(sources) == {'before_source', candidate}
    a, b = sources['before_source'], sources[candidate]
    valid = all(r.get('valid') and r.get('periodic') and r.get('objective') is not None for r in (a, b))
    raw = OUT/phase/(f'{index:03d}_{candidate}.json')
    info = json.loads(raw.read_text())['result']['info'] if raw.is_file() else {}
    pairs.append(dict(index=index, family=job['family'], model=job['model'], seed=job['seed'], budget=job['budget'],
        before_valid=a.get('valid'), after_valid=b.get('valid'), before_periodic=a.get('periodic'), after_periodic=b.get('periodic'),
        before_objective=a.get('objective'), after_objective=b.get('objective'),
        change_percent=100*(b['objective']/a['objective']-1) if valid and a['objective'] > 0 else None,
        before_iterations=a.get('iterations'), after_iterations=b.get('iterations'),
        before_wall=a.get('wall'), after_wall=b.get('wall'),
        regret_attempts=info.get('operator_attempts', {}).get('destroy_regret', 0),
        regret_improvements=info.get('operator_improvements', {}).get('destroy_regret', 0),
        regret_completed=info.get('regret_completed', 0), regret_reordered=info.get('regret_reordered', 0),
        regret_seconds=info.get('regret_seconds', 0)))
with (OUT/(phase+'_pairs.csv')).open('w') as stream:
    writer = csv.DictWriter(stream, fieldnames=list(pairs[0]))
    writer.writeheader(); writer.writerows(pairs)
summary = []
for budget in sorted({p['budget'] for p in pairs}):
    for model in sorted({p['model'] for p in pairs}):
        group = [p for p in pairs if p['model'] == model and p['budget'] == budget]
        ds = [p['change_percent'] for p in group if p['change_percent'] is not None]
        summary.append(dict(model=model, budget=budget, pairs=len(group), comparable=len(ds),
            mean_change_percent=statistics.mean(ds) if ds else None,
            improved=sum(d < -1e-7 for d in ds), equal=sum(abs(d) <= 1e-7 for d in ds), worse=sum(d > 1e-7 for d in ds),
            maximum_regression_percent=max(0, max(ds)) if ds else None, all_valid=len(ds)==len(group),
            regret_attempts=sum(p['regret_attempts'] for p in group), regret_improvements=sum(p['regret_improvements'] for p in group),
            regret_completed=sum(p['regret_completed'] for p in group), regret_reordered=sum(p['regret_reordered'] for p in group)))
decisions = []
for model in sorted({p['model'] for p in pairs}):
    groups = [r for r in summary if r['model'] == model]
    short = next(r for r in groups if r['budget']==5)
    eligible = all(r['all_valid'] and r['mean_change_percent'] <= 0 for r in groups) and short['improved'] >= short['pairs']/2
    if phase != 'pilot':
        eligible = eligible and any(r['budget']==15 for r in groups) and max(r['maximum_regression_percent'] for r in groups) <= 5
    decisions.append(dict(model=model, eligible=eligible, stage=phase))
result = dict(phase=phase, candidate=candidate, pairs=len(pairs),
    valid_before=sum(p['before_valid'] is True for p in pairs), valid_after=sum(p['after_valid'] is True for p in pairs),
    periodic_before=sum(p['before_periodic'] is True for p in pairs), periodic_after=sum(p['after_periodic'] is True for p in pairs),
    summary=summary, decisions=decisions,
    max_wall_overrun=max(p['after_wall']-p['budget'] for p in pairs if p['after_wall'] is not None),
    limitations=['Descriptive small-seed experiment; dependent nested sizes; mother sources observed earlier.',
        'Selection by development and validation results is not an unbiased estimate of all future cases.',
        'Alternating serial workers; machine load not fully controlled; no significance or optimality claim.'])
(OUT/(phase+'_comparison.json')).write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
