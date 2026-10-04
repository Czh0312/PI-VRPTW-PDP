"""All registered jobs, stratified by model/budget and source. No best-seed selection."""
from pathlib import Path
import csv,json,statistics,hashlib
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[2]
rows=json.loads((OUT/'quality/summary.json').read_text())
jobs=json.loads((OUT/'quality_manifest.json').read_text())['jobs'];assert len(rows)==2*len(jobs)
pairs=[]
for index,job in enumerate(jobs):
 group={r['source']:r for r in rows if r['index']==index};assert len(group)==2
 a,b=group['before_source'],group['after_source'];valid=a['valid'] and b['valid'] and a.get('objective') is not None and b.get('objective') is not None
 pairs.append(dict(family=job['family'],model=job['model'],seed=job['seed'],budget=job['budget'],
  cohort=job['cohort'],
  before_valid=a['valid'],after_valid=b['valid'],after_periodic=b.get('periodic'),
  before_objective=a.get('objective'),after_objective=b.get('objective'),
  change_percent=100*(b['objective']/a['objective']-1) if valid else None,
  before_wall=a.get('wall'),after_wall=b.get('wall'),before_iterations=a.get('iterations'),after_iterations=b.get('iterations')))
with (OUT/'paired_results.csv').open('w') as stream:
 w=csv.DictWriter(stream,fieldnames=list(pairs[0]));w.writeheader();w.writerows(pairs)
summary=[]
for budget in sorted({p['budget'] for p in pairs}):
 for model in sorted({p['model'] for p in pairs if p['budget']==budget}):
  g=[p for p in pairs if p['budget']==budget and p['model']==model];ds=[p['change_percent'] for p in g if p['change_percent'] is not None]
  summary.append(dict(model=model,budget=budget,pairs=len(g),mean_change_percent=statistics.mean(ds) if ds else None,
   improved=sum(d < -1e-7 for d in ds),equal=sum(abs(d)<=1e-7 for d in ds),worse=sum(d>1e-7 for d in ds),
   maximum_regression_percent=max(0,max(ds)) if ds else None,valid=sum(p['after_valid'] for p in g)))
by_case=[]
for family in sorted({p['family'] for p in pairs}):
 for model in sorted({p['model'] for p in pairs if p['family']==family}):
  g=[p for p in pairs if p['family']==family and p['model']==model]
  a=[p['before_objective'] for p in g];b=[p['after_objective'] for p in g]
  if any(v is None for v in a+b):continue
  by_case.append(dict(family=family,model=model,budget=g[0]['budget'],before_mean=statistics.mean(a),after_mean=statistics.mean(b),
   mean_change_percent=100*(statistics.mean(b)/statistics.mean(a)-1),
   before_cv_percent=100*statistics.pstdev(a)/statistics.mean(a),after_cv_percent=100*statistics.pstdev(b)/statistics.mean(b)))
owned=['models/routing/solvers/alns/'+f for f in ('single_batch.py','neighborhoods.py','single_batch_cache.py','single_batch_timing.py','partitioned_assignment.py','evaluator.py')]
manifest=json.loads((OUT/'after_source_manifest.json').read_text())
report=dict(material_passport=dict(origin_skill='academic-research-suite / experiment-agent',mode='validate',
 verification_status='VERIFIED for retained audits and fixed-iteration equivalence; not optimality or significance'),
 pairs=len(pairs),valid_before=sum(p['before_valid'] for p in pairs),valid_after=sum(p['after_valid'] for p in pairs),
 periodic_after=sum(p['after_periodic'] is True for p in pairs),summary=summary,by_case=by_case,
 max_wall_overrun=max(p['after_wall']-p['budget'] for p in pairs if p['after_wall'] is not None),
 candidate_source_drift=[f for f in owned if hashlib.sha256((ROOT/f).read_bytes()).hexdigest()!=manifest[f]],
 live_source_drift=[f for f in owned if (OUT/'adopted_source_manifest.json').exists() and hashlib.sha256((ROOT/f).read_bytes()).hexdigest()!=json.loads((OUT/'adopted_source_manifest.json').read_text())[f]],
 fixed_work=json.loads((OUT/'fixed_work_summary.json').read_text()),
 limitations=['5-second jobs use nested50/100 source03 scenario02; mother source known from previous rounds, unused in this round development pilots; 3 seeds each.',
  '15-second jobs use M1-M4 on source01 scenario02, 2 seeds each; geometry was used in fixed-work benchmarks, not a fully heldout source.',
  'Fixed-work timings: 300 iterations, seed42, two 100-customer sources, 3 independent-process repetitions each.',
  'Pairs are alternated sequentially, but machine load is uncontrolled; all results are descriptive.',
  'Faster equivalent scoring preserves fixed-iteration trajectories when wall limits do not bind. Equal-wall-time cooling and stopping can still yield regressions.',
  'No global optimum, certified gap, full-dataset or 30-second-plus claims.'])
short=next(r for r in summary if r['model']==2 and r['budget']==5)
long=next(r for r in summary if r['model']==2 and r['budget']==15)
report['worst_removal_met_rule']=(short['improved']>=4 and short['mean_change_percent']<=0 and long['mean_change_percent']<=0 and short['valid']==6 and long['valid']==2)
report['timing_scope']='M3/M4 only; fixed-work ablation also measured pure models but those paths are not adopted'
(OUT/'comparison.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
print(json.dumps(summary,indent=2));print('valid',report['valid_after'],'max overrun',report['max_wall_overrun'])
