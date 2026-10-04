"""Serial registered CPU comparisons with independent per-model adoption gates."""
from pathlib import Path
import json,math,os,statistics,subprocess,sys
OUT=Path(__file__).resolve().parent
jobs=json.loads((OUT/'fixed_work_manifest.json').read_text())['jobs'];rows=[]
for repeat in range(3):
 for job in jobs:
  for version in (('before_source','after_source') if repeat%2==0 else ('after_source','before_source')):
   row=dict(job,repeat=repeat,version=version)
   try:
    done=subprocess.run([sys.executable,str(OUT/'fixed_work.py'),str(OUT/version),str(job['model']),job['case']],capture_output=True,text=True,env={**os.environ,'PYTHONHASHSEED':'0'},timeout=80)
    row.update(json.loads(done.stdout) if done.returncode==0 else dict(valid=False,error=done.stderr[-3000:]))
   except subprocess.TimeoutExpired: row.update(valid=False,error='OUTER_TIMEOUT')
   rows.append(row);(OUT/'fixed_work_runs.json').write_text(json.dumps(rows,indent=2))
   print(repeat,job['family'],job['model'],version,row.get('cpu'),row.get('objective'),row.get('valid'),flush=True)
summary=[]
for job in jobs:
 group=[r for r in rows if r['model']==job['model'] and r['family']==job['family']]
 a=[r for r in group if r['version']=='before_source'];b=[r for r in group if r['version']=='after_source']
 valid=all(r.get('valid') and r.get('periodic') and r.get('iterations')==300 for r in group)
 same=valid and all(all(r[k]==a[0][k] for k in ('e1','e2','assignment','objective','iterations')) for r in group)
 ac=statistics.median(r['cpu'] for r in a) if valid else None;bc=statistics.median(r['cpu'] for r in b) if valid else None
 summary.append(dict(family=job['family'],model=job['model'],identical=same,all_valid=valid,before_cpu=ac,after_cpu=bc,cpu_ratio=bc/ac if valid else None,cpu_reduction_percent=100*(1-bc/ac) if valid else None,before_wall=statistics.median(r['wall'] for r in a) if valid else None,after_wall=statistics.median(r['wall'] for r in b) if valid else None))
correct=all(r['identical'] and r['all_valid'] for r in summary);decisions=[]
for model in range(1,5):
 group=[r for r in summary if r['model']==model]
 reduction=100*(1-math.exp(statistics.mean(math.log(r['cpu_ratio']) for r in group))) if all(r['all_valid'] for r in group) else None
 eligible=correct and reduction>=2 and all(r['cpu_reduction_percent']>=-5 for r in group)
 decisions.append(dict(model=model,eligible=eligible,geometric_cpu_reduction_percent=reduction))
(OUT/'fixed_work_summary.json').write_text(json.dumps(summary,indent=2))
result=dict(runs=len(rows),groups=len(summary),all_identical_valid=correct,decisions=decisions,any_eligible=any(d['eligible'] for d in decisions),gate='Global numerical/audit guard; per-model geometric CPU reduction>=2%, each source regression<=5%')
(OUT/'primary_decision.json').write_text(json.dumps(result,indent=2));print(json.dumps(summary,indent=2));print(json.dumps(result,indent=2))
