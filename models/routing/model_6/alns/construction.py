"""Independent initial topologies with several consolidation time scales."""
from collections import defaultdict
from .state import Route, State, capacity_ok, route_distance


def priority(problem, layer, action):
    o=problem.order_map[action[0]];p=problem.operating_policy
    if action[1]=='d':
        return max(p.cycle_start,o.delivery_window[0]-problem.travel(o.satellite,o.customer)-p.e2_dispatch_prep_time-
                   (problem.travel(problem.depot,o.satellite)+problem.service(o.satellite)+p.e1_load_time if layer=='e1' else 0))
    pickup=max(o.pickup_window[0],o.delivery_window[0]+o.service_time+o.min_use_time)
    return pickup+(o.service_time+problem.travel(o.customer,o.satellite)+p.e2_return_unload_time if layer=='e1' else -problem.travel(o.satellite,o.customer))


def local_windows_ok(problem,route):
    if route.layer=='e1': return True
    p=problem.operating_policy
    t=p.cycle_start+(p.e2_dispatch_prep_time if any(k=='d' for _,k in route.actions) else 0)
    site=route.base
    for oid,k in route.actions:
        o=problem.order_map[oid];lo,hi=o.delivery_window if k=='d' else o.pickup_window
        t=max(t+problem.travel(site,o.customer),lo)
        if t>hi: return False
        t+=o.service_time;site=o.customer
    return t+problem.travel(site,route.base)+p.e2_return_unload_time<=p.cycle_end


def initial_states(problem):
    p,f=problem.operating_policy,problem.fleet
    for span in (0,60,180,100000):
        for delivery_first in (False,True):
            state=State()
            for layer in ('e1','e2'):
                pending=[]
                for s in problem.satellites:
                    for kind in ('d','p'):
                        actions=sorted([(o.id,kind) for o in problem.orders if o.satellite==s],
                                       key=lambda a:priority(problem,layer,a))
                        chunk=[]
                        base=problem.depot if layer=='e1' else s
                        for a in actions:
                            proposed=Route(layer,base,0,chunk+[a])
                            close=not chunk or (span>0 and abs(priority(problem,layer,a)-priority(problem,layer,chunk[0]))<=p.ticks(span))
                            if chunk and (not close or not capacity_ok(problem,proposed) or not local_windows_ok(problem,proposed)):
                                pending.append(Route(layer,base,0,chunk));chunk=[]
                            chunk.append(a)
                        if chunk:pending.append(Route(layer,base,0,chunk))
                pending.sort(key=lambda r:((0 if r.actions[0][1]=='d' else 1) if delivery_first and layer=='e1' else 0,
                                           min(priority(problem,layer,a) for a in r.actions)))
                available=defaultdict(lambda:p.cycle_start);counts=defaultdict(int)
                for r in pending:
                    n=f.e1_count if layer=='e1' else f.e2_per_sat_count
                    cap=problem.e1_max_trips if layer=='e1' else p.e2_max_trips_per_vehicle
                    choices=[v for v in range(n) if counts[r.base,v]<cap] or list(range(n))
                    r.vehicle=min(choices,key=lambda v:(available[r.base,v],counts[r.base,v],v))
                    key=(r.base,r.vehicle);counts[key]+=1
                    t=max(available[key],min(priority(problem,layer,a) for a in r.actions))
                    # Used only to distribute initial jobs; evaluator rebuilds all actual times.
                    available[key]=t+p.ticks(route_distance(problem,r)/f.speed)+sum(problem.order_map[a[0]].service_time for a in r.actions)
                    state.routes[layer].append(r)
            yield state
