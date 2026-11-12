"""Optimistic physical time bounds, valid also for nonmetric road matrices."""


def physical_bounds(problem):
    p=problem.operating_policy
    sites=list(problem.network.nodes)
    dist={(a,b):problem.travel(a,b) for a in sites for b in sites}
    # A shortest-path relaxation can traverse even nodes forbidden to a layer;
    # it remains a lower bound, unlike a direct arc in a nonmetric input matrix.
    for k in sites:
        for a in sites:
            for b in sites:
                dist[a,b]=min(dist[a,b],dist[a,k]+dist[k,b])
    bounds={};problems=[];events=[]
    for o in problem.orders:
        ds,sc,cs,sd=(dist[a,b] for a,b in ((problem.depot,o.satellite),(o.satellite,o.customer),
                                         (o.customer,o.satellite),(o.satellite,problem.depot)))
        release=max(p.cycle_start,o.release_time or p.cycle_start)
        delivery_lb=max(o.delivery_window[0],release+p.e1_load_time+ds+problem.service(o.satellite)+p.e2_dispatch_prep_time+sc)
        pickup_lb=max(o.pickup_window[0],delivery_lb+o.service_time+o.min_use_time)
        issue_ub=o.delivery_window[1]-p.e1_load_time-ds-problem.service(o.satellite)-p.e2_dispatch_prep_time-sc
        ready_lb=pickup_lb+o.service_time+cs+p.e2_return_unload_time
        clean_lb=ready_lb+problem.service(o.satellite)+sd+p.depot_return_unload_time+p.cleaning_duration
        bounds[o.id]=dict(issue_lb=release,issue_ub=issue_ub,delivery_lb=delivery_lb,
                          pickup_lb=pickup_lb,ready_lb=ready_lb,clean_lb=clean_lb,
                          e1_delivery_lb=release+p.e1_load_time+ds,
                          e1_delivery_ub=o.delivery_window[1]-problem.service(o.satellite)-p.e2_dispatch_prep_time-sc,
                          e1_pickup_ub=p.cycle_end-problem.service(o.satellite)-sd-p.depot_return_unload_time-p.cleaning_duration)
        if delivery_lb>o.delivery_window[1] or pickup_lb>o.pickup_window[1] or clean_lb>p.cycle_end:
            problems.append(dict(code='INDIVIDUAL_TIME_INFEASIBLE',order=o.id))
        w,v=problem.task(o.id,'d')[1:]
        f=problem.fleet
        if w>min(f.e1_weight_cap,f.e2_weight_cap)+1e-7 or v>min(f.e1_volume_cap,f.e2_volume_cap)+1e-7:
            problems.append(dict(code='INDIVIDUAL_CAPACITY_INFEASIBLE',order=o.id))
        if issue_ub<clean_lb:
            events.extend(((issue_ub,1),(clean_lb,-1)))
    now=peak=0
    for _,delta in sorted(events):now+=delta;peak=max(peak,now)
    return dict(orders=bounds,pool_lower_bound=max(1,peak),infeasibility=problems)
