"""Retain every pair, including failures, and apply the preregistered thresholds."""
import csv
import json
import statistics
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent
phase = sys.argv[1] if len(sys.argv) > 1 else 'quality'
candidate = sys.argv[2] if len(sys.argv)>2 else 'after_source'
manifest = phase+'_manifest.json'
jobs = json.loads((OUT/manifest).read_text())['jobs']
rows = json.loads((OUT/phase/'summary.json').read_text())
assert len(rows) == 2*len(jobs)
pairs = []
for index, job in enumerate(jobs):
    versions = {r['source']: r for r in rows if r['index'] == index}
    assert set(versions) == {'before_source', candidate}
    a, b = versions['before_source'], versions[candidate]
    valid = all(r.get('valid') and r.get('periodic') and r.get('objective') is not None for r in (a, b))
    raw = OUT/phase/(f'{index:03d}_{candidate}.json')
    info = json.loads(raw.read_text())['result']['info'] if raw.exists() else {}
    pairs.append(dict(index=index, family=job['family'], model=job['model'], seed=job['seed'], budget=job['budget'],
        before_valid=a.get('valid'), after_valid=b.get('valid'), before_periodic=a.get('periodic'),
        after_periodic=b.get('periodic'), before_objective=a.get('objective'), after_objective=b.get('objective'),
        change_percent=100*(b['objective']/a['objective']-1) if valid and a['objective'] > 0 else None,
        before_iterations=a.get('iterations'), after_iterations=b.get('iterations'),
        before_wall=a.get('wall'), after_wall=b.get('wall'),
        descent_deferred=info.get('descent_deferred', 0), descent_recovered=info.get('descent_recovered', 0),
        descent_improvements=info.get('operator_improvements', {}).get('descent', 0)))
with (OUT/(phase+'_pairs.csv')).open('w') as stream:
    writer = csv.DictWriter(stream, fieldnames=list(pairs[0]))
    writer.writeheader(); writer.writerows(pairs)
summary = []
for budget in sorted({p['budget'] for p in pairs}):
    for model in range(1, 5):
        group = [p for p in pairs if p['model'] == model and p['budget'] == budget]
        ds = [p['change_percent'] for p in group if p['change_percent'] is not None]
        summary.append(dict(model=model, budget=budget, pairs=len(group), comparable=len(ds),
            mean_change_percent=statistics.mean(ds) if ds else None,
            improved=sum(d < -1e-7 for d in ds), equal=sum(abs(d) <= 1e-7 for d in ds),
            worse=sum(d > 1e-7 for d in ds), maximum_regression_percent=max(0, max(ds)) if ds else None,
            all_valid=len(ds) == len(group),
            deferred=sum(p['descent_deferred'] for p in group),
            recovered=sum(p['descent_recovered'] for p in group),
            descent_improvements=sum(p['descent_improvements'] for p in group)))
decisions = []
for model in range(1, 5):
    groups = [r for r in summary if r['model'] == model]
    eligible = all(r['all_valid'] and r['mean_change_percent'] <= 2 and r['maximum_regression_percent'] <= 10 for r in groups)
    decisions.append(dict(model=model, within_preregistered_quality_limits=eligible,
        unchanged_control=model!=3, decision_pending=phase=='pilot'))
result = dict(phase=phase, pairs=len(pairs), valid_before=sum(p['before_valid'] is True for p in pairs),
    valid_after=sum(p['after_valid'] is True for p in pairs),
    periodic_before=sum(p['before_periodic'] is True for p in pairs),
    periodic_after=sum(p['after_periodic'] is True for p in pairs), summary=summary, decisions=decisions,
    recovered_scope='Locally screened returned proposals, not certificates or a count of audited best improvements',
    max_wall_overrun=max(p['after_wall']-p['budget'] for p in pairs if p['after_wall'] is not None),
    limitations=['Descriptive only: four target-model pairs on two100 sources and two seeds; sources previously observed; controls share geometries.',
        'Alternated serial processes; load not fully controlled. M4 control has unchanged search but time-limited output can vary.',
        'No global optimality, statistical significance, full-dataset or longer-than-15-second claim.'])
(OUT/(phase+'_comparison.json')).write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
