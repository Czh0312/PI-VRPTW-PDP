"""Check M4 retained behavior and frozen/live integration at fixed iterations."""
from pathlib import Path
import json,subprocess,sys,os
OUT=Path(__file__).resolve().parent; ROOT=OUT.parents[2]
if len(sys.argv)>1:
 source,model,seed,case=sys.argv[1:];sys.path.insert(0,source)
 from configuration.loader import load_routing_case
 from models.routing.common.cycle import load_instance
 from models.routing.common.cycle.audit import audit_solution
 from models.routing.service import solve_routing
 config,fleet=load_routing_case(case);p=load_instance({**config,'model':int(model)},fleet)
 r=solve_routing(model=int(model),method='alns',problem=p,alns_time=60,alns_iter=160,seed=int(seed))
 print(json.dumps(dict(objective=r['info']['objective'],e1=r['e1_trips'],e2=r['e2_trips'],assignment=r.get('assignment'),
  valid=audit_solution(p,r)['valid'],iterations=r['info']['iterations'],descent_enabled=r['info'].get('descent_enabled'))))
else:
 cases=[ROOT/'output/experiments/alns_large_status_20260930/benchmark_inputs/zhou_Set2_100-5-1b/case.json',
        OUT/'benchmark_inputs/zhou_Set2_100-10-1/case.json']
 checks=[]
 for model in (1,2,3,4):
  for seed in (42,43):
   case=cases[(seed-42)%2]; versions=['tail_source','adopted_source','live']
   if model==4:versions.insert(0,'before_source')
   results={}
   for version in versions:
    source=ROOT if version=='live' else OUT/version
    proc=subprocess.run([sys.executable,str(Path(__file__).resolve()),str(source),str(model),str(seed),str(case)],
       capture_output=True,text=True,env={**os.environ,'PYTHONHASHSEED':'0'},timeout=80,check=True)
    results[version]=json.loads(proc.stdout)
   expected=results['adopted_source']
   identical=all(all(r[k]==expected[k] for k in ('objective','e1','e2','assignment','iterations')) for r in results.values())
   valid=all(r['valid'] for r in results.values())
   checks.append(dict(model=model,seed=seed,case=str(case),identical=identical,valid=valid,results=results))
   (OUT/'equivalence.json').write_text(json.dumps(checks,indent=2))
   print(model,seed,'identical',identical,'valid',valid,flush=True)
 assert all(c['identical'] and c['valid'] for c in checks)
