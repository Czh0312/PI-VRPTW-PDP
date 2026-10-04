"""Separate reused-baseline recheck from fresh, alternating holdout pairs."""
from pathlib import Path
import csv,json,statistics
OUT=Path(__file__).resolve().parent

def key(r):return tuple(r[k] for k in ('family','model','seed','budget'))
def compare(before,after,cohort):
 old={key(r):r for r in before};assert set(old)=={key(r) for r in after}
 pairs=[]
 for r in after:
  b=old[key(r)];valid=b['valid'] and r['valid'] and b.get('objective') is not None and r.get('objective') is not None
  pairs.append(dict(cohort=cohort,family=r['family'],model=r['model'],seed=r['seed'],budget=r['budget'],
   before_valid=b['valid'],after_valid=r['valid'],after_periodic=r.get('periodic'),
   before_objective=b.get('objective'),after_objective=r.get('objective'),
   change_percent=100*(r['objective']/b['objective']-1) if valid else None,
   before_wall=b.get('wall'),after_wall=r.get('wall'),before_iterations=b.get('iterations'),after_iterations=r.get('iterations')))
 return pairs

def summarize(pairs):
 result=[]
 for model in (1,2,3):
  group=[p for p in pairs if p['model']==model];ds=[p['change_percent'] for p in group if p['change_percent'] is not None]
  result.append(dict(model=model,pairs=len(group),mean_change_percent=statistics.mean(ds) if ds else None,
   improved=sum(d < -1e-7 for d in ds),equal=sum(abs(d)<=1e-7 for d in ds),worse=sum(d>1e-7 for d in ds),
   maximum_regression_percent=max(0.0, max(ds)) if ds else None,valid_after=sum(p['after_valid'] for p in group)))
 return result

old=[r for r in json.loads((OUT/'validation/summary.json').read_text()) if r['source']=='before_source']
new=json.loads((OUT/'tail_recheck/after/summary.json').read_text())
assert len(new)==27
recheck=compare(old,new,'recheck_observed_cases')
report=dict(policy='Last 20% of either budget for M1-M3; retain M4 original continuous search',
 recheck_summary=summarize(recheck),recheck_baseline_note='Baseline reused from earlier alternating early-descent validation; not newly interleaved with this candidate.',
 limitations=['Recheck sources have informed candidate design; they are not held out.',
  'Fresh holdout is one new source block with nested 50/100-customer cases and 3 seeds each, at 5 seconds.',
  'Machine load uncontrolled; results descriptive, without significance tests or optimality certificates.'])
pairs=list(recheck)
fresh_path=OUT/'tail_holdout/summary.json'
if fresh_path.exists():
 fresh=json.loads(fresh_path.read_text());assert len(fresh)==36,len(fresh)
 before=[r for r in fresh if r['source']=='before_source'];after=[r for r in fresh if r['source']=='tail_source']
 holdout=compare(before,after,'fresh_source_block');pairs+=holdout
 report.update(holdout_summary=summarize(holdout),holdout_valid_before=sum(p['before_valid'] for p in holdout),
  holdout_valid_after=sum(p['after_valid'] for p in holdout),holdout_periodic=sum(p['after_periodic'] is True for p in holdout))
 by_case=[]
 for family in sorted({p['family'] for p in pairs}):
  for model in (1,2,3):
   group=[p for p in pairs if p['family']==family and p['model']==model]
   a=[p['before_objective'] for p in group];b=[p['after_objective'] for p in group]
   if not a or any(v is None for v in a+b):continue
   by_case.append(dict(family=family,model=model,budget=group[0]['budget'],cohort=group[0]['cohort'],
    before_mean=statistics.mean(a),after_mean=statistics.mean(b),
    mean_change_percent=100*(statistics.mean(b)/statistics.mean(a)-1),
    before_cv_percent=100*statistics.pstdev(a)/statistics.mean(a),after_cv_percent=100*statistics.pstdev(b)/statistics.mean(b)))
 report['by_case']=by_case
report.update(valid_after=sum(p['after_valid'] for p in pairs),periodic_after=sum(p['after_periodic'] is True for p in pairs),
 max_wall_overrun=max(p['after_wall']-p['budget'] for p in pairs if p['after_wall'] is not None))
with (OUT/'tail_paired_results.csv').open('w') as stream:
 w=csv.DictWriter(stream,fieldnames=list(pairs[0]));w.writeheader();w.writerows(pairs)
(OUT/'tail_comparison.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
print(json.dumps({k:v for k,v in report.items() if k.endswith('summary') or k.startswith('holdout_') or k in ('max_wall_overrun','valid_after')},indent=2))
