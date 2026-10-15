"""Enumerate a relaxed last-round E1 cover to explain terminal-cleaning conflicts.

No MILP or search result is used. This is a lower bound, not a routing solver:
ignore capacity, delivery routing, competition and non-mandatory returns. Every
vehicle may start its recovery from the depot or its unique last delivery site.
If even this relaxed cover finishes too late, the fixed calendar is infeasible.
"""
from itertools import permutations
from models.common.cycle.precheck import check_necessary_conditions, travel_lower_bounds


def last_recovery_certificate(problem):
    p=problem.operating_policy
    if problem.free_assignment or len(p.batches)<2 or len(problem.satellites)>8:
        raise ValueError('This diagnostic requires fixed assignment, multiple batches and at most eight sites')
    bounds=travel_lower_bounds(problem)
    check=check_necessary_conditions(problem,bounds)
    required=sorted({problem.order_map[r['order_id']].satellite for r in check['order_bounds']
                     if r['earliest_unload']>p.batches[-2].cutoff})
    deliveries=[s for s in problem.satellites if problem.demands[s,p.batches[-1].id]>0]
    if len(deliveries)>problem.fleet.e1_count:
        raise ValueError('Not enough slots for this deliberately relaxed anchor construction')
    anchors=[[s,problem.depot] for s in deliveries]+[[problem.depot]]*(problem.fleet.e1_count-len(deliveries))
    full=(1<<len(required))-1
    costs=[]
    for choices in anchors:
        row={0:0}
        for mask in range(1,full+1):
            subset=[s for i,s in enumerate(required) if mask&(1<<i)]
            row[mask]=min(bounds['outward'][anchor][route[0]]
                +sum(problem.service(s) for s in route)
                +sum(bounds['outward'][a][b] for a,b in zip(route,route[1:]))
                +bounds['outward'][route[-1]][problem.depot]
                +p.depot_return_unload_time+p.cleaning_duration
                for anchor in choices for route in permutations(subset))
        costs.append(row)
    dp={0:0}
    for row in costs:
        nxt={}
        for covered,value in dp.items():
            for subset,elapsed in row.items():
                if covered&subset:continue
                combined=covered|subset
                nxt[combined]=min(nxt.get(combined,float('inf')),max(value,elapsed))
        dp=nxt
    minimum=dp[full]
    available=p.cycle_end-p.batches[-1].cutoff
    return dict(proof='optimistic_last_recovery_cover_v1',forced_last_return_sites=required,
        last_batch_delivery_sites=deliveries,anchor_options=anchors,
        return_and_clean_lower_bound_ticks=minimum,available_ticks=available,
        lower_bound_minutes=p.minutes(minimum),available_minutes=p.minutes(available),
        infeasible=minimum>available,
        scope='A failed relaxed cover proves fixed-calendar infeasibility; passing does not prove full feasibility')
