"""Keep every pair and apply the preregistered budget and short-size gates."""
from pathlib import Path
import csv,json,statistics
OUT=Path(__file__).resolve().parent
jobs=json.loads((OUT/'quality_manifest.json').read_text())['jobs']
rows=json.loads((OUT/'quality/summary.json').read_text());assert len(rows)==len(jobs)*2
pairs=[]
for index,job in enumerate(jobs):
 versions={r['source']:r for r in rows if r['index']==index};assert set(versions)=={'before_source','after_source'}
 a,b=versions['before_source'],versions['after_source']
 valid=all(r.get('valid') and r.get('periodic') and r.get('objective') is not None for r in (a,b))
 pairs.append(dict(index=index,family=job['family'],model=2,seed=job['seed'],budget=job['budget'],size=50 if 'subset50' in job['family'] else 100,valid=valid,
  before_objective=a.get('objective'),after_objective=b.get('objective'),
  change_percent=100*(b['objective']/a['objective']-1) if valid and a['objective']>0 else None,
  before_iterations=a.get('iterations'),after_iterations=b.get('iterations'),before_wall=a.get('wall'),after_wall=b.get('wall')))
with (OUT/'quality_pairs.csv').open('w') as f:
 writer=csv.DictWriter(f,fieldnames=list(pairs[0]));writer.writeheader();writer.writerows(pairs)
summary=[]
for name,group in [('short',[p for p in pairs if p['budget']==5]),('long',[p for p in pairs if p['budget']==15]),('short50',[p for p in pairs if p['budget']==5 and p['size']==50]),('short100',[p for p in pairs if p['budget']==5 and p['size']==100])]:
 ds=[p['change_percent'] for p in group if p['change_percent'] is not None];valid=len(group)==len(ds)
 mean=statistics.mean(ds) if valid else None;maximum=max(ds) if valid else None
 summary.append(dict(stratum=name,pairs=len(group),all_valid=valid,mean_change_percent=mean,maximum_change_percent=maximum,
  improved=sum(d < -1e-7 for d in ds),equal=sum(abs(d)<=1e-7 for d in ds),worse=sum(d>1e-7 for d in ds),
  mean_iteration_change_percent=statistics.mean(100*(p['after_iterations']/p['before_iterations']-1) for p in group) if valid else None,
  required_improvements=3 if name=='short' else 1 if name=='long' else 0,
  eligible=valid and mean<=0 and maximum<=5 and sum(d < -1e-7 for d in ds)>=(3 if name=='short' else 1 if name=='long' else 0)))
result=dict(pairs=len(pairs),all16_valid=all(p['valid'] for p in pairs),eligible=all(r['eligible'] for r in summary),summary=summary,pair_results=pairs)
(OUT/'quality_comparison.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='pair_results'},indent=2))
