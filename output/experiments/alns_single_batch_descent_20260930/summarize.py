"""Summarize the complete reserved validation and retain both pilot candidates."""
from pathlib import Path
import csv,json,statistics,hashlib
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]

def mean(values):return statistics.mean(values) if values else None

def summarize(rows,source):
 pairs=[]
 for index in sorted({r['index'] for r in rows}):
  group={r['source']:r for r in rows if r['index']==index}
  assert len(group)==2, (index,group.keys())
  a,b=group['before_source'],group[source]
  valid=a['valid'] and b['valid'] and a.get('objective') is not None and b.get('objective') is not None
  pairs.append(dict(index=index,family=a['family'],model=a['model'],seed=a['seed'],budget=a['budget'],
    before_valid=a['valid'],after_valid=b['valid'],after_periodic=b.get('periodic'),
    before_objective=a.get('objective'),after_objective=b.get('objective'),
    change_percent=100*(b['objective']/a['objective']-1) if valid else None,
    before_wall=a.get('wall'),after_wall=b.get('wall'),
    before_iterations=a.get('iterations'),after_iterations=b.get('iterations')))
 summary=[]
 for model in sorted({p['model'] for p in pairs}):
  group=[p for p in pairs if p['model']==model]; changes=[p['change_percent'] for p in group if p['change_percent'] is not None]
  summary.append(dict(model=model,pairs=len(group),mean_change_percent=mean(changes),
   before_valid=sum(p['before_valid'] for p in group),after_valid=sum(p['after_valid'] for p in group),
   improved=sum(d < -1e-7 for d in changes),equal=sum(abs(d)<=1e-7 for d in changes),
   worse=sum(d>1e-7 for d in changes),worst_change_percent=max(changes) if changes else None))
 return pairs,summary

pilots={}
for folder,source in [('pilot','candidate_source'),('pilot_cached','cached_source')]:
 pairs,summary=summarize(json.loads((OUT/folder/'summary.json').read_text()),source)
 pilots[folder]=dict(pairs=pairs,summary=summary)
(OUT/'pilot_comparison.json').write_text(json.dumps(pilots,indent=2))
rows=json.loads((OUT/'validation/summary.json').read_text())
assert len(rows)==54, len(rows)
pairs,summary=summarize(rows,'final_source')
with (OUT/'paired_validation.csv').open('w') as stream:
 writer=csv.DictWriter(stream,fieldnames=list(pairs[0]));writer.writeheader();writer.writerows(pairs)
by_case=[]
for family in sorted({p['family'] for p in pairs}):
 for model in (1,2,3):
  group=[p for p in pairs if p['family']==family and p['model']==model]
  a=[p['before_objective'] for p in group];b=[p['after_objective'] for p in group]
  if any(v is None for v in a+b):continue
  by_case.append(dict(family=family,model=model,budget=group[0]['budget'],
    before_mean=mean(a),after_mean=mean(b),mean_change_percent=100*(mean(b)/mean(a)-1),
    before_cv_percent=100*statistics.pstdev(a)/mean(a),after_cv_percent=100*statistics.pstdev(b)/mean(b)))
owned=['models/routing/solvers/alns/'+f for f in ('single_batch.py','single_batch_descent.py','partitioned_assignment.py')]
frozen=json.loads((OUT/'final_source_manifest.json').read_text())
report=dict(material_passport=dict(origin_skill='academic-research-suite / experiment-agent',origin_mode='validate',
 verification_status='Verified only for retained runs and independent audits; no optimality or statistical significance claims'),
 pairs=len(pairs),summary=summary,by_case=by_case,
 valid_before=sum(p['before_valid'] for p in pairs),valid_after=sum(p['after_valid'] for p in pairs),
 periodic_after=sum(p['after_periodic'] is True for p in pairs),
 maximum_wall_overrun=max(p['after_wall']-p['budget'] for p in pairs if p['after_wall'] is not None),
 changed_owned_files=[f for f in owned if hashlib.sha256((ROOT/f).read_bytes()).hexdigest()!=frozen[f]],
 selection='M1/M2 cached descent, M3 global enumeration descent, M4 retains previous trajectory; chosen using pilot cases only',
 limitations=['Three validation cases form two source blocks; the 50-customer case is nested in a 100-customer case.',
  'Only 3 seeds per cell, 5s and 10s budgets; machine load not controlled; alternating subprocess execution.',
  'Final source changes only three single-batch ALNS modules relative to the frozen baseline; concurrent shared-code edits assessed separately.',
  'Mean paired percentage changes are descriptive and may hide individual regressions. No certified optimality gap.',
  'The separately retained pilots guided model-specific choices; no per-seed best-of-version selection.'])
(OUT/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
for item in summary:print(item)
print('Valid',report['valid_before'],report['valid_after'],'overrun',report['maximum_wall_overrun'])
