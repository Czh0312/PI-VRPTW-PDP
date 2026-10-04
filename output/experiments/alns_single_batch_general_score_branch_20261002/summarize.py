"""Each model must meet every original budget/size quality gate; no subset adoption."""
from pathlib import Path
import csv,json,statistics
OUT=Path(__file__).resolve().parent
jobs=json.loads((OUT/'quality_manifest.json').read_text())['jobs']
rows=json.loads((OUT/'quality/summary.json').read_text());assert len(rows)==len(jobs)*2==32
pairs=[]
for index,job in enumerate(jobs):
    versions={r['source']:r for r in rows if r['index']==index}
    assert set(versions)=={'before_source','after_source'}
    a,b=versions['before_source'],versions['after_source']
    assert all(r['model']==job['model'] and r['seed']==job['seed'] and r['budget']==job['budget'] for r in (a,b))
    valid=all(r.get('valid') and r.get('periodic') and r.get('objective') is not None for r in (a,b))
    pairs.append(dict(index=index,family=job['family'],model=job['model'],seed=job['seed'],budget=job['budget'],size=job['size'],valid=valid,
        before_objective=a.get('objective'),after_objective=b.get('objective'),
        change_percent=100*(b['objective']/a['objective']-1) if valid and a['objective']>0 else None,
        before_iterations=a.get('iterations'),after_iterations=b.get('iterations'),before_wall=a.get('wall'),after_wall=b.get('wall')))
with (OUT/'quality_pairs.csv').open('w') as f:
    writer=csv.DictWriter(f,fieldnames=list(pairs[0]));writer.writeheader();writer.writerows(pairs)
summary=[]
for model in (3,4):
    group=[p for p in pairs if p['model']==model];assert len(group)==8
    for name,selected in [('short',[p for p in group if p['budget']==30]),('long',[p for p in group if p['budget']==60]),('short50',[p for p in group if p['budget']==30 and p['size']==50]),('short100',[p for p in group if p['budget']==30 and p['size']==100])]:
        ds=[p['change_percent'] for p in selected if p['change_percent'] is not None]
        valid=len(ds)==len(selected) and len(selected)=={'short':6,'long':2,'short50':3,'short100':3}[name]
        mean=statistics.mean(ds) if valid else None;maximum=max(ds) if valid else None
        wins=sum(d < -1e-7 for d in ds);required={'short':3,'long':1,'short50':0,'short100':0}[name]
        summary.append(dict(model=model,stratum=name,pairs=len(selected),all_valid=valid,mean_change_percent=mean,maximum_change_percent=maximum,
            improved=wins,equal=sum(abs(d)<=1e-7 for d in ds),worse=sum(d>1e-7 for d in ds),required_improvements=required,
            mean_iteration_change_percent=statistics.mean(100*(p['after_iterations']/p['before_iterations']-1) for p in selected) if valid else None,
            eligible=valid and mean<=0 and maximum<=5 and wins>=required))
result=dict(pairs=len(pairs),all32_valid=all(p['valid'] for p in pairs),eligible=all(r['eligible'] for r in summary),summary=summary,pair_results=pairs)
(OUT/'quality_comparison.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='pair_results'},indent=2))
