"""Run registered pairs in alternating source order; retain failures and raw audits."""
import hashlib,json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def main():
 for name,h in json.loads((ROOT/'registration_hashes.json').read_text()).items():
  assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==h,name
 manifest=Path(sys.argv[1]); phase=sys.argv[2]; candidate=sys.argv[3]
 jobs=json.loads(manifest.read_text())['jobs']; out=ROOT/phase; out.mkdir()
 rows=[]
 for i,job in enumerate(jobs):
  sources=['before_source',candidate]
  if i%2: sources.reverse()
  for source in sources:
   name=f'{i:03d}_{source}'; dest=out/(name+'.json')
   script=ROOT/source/'tools/audit/benchmark_single_batch_solvers.py'
   started=time.time(); row=dict(job,index=i,source=source,load_before=os.getloadavg())
   try:
    p=subprocess.run([sys.executable,str(script),'--worker',json.dumps(job),'--output',str(dest)],
      cwd=script.parents[2],env={**os.environ,'PYTHONHASHSEED':'0'},capture_output=True,text=True,timeout=job['budget']+20)
    (out/(name+'.log')).write_text(p.stdout+p.stderr)
    if p.returncode: row.update(valid=False,error=p.stderr[-3000:])
    else:
     raw=json.loads(dest.read_text()); info=raw['result']['info']; audit=raw['independent_audit']
     row.update(objective=info.get('objective'),valid=audit['valid'],periodic=audit.get('periodic_replay',{}).get('valid'),
      wall=raw['solve_wall_seconds'],iterations=info.get('iterations'),descent_improvements=info.get('operator_improvements',{}).get('descent'),
      assignment_terminal_checks=info.get('assignment_terminal_checks',0),assignment_terminal_steps=info.get('assignment_terminal_steps',0),assignment_terminal_direct_steps=info.get('assignment_terminal_direct_steps',0),assignment_terminal_repaired_steps=info.get('assignment_terminal_repaired_steps',0),assignment_terminal_timeouts=info.get('assignment_terminal_timeouts',0),assignment_terminal_gains=info.get('assignment_terminal_gains',[]),assignment_terminal_decodes=info.get('assignment_terminal_decodes',0),e1_trial_cache_hits=info.get('e1_trial_cache_hits'),e1_trial_cache_misses=info.get('e1_trial_cache_misses'),e1_trial_cache_entries=info.get('e1_trial_cache_entries'),e1_trial_cache_capacity=info.get('e1_trial_cache_capacity'),assignment_attempts=info.get('assignment_attempts'),assignment_route_projections=info.get('assignment_route_projections'),
      late_pickup_policy=info.get('late_pickup_policy'),late_pickup_checks=info.get('late_pickup_checks'),late_pickup_uphill=info.get('late_pickup_uphill'),late_pickup_vetoes=info.get('late_pickup_vetoes'),descent_queue_capacity=info.get('descent_queue_capacity',256),descent_e2_queue_peak=info.get('descent_e2_queue_peak',0),descent_seconds=info.get('descent_seconds'),descent_attempts=info.get('operator_attempts',{}).get('descent'),descent_load_screen_checks=info.get('descent_load_screen_checks',0),descent_load_screen_rejections=info.get('descent_load_screen_rejections',0))
   except subprocess.TimeoutExpired as exc:
    row.update(valid=False,error='OUTER_TIMEOUT')
    captured=(exc.stdout or b'')+(exc.stderr or b'')
    (out/(name+'.log')).write_bytes(captured if isinstance(captured,bytes) else captured.encode())
   row['started']=started;row['load_after']=os.getloadavg(); rows.append(row)
   (out/'summary.json').write_text(json.dumps(rows,indent=2))
   print(i,source,job['model'],job['family'],job['seed'],row.get('objective'),row.get('valid'),flush=True)

if __name__=='__main__':main()
