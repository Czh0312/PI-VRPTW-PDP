"""Apply the registered M3 two-source CPU and full-output equality gates."""
from pathlib import Path
import json,os,statistics,subprocess,sys
OUT=Path(__file__).resolve().parent
jobs=json.loads((OUT/'fixed_work_manifest.json').read_text())['jobs'];rows=[]
for repeat in range(3):
 for job in jobs:
  for version in (('before_source','after_source') if repeat%2==0 else ('after_source','before_source')):
   row=dict(job,repeat=repeat,version=version)
   try:
    done=subprocess.run([sys.executable,str(OUT/'fixed_work.py'),str(OUT/version),str(job['model']),job['case']],capture_output=True,text=True,env={**os.environ,'PYTHONHASHSEED':'0'},timeout=80)
    row.update(json.loads(done.stdout) if done.returncode==0 else dict(valid=False,error=done.stderr[-3000:]))
   except subprocess.TimeoutExpired:row.update(valid=False,error='OUTER_TIMEOUT')
   rows.append(row);(OUT/'fixed_work_runs.json').write_text(json.dumps(rows,indent=2))
   print(repeat,job['family'],version,row.get('cpu'),row.get('objective'),row.get('valid'),flush=True)
summary=[]
for job in jobs:
 group=[r for r in rows if r['family']==job['family']]
 a=[r for r in group if r['version']=='before_source'];b=[r for r in group if r['version']=='after_source']
 valid=len(group)==6 and all(r.get('valid') and r.get('periodic') and r.get('iterations')==500 for r in group)
 same=valid and all(all(r[k]==a[0][k] for k in ('e1','e2','assignment','objective','iterations')) for r in group)
 ac=statistics.median(r['cpu'] for r in a) if valid else None;bc=statistics.median(r['cpu'] for r in b) if valid else None
 reduction=100*(1-bc/ac) if valid else None
 summary.append(dict(family=job['family'],model=3,identical=same,all_valid=valid,before_cpu=ac,after_cpu=bc,cpu_reduction_percent=reduction,eligible=same and reduction>=2))
result=dict(runs=len(rows),groups=summary,eligible=all(r['eligible'] for r in summary),gate='Each of2 sources CPU reduction>=2%, all schedules/assignment/objective/500iterations exact,audit/replay valid')
(OUT/'primary_decision.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
