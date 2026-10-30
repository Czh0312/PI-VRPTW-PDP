"""S01-specific optimality-preserving reductions, with enumerated lower bound."""
from pathlib import Path
import json,math,itertools,importlib.util
OUT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('bounds_certificate_trial',OUT/'solve_k2.py')
trial=importlib.util.module_from_spec(spec);spec.loader.exec_module(trial)

def certificate(parent,incumbent,grid_seconds=60):
    p=parent.operating_policy;orders=parent.orders
    assert len(parent.satellites)==1 and len(orders)==7
    assert all(o.count==1 for o in orders)
    site=next(iter(parent.satellites));customers=sorted({o.customer for o in orders})
    assert len(customers)==7
    nodes=[site]+customers
    distances={(a,b):parent.distance(a,b) for a in nodes for b in nodes}
    for a,b,c in itertools.product(nodes,repeat=3):
        assert distances[a,c]<=distances[a,b]+distances[b,c]+1e-7
        assert parent.travel(a,c)<=parent.travel(a,b)+parent.travel(b,c)
    best=math.inf;best_route=None;enumerated=0
    for perm in itertools.permutations(customers):
        route=[site]+list(perm)+[site]
        length=sum(distances[a,b] for a,b in zip(route,route[1:]))
        enumerated+=1
        if length<best:best=length;best_route=route
    assert enumerated==math.factorial(7)
    rates=parent.cost_profile.rates
    assert rates['distance_scale_to_km']==1 and rates['container_pool_cost_per_period']==5
    assert all(v>=0 for v in rates.values() if isinstance(v,(int,float)))
    e1_lb=2*(parent.distance(parent.depot,site)+parent.distance(site,parent.depot))
    constant=sum(parent.cost_profile.constant_cost(parent.total).values())
    n5_lb=5*rates['container_pool_cost_per_period']+e1_lb*rates['e1_distance_cost_per_km']+best*rates['e2_distance_cost_per_km']+constant
    assert n5_lb>incumbent+1e-5
    # Batch demand bound gives N>=4; this dominance bound excludes N>=5
    # from any optimum no worse than the audited incumbent. Therefore N=4.
    recycle=parent.total-4
    assert recycle==3
    direct_supply=p.e1_load_time+parent.travel(parent.depot,site)+parent.service(site)+p.e2_dispatch_prep_time
    optimistic_returns={}
    for o in orders:
        td=max(o.delivery_window[0],p.cycle_start+direct_supply+parent.travel(site,o.customer))
        tp=max(o.pickup_window[0],td+o.service_time+o.min_use_time)
        optimistic_returns[o.id]=tp+o.service_time+parent.travel(o.customer,site)+p.e2_return_unload_time
    third=sorted(optimistic_returns.values())[recycle-1]
    assert grid_seconds in (1,60)
    cutoff_lb=grid_seconds*math.ceil(third/grid_seconds)
    clean_after_cutoff=parent.service(site)+parent.travel(site,parent.depot)+p.depot_return_unload_time+p.cleaning_duration
    start2_lb=grid_seconds*math.ceil((cutoff_lb+clean_after_cutoff)/grid_seconds)
    forced_first=[];delivery_from_second={}
    for o in orders:
        earliest=start2_lb+direct_supply+parent.travel(site,o.customer)
        delivery_from_second[o.id]=dict(earliest=earliest,due=o.delivery_window[1])
        if earliest>o.delivery_window[1]:forced_first.append(o.id)
    assert len(forced_first)==4
    forced_second=[o.id for o in orders if o.id not in forced_first]
    assert len(forced_second)==3
    return dict(incumbent_cost=incumbent,pool_at_least=4,pool_at_most_in_optimum=4,
        TSP_permutations=enumerated,TSP_distance_lower_bound=best,TSP_tour=best_route,
        E1_distance_lower_bound=e1_lb,constant_cost=constant,N_ge_5_total_cost_lower_bound=n5_lb,
        metric_triangle_inequality_checked=True,travel_triangle_inequality_checked=True,
        returns_needed_before_second_batch=recycle,optimistic_order_returns=optimistic_returns,
        first_cutoff_lower_bound=cutoff_lb,clean_delay_after_cutoff=clean_after_cutoff,
        second_batch_start_lower_bound=start2_lb,second_batch_earliest_deliveries=delivery_from_second,
        forced_first_batch_orders=forced_first,forced_second_batch_orders=forced_second,
        scope=('Optimality-preserving for S01 K=2 minute calendar, using audited incumbent; excludes only dominated or impossible cases' if grid_seconds==60 else 'Optimality-preserving for S01 K=2 second calendar, using audited incumbent; excludes only dominated or impossible cases'),
        proof_steps=[
            'D_b<=N and D1+D2=7 imply N>=4.',
            'Every productive E1 batch visits the sole satellite and returns, giving two depot-satellite round trips.',
            'Concatenate all E2 tours at the sole satellite and shortcut duplicates; metricity gives a Hamiltonian tour no longer than their sum.',
            'Even relaxed transport plus N>=5 costs more than the audited incumbent; hence an optimum must have N=4.',
            'Issuing 7 boxes from N=4 requires at least 3 first-batch boxes cleaned before the second batch starts.',
            'There is exactly one E1 recovery visit per batch; its clean release is no earlier than cutoff+site service+depot travel+unload+cleaning.',
            'At least 3 customer boxes must reach the satellite by cutoff1, bounding cutoff1 by the third earliest optimistic individual return.',
            'Four early orders cannot meet delivery due dates if assigned to batch2; they fill all four batch1 boxes, forcing remaining three to batch2.'
        ])

def main():
    cfg,fleet=trial.d.load_routing_case(trial.BASE/'cases/S01_m8_k1_4.json',use_pool_config=False)
    parent=trial.d.load_instance(cfg,fleet);seed=trial.read(trial.SEED)
    audit=trial.d.audit_solution(parent,seed);assert audit['valid']
    cert=certificate(parent,audit['objective'])
    assert seed['container_pool_size']==4
    for oid in cert['forced_first_batch_orders']:assert seed['order_batch_assignment'][oid]=='b1'
    for oid in cert['forced_second_batch_orders']:assert seed['order_batch_assignment'][oid]=='b2'
    assert seed['batch_cutoffs']['b1']>=cert['first_cutoff_lower_bound']
    assert seed['batch_boundaries'][1]>=cert['second_batch_start_lower_bound']
    trial.save('reduction_certificate.json',cert)
    print(json.dumps(cert,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
