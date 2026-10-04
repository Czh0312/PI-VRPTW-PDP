"""All registered quality pairs, including failures, grouped by model and budget."""
from pathlib import Path
import csv,json,statistics
OUT=Path(__file__).resolve().parent
jobs=json.loads((OUT/'scoped_quality_manifest.json').read_text())['jobs']
rows=json.loads((OUT/'scoped_quality/summary.json').read_text());assert len(rows)==len(jobs)*2
pairs=[]
for index,job in enumerate(jobs):
 versions={r['source']:r for r in rows if r['index']==index};assert set(versions)=={'before_source','scoped_source'}
 a,b=versions['before_source'],versions['scoped_source']
 valid=all(r.get('valid') and r.get('periodic') and r.get('objective') is not None for r in (a,b))
 pairs.append(dict(index=index,family=job['family'],model=job['model'],seed=job['seed'],budget=job['budget'],
  before_valid=a.get('valid'),after_valid=b.get('valid'),before_periodic=a.get('periodic'),after_periodic=b.get('periodic'),
  before_objective=a.get('objective'),after_objective=b.get('objective'),
  change_percent=100*(b['objective']/a['objective']-1) if valid and a['objective']>0 else None,
  before_iterations=a.get('iterations'),after_iterations=b.get('iterations'),before_wall=a.get('wall'),after_wall=b.get('wall')))
with (OUT/'scoped_quality_pairs.csv').open('w') as f:
 writer=csv.DictWriter(f,fieldnames=list(pairs[0]));writer.writeheader();writer.writerows(pairs)
summary=[]
for budget in (5,15):
 for model in range(1,4):
  group=[p for p in pairs if p['model']==model and p['budget']==budget];ds=[p['change_percent'] for p in group if p['change_percent'] is not None]
  summary.append(dict(model=model,budget=budget,pairs=len(group),comparable=len(ds),all_valid=len(group)==len(ds),
   mean_change_percent=statistics.mean(ds) if ds else None,improved=sum(d < -1e-7 for d in ds),equal=sum(abs(d)<=1e-7 for d in ds),
   worse=sum(d>1e-7 for d in ds),maximum_regression_percent=max(0,max(ds)) if ds else None,
   mean_iteration_change_percent=statistics.mean(100*(p['after_iterations']/p['before_iterations']-1) for p in group if p['before_iterations']) if len(group)==len(ds) else None))
eligible=all(r['all_valid'] and r['mean_change_percent']<=2 and r['maximum_regression_percent']<=10 for r in summary)
result=dict(pairs=len(pairs),valid_before=sum(p['before_valid'] is True for p in pairs),valid_after=sum(p['after_valid'] is True for p in pairs),periodic_before=sum(p['before_periodic'] is True for p in pairs),periodic_after=sum(p['after_periodic'] is True for p in pairs),eligible=eligible,summary=summary,
 gate='Each model/budget mean regression<=2%; each single<=10%; all audit/replay valid. Reject whole candidate on any failure.',
 limitations=['Descriptive observations, observed mother sources, dependent nested50/100 cases; no significance or universal quality effect.',
 'Alternating serial processes. Other machine load uncontrolled. Speed changes can alter wall-time stopping and annealing schedule.',
 'No stronger lower bound, proven optimum or actual GAP improvement.'])
(OUT/'scoped_quality_comparison.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
