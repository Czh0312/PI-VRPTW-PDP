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
 result=solve_routing(model=int(model),method='alns',problem=p,alns_time=60,alns_iter=500,seed=291)
 cpu,wall=time.process_time()-cpu,time.perf_counter()-wall
 audit=audit_solution(p,result)
 print(json.dumps(dict(cpu=cpu,wall=wall,objective=result['info']['objective'],iterations=result['info']['iterations'],
  valid=audit['valid'],periodic=audit['periodic_replay']['valid'],e1=result['e1_trips'],e2=result['e2_trips'],assignment=result.get('assignment'),trial_hits=result['info'].get('e1_trial_cache_hits'),trial_misses=result['info'].get('e1_trial_cache_misses'),trial_entries=result['info'].get('e1_trial_cache_entries'),trial_capacity=result['info'].get('e1_trial_cache_capacity'))))
