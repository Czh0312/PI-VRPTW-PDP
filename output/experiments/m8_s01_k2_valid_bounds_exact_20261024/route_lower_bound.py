"""Exhaustive feasible-trip relaxation and exact task-set partition lower bound.

Relaxes inter-trip vehicle scheduling, cross-trip delivery->pickup precedence,
satellite supply times, and E1 recovery cutoffs. Keeps each trip's full initial
delivery load, original task windows, capacities, same-trip precedence/use,
single-delivery-batch rule, travel/service/unload and full task coverage.
Every original feasible E2 trip is represented. Thus its optimum is a lower
bound, even if the minimizing trip collection cannot be jointly scheduled.
"""
from pathlib import Path
import importlib.util,itertools,functools,math,time,json
OUT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('route_bound_certificate',OUT/'certify_reductions.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
t=c.trial

def main():
    cfg,fleet=t.d.load_routing_case(t.BASE/'cases/S01_m8_k1_4.json',use_pool_config=False)
    p=t.d.load_instance(cfg,fleet);policy=p.operating_policy;f=p.fleet
    seed=t.read(t.SEED);audit=t.d.audit_solution(p,seed);assert audit['valid']
    cert=c.certificate(p,audit['objective']);orders=sorted(p.orders,key=lambda o:o.id);n=len(orders)
    assert n==7 and len(p.satellites)==1 and f.e2_volume_cap==3 and f.container_volume==1
    assert all(o.count==1 for o in orders)
    site=next(iter(p.satellites));start=time.perf_counter();routes={};nodes=0
    first=set(cert['forced_first_batch_orders'])
    groups=[[i for i,o in enumerate(orders) if o.id in first],[i for i,o in enumerate(orders) if o.id not in first]]
    Dsets=[()]
    for group in groups:
        for size in range(1,min(3,len(group))+1):Dsets+=list(itertools.combinations(group,size))
    for chosen in Dsets:
        initial_w=sum(orders[i].goods_weight+f.container_weight for i in chosen)
        if initial_w>f.e2_weight_cap+1e-8:continue
        chosen_mask=sum(1<<i for i in chosen);dtimes={}
        def walk(at,clock,mask,remaining,weight,volume,pc,distance,path):
            nonlocal nodes
            nodes+=1
            if not remaining and mask:
                end=clock+p.travel(at,site)+policy.e2_return_unload_time
                value=distance+p.distance(at,site)
                if end<=policy.cycle_end and value<routes.get(mask,(math.inf,None))[0]:
                    routes[mask]=(value,list(path))
            for i in chosen:
                if not remaining>>i&1:continue
                o=orders[i];arrive=max(clock+p.travel(at,o.customer),o.delivery_window[0])
                if arrive>o.delivery_window[1]:continue
                w=weight-o.goods_weight-f.container_weight;v=volume-1
                if w<-1e-7 or v<0:continue
                dtimes[i]=arrive
                walk(o.customer,arrive+o.service_time,mask|1<<i,remaining^(1<<i),w,v,pc,
                     distance+p.distance(at,o.customer),path+[(o.id,'d',arrive)])
                del dtimes[i]
            if pc==3:return
            for i,o in enumerate(orders):
                bit=1<<(n+i)
                if mask&bit or (chosen_mask>>i&1 and remaining>>i&1):continue
                if volume+1>f.e2_volume_cap or weight+f.container_weight>f.e2_weight_cap+1e-8:continue
                earliest=max(o.pickup_window[0],o.delivery_window[0]+o.service_time+o.min_use_time)
                if i in dtimes:earliest=max(earliest,dtimes[i]+o.service_time+o.min_use_time)
                arrive=max(clock+p.travel(at,o.customer),earliest)
                if arrive>o.pickup_window[1]:continue
                walk(o.customer,arrive+o.service_time,mask|bit,remaining,weight+f.container_weight,volume+1,pc+1,
                     distance+p.distance(at,o.customer),path+[(o.id,'p',arrive)])
        walk(site,policy.cycle_start,0,chosen_mask,initial_w,len(chosen),0,0,[])
    assert routes
    bytask=[[] for _ in range(2*n)]
    for mask,(cost,path) in routes.items():
        for i in range(2*n):
            if mask>>i&1:bytask[i].append((mask,cost))
    choice={}
    @functools.lru_cache(None)
    def partition(mask):
        if not mask:return 0.0
        bit=(mask&-mask).bit_length()-1;best=math.inf
        for route,cost in bytask[bit]:
            if route&mask!=route:continue
            value=cost+partition(mask^route)
            if value<best:best=value;choice[mask]=route
        return best
    full=(1<<(2*n))-1;distance_bound=partition(full)
    assert math.isfinite(distance_bound)
    cover=[];mask=full
    while mask:
        route=choice[mask];cover.append(dict(mask=route,distance=routes[route][0],tasks=routes[route][1]));mask^=route
    total=20+cert['E1_distance_lower_bound']*p.cost_profile.rates['e1_distance_cost_per_km']+cert['constant_cost']+distance_bound*p.cost_profile.rates['e2_distance_cost_per_km']
    result=dict(exact_relaxation=True,relaxations=['inter-trip vehicle scheduling','cross-trip delivery/pickup precedence',
        'goods supply times','E1 cutoff eligibility'],orders=[o.id for o in orders],
        enumerated_prefixes=nodes,feasible_task_masks=len(routes),set_partition_states=partition.cache_info().currsize,
        E2_distance_lower_bound=distance_bound,total_cost_lower_bound=total,incumbent_cost=audit['objective'],
        absolute_gap_to_incumbent=audit['objective']-total,elapsed_seconds=time.perf_counter()-start,
        cover=cover,global_K2_scope='Requires the separately verified pool/assignment reduction certificate')
    assert total<=audit['objective']+1e-6
    t.save('route_lower_bound.json',result)
    t.save('route_bound_patterns.json',[dict(mask=mask,distance=cost,tasks=path) for mask,(cost,path) in routes.items()])
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
