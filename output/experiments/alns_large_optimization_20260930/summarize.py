"""Summarize every registered pair; do not discard failed or worse runs."""
from pathlib import Path
import csv
import hashlib
import json
import statistics

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
DEV = ROOT/'output/experiments/alns_large_status_20260930'

def read(p):
    return json.loads(p.read_text())

def key(r):
    return tuple(r[k] for k in ('family', 'model', 'budget', 'seed'))

old = read(DEV/'current/summary.json') + read(OUT/'before/summary.json')
new = [r for r in read(DEV/'optimized/summary.json') + read(OUT/'after/summary.json') if r['model'] != 2]
new += read(DEV/'m2_partitioned/summary.json') + read(OUT/'m2_partitioned/summary.json')
assert len(old) == len(new) == 72
before = {key(r): r for r in old}
assert set(before) == {key(r) for r in new}
pairs = []
for r in new:
    b = before[key(r)]
    both = b['valid'] and r['valid'] and b['objective'] is not None and r['objective'] is not None
    pairs.append(dict(family=r['family'], customers=50 if 'subset50' in r['family'] else 100,
        model=r['model'], budget=r['budget'], seed=r['seed'],
        cohort='development' if '100-5-1b' in r['family'] else 'held_out',
        before_valid=b['valid'], after_valid=r['valid'], after_periodic=r['periodic'],
        before_objective=b.get('objective'), after_objective=r.get('objective'),
        distance_change_percent=100*(r['objective']/b['objective']-1) if both else None,
        before_wall=b.get('wall'), after_wall=r.get('wall'),
        before_iterations=b.get('iterations'), after_iterations=r.get('iterations')))
with (OUT/'paired_results.csv').open('w') as f:
    w=csv.DictWriter(f,fieldnames=list(pairs[0])); w.writeheader();w.writerows(pairs)
summary = []
for cohort in ('all', 'development', 'held_out'):
    for model in range(1,5):
        rows=[r for r in pairs if r['model']==model and (cohort=='all' or r['cohort']==cohort)]
        changes=[r['distance_change_percent'] for r in rows if r['distance_change_percent'] is not None]
        summary.append(dict(cohort=cohort,model=model,runs=len(rows),valid=sum(r['after_valid'] for r in rows),
            average_distance_change_percent=statistics.mean(changes),
            improved=sum(x < -1e-7 for x in changes),equal=sum(abs(x)<=1e-7 for x in changes),
            worse=sum(x>1e-7 for x in changes),worst_change_percent=max(changes)))
by_case=[]
for family in sorted({r['family'] for r in pairs}):
    for model in range(1,5):
        for budget in sorted({r['budget'] for r in pairs if r['family']==family}):
            rows=[r for r in pairs if r['family']==family and r['model']==model and r['budget']==budget]
            a=[r['before_objective'] for r in rows];b=[r['after_objective'] for r in rows]
            if any(x is None for x in a+b):continue
            by_case.append(dict(family=family,model=model,budget=budget,
                before_mean=statistics.mean(a),after_mean=statistics.mean(b),
                before_cv_percent=100*statistics.pstdev(a)/statistics.mean(a),
                after_cv_percent=100*statistics.pstdev(b)/statistics.mean(b),
                mean_change_percent=100*(statistics.mean(b)/statistics.mean(a)-1),
                before_range=[min(a),max(a)],after_range=[min(b),max(b)]))
critical=['models/routing/solvers/alns/'+s for s in ('single_batch.py','assignment.py','neighborhoods.py','construction.py','evaluator.py','partitioned_assignment.py')]
frozen=read(OUT/'final_source_manifest.json')['sha256']
changed=[p for p in critical if hashlib.sha256((ROOT/p).read_bytes()).hexdigest()!=frozen[p]]
verification=dict(material_passport=dict(origin_skill='academic-research-suite / experiment-agent',
    origin_mode='validate',origin_date='2026-09-30',verification_status='VERIFIED for executed pairs, audits and frozen-source provenance; not optimality or general statistical significance'),
    runs_per_version=len(new),valid_before=sum(r['valid'] for r in old),valid_after=sum(r['valid'] for r in new),
    periodic_after=sum(r['periodic'] for r in new),critical_live_source_changes_since_freeze=changed,
    max_wall_overrun=max(r['wall']-r['budget'] for r in new),
    summary=summary,by_case=by_case,
    statistical_scope='Descriptive paired relative distance changes, equal weight per run; 3 seeds per cell. No p-values or confidence intervals. 4 instances form 2 nested source blocks.',
    limitations=['Not the full set of 20 large cases; only budgets 1 and 5 seconds.',
                 'Development instances have been observed during optimization. The second source was initially held out; M2 retained the earlier partitioned search based on development results, then this source was rechecked. It is supplemental validation, not a pristine holdout for final M2.',
                 'Historical development baseline and held-out baseline ran at different times; machine load was not controlled.',
                 'Time-budget-dependent cooling changes the trajectory: 1s and 5s are independent runs.',
                 'No optimality certificates or certified relative optimality gaps.',
                 'No per-seed guarantee that free assignment outperforms fixed assignment.'],
    fallacy_scan={'coverage':'11/11 checked','simpson':'Stratify by source, size, model and budget; no pooled raw-distance totals.',
      'ecological':'Do not extrapolate to all instances.','berkson':'Source-selection limitations recorded.',
      'collider':'Not applicable; no adjusted causal model.','base_rate':'All 72 registered jobs retained.',
      'regression_to_mean':'Fixed seeds and preselected held-out source, no worst-seed selection.',
      'survivorship':'All failed/worse outcomes retained.','look_elsewhere':'Full table and discarded joint-M2 candidate retained; no significance testing.',
      'forking_paths':'Pilot retained separately; final source frozen before complete validation.',
      'causal':'Machine load uncontrolled; timing effects are diagnostic observations.',
      'reverse_causality':'Not applicable to assigned budgets.'})
(OUT/'comparison.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2))
for r in summary:print(r)
print('valid',verification['valid_before'],verification['valid_after'],'max_overrun',verification['max_wall_overrun'],'live changes',changed)
