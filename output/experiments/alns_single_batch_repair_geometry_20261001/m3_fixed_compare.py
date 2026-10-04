"""Registered, serial fixed-work comparison; retain all raw schedules and checks."""
from pathlib import Path
import json,math,os,statistics,subprocess,sys
OUT=Path(__file__).resolve().parent
jobs=json.loads((OUT/'m3_fixed_work_manifest.json').read_text())['jobs']
rows=[]
for repeat in range(3):
 for job in jobs:
  for version in (('before_source','m3_source') if repeat%2==0 else ('m3_source','before_source')):
   done=subprocess.run([sys.executable,str(OUT/'m3_fixed_worker.py'),str(OUT/version),str(job['model']),job['case']],
    capture_output=True,text=True,env={**os.environ,'PYTHONHASHSEED':'0'},timeout=80)
   row=dict(job,repeat=repeat,version=version)
   if done.returncode:
    row.update(valid=False,error=done.stderr[-3000:]);print(done.stderr,flush=True)
   else: row.update(json.loads(done.stdout))
   rows.append(row);(OUT/'m3_fixed_work_runs.json').write_text(json.dumps(rows,indent=2))
   print(repeat,job['family'],job['model'],version,row.get('cpu'),row.get('objective'),row.get('valid'),flush=True)
summary=[]
for job in jobs:
 group=[r for r in rows if r['model']==job['model'] and r['family']==job['family']]
 a=[r for r in group if r['version']=='before_source'];b=[r for r in group if r['version']=='m3_source']
 valid=all(r.get('valid') and r.get('periodic') and r.get('iterations')==300 for r in group)
 same=valid and all(all(r[k]==a[0][k] for k in ('e1','e2','assignment','objective','iterations')) for r in group)
 ac=statistics.median(r['cpu'] for r in a if r.get('cpu') is not None)
 bc=statistics.median(r['cpu'] for r in b if r.get('cpu') is not None)
 summary.append(dict(family=job['family'],model=job['model'],identical=same,all_valid=valid,
  before_cpu=ac,after_cpu=bc,cpu_ratio=bc/ac,cpu_reduction_percent=100*(1-bc/ac),
  before_wall=statistics.median(r['wall'] for r in a),after_wall=statistics.median(r['wall'] for r in b)))
model_reductions={str(m):100*(1-math.exp(statistics.mean(math.log(r['cpu_ratio']) for r in summary if r['model']==m))) for m in (3,)}
overall=100*(1-math.exp(statistics.mean(math.log(r['cpu_ratio']) for r in summary)))
eligible=all(r['identical'] and r['all_valid'] and r['cpu_reduction_percent']>=2 for r in summary)
(OUT/'m3_fixed_work_summary.json').write_text(json.dumps(summary,indent=2))
decision=dict(eligible=eligible,runs=len(rows),groups=len(summary),overall_geometric_cpu_reduction_percent=overall,model_geometric_cpu_reduction_percent=model_reductions,gate='M3 both sources each CPU decrease>=2%; all outputs identical and audits/replay valid')
(OUT/'m3_primary_decision.json').write_text(json.dumps(decision,indent=2));print(json.dumps(summary,indent=2));print(json.dumps(decision,indent=2))
