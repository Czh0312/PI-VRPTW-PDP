"""Earliest feasible event schedule of a route topology, with explicit waits.

Longest paths in a precedence DAG synchronize both echelons and washing.
The independent business audit subsequently checks the complete result.
"""
from collections import defaultdict, deque

from models.common.no_batch.audit import audit_solution
from models.common.no_batch.instance import VERSION
from .state import groups, capacity_ok


def evaluate(problem, state):
    p = problem.operating_policy
    lower, upper, children, indegree = {}, {}, defaultdict(list), defaultdict(int)
    vehicles, task_nodes, descriptors = defaultdict(list), {}, []
    def node(key, lo=None, hi=None):
        lower[key] = p.cycle_start if lo is None else lo
        upper[key] = p.cycle_end if hi is None else hi
        return key
    def edge(a,b,lag):
        children[a].append((b,lag)); indegree[b] += 1
    for layer in ('e1','e2'):
        for index, r in enumerate(state.routes[layer]):
            if not r.actions or not capacity_ok(problem,r):
                return None
            rid = f'{layer}_{index}'
            delivered = [oid for oid,k in r.actions if k=='d']
            prep = (p.e1_load_time if layer=='e1' else p.e2_dispatch_prep_time) if delivered else 0
            turn = p.e1_turnaround_time if layer=='e1' else p.e2_turnaround_time
            begin = node((rid,'load'), max([p.cycle_start]+[problem.order_map[o].release_time or p.cycle_start for o in delivered]) if layer=='e1' else None)
            end = node((rid,'end'), hi=p.cycle_end-turn)
            if layer=='e1' and any(k=='p' for _,k in r.actions):
                upper[end] = min(upper[end],p.cycle_end-p.cleaning_duration)
            vehicles[layer,r.base,r.vehicle].append((begin,end,turn))
            previous, lastsite, lastduration = begin, r.base, prep
            visits = []
            for j,(site,kind,oids) in enumerate(groups(problem,r)):
                o = problem.order_map[oids[0]]
                service = problem.service(site) if layer=='e1' else o.service_time
                window = (p.cycle_start,p.cycle_end) if layer=='e1' else (o.delivery_window if kind=='d' else o.pickup_window)
                current = node((rid,j), *window)
                edge(previous,current,lastduration+problem.travel(lastsite,site))
                for oid in oids:
                    key = (layer,oid,kind)
                    if key in task_nodes:
                        return None
                    task_nodes[key] = (current,service,begin,end)
                visits.append((current,site,kind,oids))
                previous,lastsite,lastduration = current,site,service
            unload = p.depot_return_unload_time if layer=='e1' else p.e2_return_unload_time
            edge(previous,end,lastduration+problem.travel(lastsite,r.base)+unload)
            descriptors.append((r,rid,begin,end,prep,visits))
    for o in problem.orders:
        if any((l,o.id,k) not in task_nodes for l in ('e1','e2') for k in ('d','p')):
            return None
        a,b,c,d = (task_nodes[l,o.id,k] for l,k in (('e1','d'),('e2','d'),('e2','p'),('e1','p')))
        edge(a[0],b[2],a[1])
        edge(b[0],c[0],b[1]+o.min_use_time)
        edge(c[3],d[0],0)
    for a,b in state.reuse:
        if a == b or a not in problem.order_map or b not in problem.order_map:
            return None
        edge(task_nodes['e1',a,'p'][3],task_nodes['e1',b,'d'][2],p.cleaning_duration)
    for rows in vehicles.values():
        for a,b in zip(rows,rows[1:]):
            edge(a[1],b[0],a[2])
    q = deque(k for k in lower if indegree[k]==0)
    sequence = []
    while q:
        a=q.popleft(); sequence.append(a)
        if lower[a] > upper[a]:
            return None
        for b,lag in children[a]:
            lower[b] = max(lower[b],lower[a]+lag)
            indegree[b]-=1
            if indegree[b]==0: q.append(b)
    if len(sequence) != len(lower):
        return None
    latest = upper.copy()
    for a in reversed(sequence):
        for b,lag in children[a]: latest[a]=min(latest[a],latest[b]-lag)
    result=dict(schema_version=3,model=10,policy_version=VERSION,batch_mode='no_batch',time_unit='ticks',e1_trips=[],e2_trips=[])
    for r,rid,begin,end,prep,visits in descriptors:
        ev=[]
        for key,site,kind,oids in visits:
            ev.append(dict(site_id=site,kind=kind,order_ids=list(oids),quantity=len(oids),start=lower[key]) if r.layer=='e1' else
                      dict(order_id=oids[0],kind=kind,start=lower[key]))
        trip=dict(trip_id=rid,vehicle_id=r.vehicle,loading_start=lower[begin],departure=lower[begin]+prep,
                  return_complete=lower[end],events=ev)
        if r.layer=='e2': trip['satellite_id']=r.base
        result[r.layer+'_trips'].append(trip)
    audit=audit_solution(problem,result)
    if any(e['code'] not in ('POOL_LIMIT','TRANSPORT_BUDGET') for e in audit['errors']):
        return None
    result['audit']=audit
    result['container_pool_size']=audit['minimum_pool_for_schedule']
    result['feasible']=audit['valid']
    return dict(result=result,pool=result['container_pool_size'],cost=audit['transport_cost'],
                issue={o.id:lower[task_nodes['e1',o.id,'d'][2]] for o in problem.orders},
                issue_latest={o.id:latest[task_nodes['e1',o.id,'d'][2]] for o in problem.orders},
                clean={o.id:lower[task_nodes['e1',o.id,'p'][3]]+p.cleaning_duration for o in problem.orders})


def improve_pool(problem,state,value,deadline=None,attempts=40):
    """Delay selected load operations until a previous return has been washed."""
    import time
    tried=0
    while value is not None and (value['pool']>problem.pool_max or problem.objective_mode=='min_pool'):
        pairs=[(value['clean'][a]-value['issue'][b],a,b) for a in problem.order_map for b in problem.order_map
               if a!=b and (a,b) not in state.reuse and value['issue'][b]<value['clean'][a]<=value['issue_latest'][b]]
        changed=False
        for _,a,b in sorted(pairs):
            if tried>=attempts or (deadline is not None and time.perf_counter()>=deadline): return state,value
            candidate=state.copy();candidate.reuse.append((a,b));tried+=1
            tested=evaluate(problem,candidate)
            if tested and tested['pool']<value['pool']:
                state,value,changed=candidate,tested,True
                break
        if not changed: break
    return state,value
