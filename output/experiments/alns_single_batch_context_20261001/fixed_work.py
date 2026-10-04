"""Fixed iterations, uninstrumented CPU/wall time, alternating frozen versions."""
from pathlib import Path
import os,json,subprocess,sys,time,statistics
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[2]
if len(sys.argv)>1:
 source,model,case=sys.argv[1:];sys.path.insert(0,source)
 from configuration.loader import load_routing_case
 from models.routing.common.cycle import load_instance
 from models.routing.common.cycle.audit import audit_solution
 from models.routing.service import solve_routing
 config,fleet=load_routing_case(case);p=load_instance({**config,'model':int(model)},fleet)
 cpu,wall=time.process_time(),time.perf_counter()
 result=solve_routing(model=int(model),method='alns',problem=p,alns_time=60,alns_iter=300,seed=42)
 cpu,wall=time.process_time()-cpu,time.perf_counter()-wall
 print(json.dumps(dict(cpu=cpu,wall=wall,objective=result['info']['objective'],iterations=result['info']['iterations'],
  valid=audit_solution(p,result)['valid'],e1=result['e1_trips'],e2=result['e2_trips'],assignment=result.get('assignment'))))
else:
 cases=[ROOT/'output/experiments/alns_large_status_20260930/benchmark_inputs/zhou_Set2_100-5-1b/case.json',
        ROOT/'output/experiments/alns_single_batch_descent_20260930/benchmark_inputs/zhou_Set2_100-10-1/case.json']
 rows=[]
 for repeat in range(3):
  for case in cases:
   for model in range(1,5):
    for version in (('before_source','after_source') if repeat%2==0 else ('after_source','before_source')):
     done=subprocess.run([sys.executable,str(Path(__file__).resolve()),str(OUT/version),str(model),str(case)],
       capture_output=True,text=True,env={**os.environ,'PYTHONHASHSEED':'0'},check=True,timeout=80)
     value=json.loads(done.stdout);value.update(repeat=repeat,version=version,model=model,family=case.parent.name)
     rows.append(value);(OUT/'fixed_work_runs.json').write_text(json.dumps(rows,indent=2))
     print(repeat,case.parent.name,model,version,round(value['cpu'],4),value['objective'],flush=True)
 summary=[]
 for case in cases:
  for model in range(1,5):
   a=[r for r in rows if r['family']==case.parent.name and r['model']==model and r['version']=='before_source']
   b=[r for r in rows if r['family']==case.parent.name and r['model']==model and r['version']=='after_source']
   same=all(all(r[k]==a[0][k] for k in ('e1','e2','assignment','objective','iterations')) for r in a+b)
   assert same and all(r['valid'] and r['iterations']==300 for r in a+b),(case,model)
   ac,bc=statistics.median(r['cpu'] for r in a),statistics.median(r['cpu'] for r in b)
   summary.append(dict(family=case.parent.name,model=model,identical=True,before_cpu=ac,after_cpu=bc,
    cpu_reduction_percent=100*(1-bc/ac),speedup=ac/bc,before_wall=statistics.median(r['wall'] for r in a),
    after_wall=statistics.median(r['wall'] for r in b)))
 (OUT/'fixed_work_summary.json').write_text(json.dumps(summary,indent=2))
 print(json.dumps(summary,indent=2))
