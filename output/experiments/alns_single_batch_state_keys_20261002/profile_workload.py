"""Profile frozen fixed-work M3; do not treat instrumented time as CPU evidence."""
from pathlib import Path
import cProfile
import json
import os
import pstats
import sys

OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(OUT/'before_source'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.service import solve_routing

rows=[]
for index,job in enumerate(json.loads((OUT/'cpu_jobs.json').read_text())['jobs']):
    config,fleet=load_routing_case(job['case'])
    problem=load_instance({**config,'model':job['model']},fleet)
    profiler=cProfile.Profile()
    result=profiler.runcall(solve_routing,model=3,method='alns',problem=problem,
                          alns_time=job['budget'],alns_iter=job['iterations'],seed=job['seed'])
    profiler.dump_stats(str(OUT/('%02d.pstats'%index)))
    audit=audit_solution(problem,result)
    assert audit['valid'] and audit['periodic_replay']['valid']
    stats=pstats.Stats(profiler)
    functions=[]
    for (file,line,name),(primitive,calls,self_time,cumulative,callers) in stats.stats.items():
        functions.append(dict(file=file,line=line,name=name,calls=calls,primitive=primitive,
                              self_seconds=self_time,cumulative_seconds=cumulative,
                              callers=[dict(file=k[0],line=k[1],name=k[2],stats=v) for k,v in callers.items()]))
    row=dict(job,total_profile_seconds=stats.total_tt,load=os.getloadavg(),
             objective=result['info']['objective'],iterations=result['info']['iterations'],
             full_audit_valid=audit['valid'],periodic=audit['periodic_replay']['valid'],
             top_self=sorted(functions,key=lambda x:-x['self_seconds'])[:30],
             signatures=[x for x in functions if x['name']=='state_key'],
             top_cumulative=sorted(functions,key=lambda x:-x['cumulative_seconds'])[:15])
    rows.append(row)
    (OUT/'profile_summary.json').write_text(json.dumps(rows,indent=2))
    print(job['family'],'profile_seconds',stats.total_tt,'signatures',
          [(x['calls'],x['cumulative_seconds'],100*x['cumulative_seconds']/stats.total_tt)
           for x in row['signatures']],flush=True)
