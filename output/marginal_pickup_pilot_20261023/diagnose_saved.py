"""Inspect E1 moves on the two saved final schedules; no optimizer calls."""
from collections import Counter
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from models.common.cycle import load_instance
from models.routing.model_5.alns.evaluator import _e1_trip
from models.routing.model_5.alns.marginal_pickup import _route_cost,marginal_pickup_neighbors
from models.routing.model_5.alns.state import state_from_result
from tools.configuration.loader import load_routing_case

out=Path(__file__).resolve().parent
problem=load_instance(*load_routing_case(ROOT/'data/formal_benchmark27_spacetime_v2/model_5/large/L07/case.json'))
p=problem.operating_policy
reports={}
for strategy in ('cost_aware','cost_aware_marginal'):
    decoded=json.loads((out/'experiment/quality/L07'/strategy/'result.json').read_text())
    state=state_from_result(problem,decoded)
    ready=[p.cycle_start]*problem.fleet.e1_count
    counts=Counter();rows=[]
    for batch in p.batches:
        trips={t['vehicle_id']:t for t in decoded['e1_trips'] if t['batch_id']==batch.id}
        picks={k:[e['site_id'] for e in t['events'] if e['kind']=='p'] for k,t in trips.items()}
        q={e['site_id']:e['quantity'] for t in trips.values() for e in t['events'] if e['kind']=='p'}
        elsewhere={t['vehicle_id'] for t in decoded['e1_trips'] if t['batch_id']!=batch.id}
        cost=lambda k,t:_route_cost(problem,batch,t,k in elsewhere)
        for source,sites in picks.items():
            deliveries=state['deliveries'][batch.id][source]
            if sites and deliveries and len(sites)==1 and sites[0]==deliveries[-1]:
                counts['pickup_at_last_delivery_site']+=1
            for site in sites:
                shortened=[s for s in sites if s!=site]
                st,si=_e1_trip(problem,batch,source,deliveries,shortened,q,ready[source])
                for dest in range(problem.fleet.e1_count):
                    if dest==source:continue
                    other=picks.get(dest,[])
                    for pos in range(len(other)+1):
                        dt,di=_e1_trip(problem,batch,dest,state['deliveries'][batch.id][dest],
                            other[:pos]+[site]+other[pos:],q,ready[dest])
                        issues=si+di
                        delta=cost(source,st)+cost(dest,dt)-cost(source,trips[source])-cost(dest,trips.get(dest))
                        category=('locally_infeasible' if issues else
                                  'transport_saving' if delta < -1e-9 else 'non_saving')
                        counts[category]+=1
                        rows.append(dict(batch=batch.id,site=site,source=source,destination=dest,
                            position=pos,transport_delta=delta,issues=issues,category=category))
        for k,t in trips.items():ready[k]=t['return_complete']+p.e1_turnaround_time
    generated=list(marginal_pickup_neighbors(problem,state,decoded,time.perf_counter()+5))
    reports[strategy]=dict(counts=dict(counts),checked_insertions=len(rows),
        actual_generator_candidates=len(generated),scope='saved final schedule only; no search replay',
        minimum_transport_delta=min(r['transport_delta'] for r in rows),moves=rows)
(out/'saved_schedule_diagnosis.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({s:{k:v for k,v in r.items() if k!='moves'} for s,r in reports.items()},indent=2))
