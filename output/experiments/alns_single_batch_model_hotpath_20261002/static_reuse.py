"""Observe hypothetical context-local static metadata reuse, never reuse scores."""
from pathlib import Path
from collections import OrderedDict
import json,os,sys
OUT=Path(__file__).resolve().parent;sys.path.insert(0,str(OUT/'before_source'))
from configuration.loader import load_routing_case
from models.routing.common.cycle import load_instance
from models.routing.common.cycle.audit import audit_solution
from models.routing.service import solve_routing
from models.routing.solvers.alns.single_batch_scoring import SingleBatchRouteScorer
original=SingleBatchRouteScorer.__call__
current=None

def observed(self,problem,route,goods,ready):
    global current
    if route['tasks']:
        key=tuple(route['tasks'])
        if not hasattr(self,'_static_diag_lrus'):
            self._static_diag_lrus={name:OrderedDict() for name in ('route512','route4096','delivery512')}
            current['scorer_contexts']+=1
        current['nonempty_calls']+=1;current['task_events']+=len(key)
        delivery=tuple(t for t in key if t[1]=='d')
        for name,signature,capacity in (('route512',key,512),('route4096',key,4096),('delivery512',delivery,512)):
            lru=self._static_diag_lrus[name];stats=current['reuse'][name]
            if signature in lru:
                stats['hits']+=1;lru.move_to_end(signature)
            else:
                stats['misses']+=1;lru[signature]=None
                if len(lru)>capacity:lru.popitem(last=False)
            stats['maximum_context_entries']=max(stats['maximum_context_entries'],len(lru))
    else:
        current['empty_calls']+=1
    return original(self,problem,route,goods,ready)

SingleBatchRouteScorer.__call__=observed
rows=[]
for index,job in enumerate(json.loads((OUT/'diagnostic_jobs.json').read_text())['jobs']):
    current=dict(nonempty_calls=0,empty_calls=0,task_events=0,scorer_contexts=0,
                 reuse={name:dict(hits=0,misses=0,maximum_context_entries=0) for name in ('route512','route4096','delivery512')})
    config,fleet=load_routing_case(job['case']);problem=load_instance({**config,'model':job['model']},fleet)
    before=os.getloadavg()
    result=solve_routing(model=job['model'],method='alns',problem=problem,alns_time=job['budget'],alns_iter=job['iterations'],seed=job['seed'])
    audit=audit_solution(problem,result)
    raw=dict(job=job,result=result,audit=audit,counters=current,load_before=before,load_after=os.getloadavg())
    (OUT/('%02d_reuse_result.json'%index)).write_text(json.dumps(raw,indent=2))
    for stats in current['reuse'].values():
        stats['hit_percent']=100*stats['hits']/(stats['hits']+stats['misses']) if stats['hits']+stats['misses'] else 0
    row=dict(job,counters=current,objective=result['info']['objective'],iterations=result['info']['iterations'],full_audit_valid=audit['valid'],periodic=audit['periodic_replay']['valid'])
    rows.append(row);(OUT/'reuse_summary.json').write_text(json.dumps(rows,indent=2))
    print(job['family'],job['model'],current['nonempty_calls'],[(k,round(v['hit_percent'],2)) for k,v in current['reuse'].items()],flush=True)
    assert audit['valid'] and audit['periodic_replay']['valid']
