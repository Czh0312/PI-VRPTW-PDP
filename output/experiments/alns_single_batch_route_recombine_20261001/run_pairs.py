"""Run registered pairs in alternating source order; retain failures and raw audits."""
import json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def main():
 manifest=Path(sys.argv[1]); phase=sys.argv[2]; candidate=sys.argv[3]
 jobs=json.loads(manifest.read_text())['jobs']; out=ROOT/phase; out.mkdir()
 rows=[]
 for i,job in enumerate(jobs):
  sources=['before_source',candidate]
  if i%2: sources.reverse()
  for source in sources:
   name=f'{i:03d}_{source}'; dest=out/(name+'.json')
   script=ROOT/source/'tools/audit/benchmark_single_batch_solvers.py'
   started=time.time(); row=dict(job,index=i,source=source)
   try:
    p=subprocess.run([sys.executable,str(script),'--worker',json.dumps(job),'--output',str(dest)],
      cwd=script.parents[2],env={**os.environ,'PYTHONHASHSEED':'0'},capture_output=True,text=True,timeout=job['budget']+20)
    (out/(name+'.log')).write_text(p.stdout+p.stderr)
    if p.returncode: row.update(valid=False,error=p.stderr[-3000:])
    else:
     raw=json.loads(dest.read_text()); info=raw['result']['info']; audit=raw['independent_audit']
     row.update(objective=info.get('objective'),valid=audit['valid'],periodic=audit.get('periodic_replay',{}).get('valid'),
      wall=raw['solve_wall_seconds'],iterations=info.get('iterations'),descent_improvements=info.get('operator_improvements',{}).get('descent'),
      assignment_tail_attempts=info.get('assignment_tail_attempts',0),assignment_tail_checks=info.get('assignment_tail_checks',0),assignment_tail_improvements=info.get('assignment_tail_improvements',0),assignment_tail_deltas=info.get('assignment_tail_deltas',[]),assignment_attempts=info.get('assignment_attempts'),assignment_route_projections=info.get('assignment_route_projections'),
      descent_seconds=info.get('descent_seconds'),descent_attempts=info.get('operator_attempts',{}).get('descent'))
   except subprocess.TimeoutExpired: row.update(valid=False,error='OUTER_TIMEOUT')
   row['started']=started; rows.append(row)
   (out/'summary.json').write_text(json.dumps(rows,indent=2))
   print(i,source,job['model'],job['family'],job['seed'],row.get('objective'),row.get('valid'),flush=True)

if __name__=='__main__':main()
