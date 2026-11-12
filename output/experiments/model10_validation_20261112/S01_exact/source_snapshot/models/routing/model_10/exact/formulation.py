"""Full arc-based M10 MILP with repeated visits and multi-trip shared fleets.

An E1 action is one order's full-box delivery or empty-box collection. Adjacent
same-site/same-kind actions are a single handling operation with a shared start
time (not one service charge per box). All actions are covered exactly once.
Container successor arcs form disjoint reuse chains; their number is the pool.
"""
from .linear import LinearModel, Expr
from models.common.no_batch.bounds import physical_bounds


def build_model(problem, *, deadline=None, verbose=False):
    if problem.model_number != 10:
        raise ValueError('M10 entry requires a no-batch instance')
    p, f = problem.operating_policy, problem.fleet
    H, Q = p.cycle_end-p.cycle_start, problem.total
    m = LinearModel(deadline)
    physical = physical_bounds(problem)
    max_duration = max([p.e1_load_time, p.e2_dispatch_prep_time, p.cleaning_duration,
                        p.e1_turnaround_time, p.e2_turnaround_time, p.depot_return_unload_time,
                        p.e2_return_unload_time]+list(problem._service.values())+[o.service_time for o in problem.orders])
    M = H+max(problem._travel.values())+4*max_duration+1
    nodes = tuple((o.id, k) for o in problem.orders for k in ('d','p'))
    times = {layer:{a:m.var(f'{layer}_time_{a}', 0, H) for a in nodes} for layer in ('e1','e2')}
    issue = {o.id:m.var('issue_'+o.id, 0, H) for o in problem.orders}
    ready = {o.id:m.var('empty_ready_'+o.id, 0, H) for o in problem.orders}
    clean = {o.id:m.var('clean_'+o.id, 0, H) for o in problem.orders}
    routes = dict(e1=[], e2=[])
    coverage = {layer:{a:[] for a in nodes} for layer in routes}
    cost = Expr()

    def route_group(layer, actions, base, vehicles, trip_cap):
        nonlocal cost
        count = min(len(actions), vehicles*trip_cap)
        cap_w = f.e1_weight_cap if layer == 'e1' else f.e2_weight_cap
        cap_v = f.e1_volume_cap if layer == 'e1' else f.e2_volume_cap
        big_w = 2*cap_w+sum(problem.task(a[0], a[1])[1] for a in actions)+1
        big_v = 2*cap_v+len(actions)*f.container_volume+1
        turn = p.e1_turnaround_time if layer == 'e1' else p.e2_turnaround_time
        prep = p.e1_load_time if layer == 'e1' else p.e2_dispatch_prep_time
        unload = p.depot_return_unload_time if layer == 'e1' else p.e2_return_unload_time
        def site(a):
            o = problem.order_map[a[0]]
            return o.satellite if layer == 'e1' else o.customer
        def duration(a):
            return problem.service(site(a)) if layer == 'e1' else problem.order_map[a[0]].service_time
        rank = {a:m.var(f'rank_{layer}_{base}_{a}', 1, len(actions)) for a in actions}
        weight = {a:m.var(f'weight_{layer}_{base}_{a}', 0, cap_w, False) for a in actions}
        volume = {a:m.var(f'volume_{layer}_{base}_{a}', 0, cap_v, False) for a in actions}
        group = []
        for r in range(count):
            m.budget()
            label = f'{layer}_{base}_{r}'
            used = m.var('used_'+label)
            has_delivery = m.var('delivery_'+label)
            start, depart, end = (m.var(key+'_'+label, 0, H) for key in ('load','depart','end'))
            assigned = {a:m.var(f'z_{label}_{a}') for a in actions}
            vehicle = [m.var(f'vehicle_{label}_{v}') for v in range(vehicles)]
            first = {a:m.var(f'first_{label}_{a}') for a in actions}
            last = {a:m.var(f'last_{label}_{a}') for a in actions}
            arcs = {(a,b):m.var(f'arc_{label}_{a}_{b}') for a in actions for b in actions if a != b}
            m.eq(sum(vehicle), used)
            m.eq(sum(first.values()), used); m.eq(sum(last.values()), used)
            m.ge(start, H*(1-used)); m.ge(end, H*(1-used))
            m.eq(depart, start+prep*has_delivery)
            m.le(end, H-turn*used)
            m.le(has_delivery, sum(z for a,z in assigned.items() if a[1]=='d'))
            m.le(sum(assigned.values()), len(actions)*used)
            initial_w = sum(problem.task(a[0], 'd')[1]*z for a,z in assigned.items() if a[1]=='d')
            initial_v = sum(f.container_volume*z for a,z in assigned.items() if a[1]=='d')
            m.le(initial_w, cap_w); m.le(initial_v, cap_v)
            for a,z in assigned.items():
                coverage[layer][a].append(z)
                m.eq(first[a]+sum(arcs[b,a] for b in actions if b != a), z)
                m.eq(last[a]+sum(arcs[a,b] for b in actions if b != a), z)
                t, o = times[layer][a], problem.order_map[a[0]]
                if a[1] == 'd':
                    m.le(z, has_delivery)
                    if layer == 'e1':
                        m.conditional_eq(z, issue[o.id], start, M)
                    else:
                        m.conditional_ge(z, start, times['e1'][a]+problem.service(o.satellite), M)
                elif layer == 'e1':
                    m.conditional_eq(z, clean[o.id], end+p.cleaning_duration, M)
                else:
                    m.conditional_eq(z, ready[o.id], end, M)
                m.conditional_ge(first[a], t, depart+problem.travel(base, site(a)), M)
                m.conditional_ge(last[a], end, t+duration(a)+problem.travel(site(a),base)+unload, M)
                sign = -1 if a[1]=='d' else 1
                w, v = problem.task(a[0], a[1])[1:]
                m.conditional_eq(first[a], weight[a], initial_w+sign*w, big_w)
                m.conditional_eq(first[a], volume[a], initial_v+sign*v, big_v)
                cost += problem.distance_cost[layer]*(problem.distance(base,site(a))*first[a]+
                                                       problem.distance(site(a),base)*last[a])
            for (a,b), x in arcs.items():
                m.conditional_ge(x, rank[b], rank[a]+1, len(actions)+1)
                same_operation = layer == 'e1' and a[1] == b[1] and site(a) == site(b)
                if same_operation:
                    m.conditional_eq(x, times[layer][b], times[layer][a], M)
                else:
                    m.conditional_ge(x, times[layer][b], times[layer][a]+duration(a)+problem.travel(site(a),site(b)), M)
                sign = -1 if b[1]=='d' else 1
                w,v = problem.task(b[0],b[1])[1:]
                m.conditional_eq(x, weight[b], weight[a]+sign*w, big_w)
                m.conditional_eq(x, volume[b], volume[a]+sign*v, big_v)
                cost += problem.distance_cost[layer]*problem.distance(site(a),site(b))*x
            route = dict(label=label, base=base, used=used, start=start, departure=depart, end=end,
                         assignments=assigned, first=first, last=last, arcs=arcs, vehicle=vehicle)
            routes[layer].append(route); group.append(route)
        for before, after in zip(group, group[1:]):
            m.ge(before['used'], after['used'])
            m.le(before['start'], after['start'])
        for v in range(vehicles):
            m.le(sum(r['vehicle'][v] for r in group), trip_cap)
            for i, a in enumerate(group):
                for b in group[i+1:]:
                    m.ge(b['start'], a['end']+turn-M*(2-a['vehicle'][v]-b['vehicle'][v]))
        if group:
            m.eq(group[0]['vehicle'][0],group[0]['used'])

    route_group('e1', nodes, problem.depot, f.e1_count, problem.e1_max_trips)
    for s in problem.satellites:
        actions = tuple(a for a in nodes if problem.order_map[a[0]].satellite == s)
        if actions:
            route_group('e2', actions, s, f.e2_per_sat_count, p.e2_max_trips_per_vehicle)
    for layer in routes:
        for a, zs in coverage[layer].items():
            m.eq(sum(zs), 1)
    for o in problem.orders:
        bounds=physical['orders'][o.id]
        for variable,lo,hi in ((issue[o.id],bounds['issue_lb'],bounds['issue_ub']),
                               (times['e1'][o.id,'d'],bounds['e1_delivery_lb'],bounds['e1_delivery_ub']),
                               (times['e1'][o.id,'p'],bounds['ready_lb'],bounds['e1_pickup_ub'])):
            # Separate inequalities preserve an infeasibility certificate even
            # if an optimistic lower bound already exceeds its upper bound.
            m.ge(variable,lo-p.cycle_start);m.le(variable,hi-p.cycle_start)
        m.ge(clean[o.id],bounds['clean_lb']-p.cycle_start)
        m.ge(ready[o.id],bounds['ready_lb']-p.cycle_start)
        m.ge(times['e2'][o.id,'d'],bounds['delivery_lb']-p.cycle_start)
        m.ge(times['e2'][o.id,'p'],bounds['pickup_lb']-p.cycle_start)
        m.ge(issue[o.id], max(p.cycle_start, o.release_time or p.cycle_start)-p.cycle_start)
        for kind, window in (('d',o.delivery_window),('p',o.pickup_window)):
            m.add(times['e2'][o.id,kind], window[0]-p.cycle_start, window[1]-p.cycle_start)
        m.ge(times['e2'][o.id,'p'], times['e2'][o.id,'d']+o.service_time+o.min_use_time)
        m.ge(times['e1'][o.id,'p'], ready[o.id])
    links = {(a.id,b.id):m.var(f'reuse_{a.id}_{b.id}') for a in problem.orders for b in problem.orders
             if a.id != b.id and physical['orders'][a.id]['clean_lb']<=physical['orders'][b.id]['issue_ub']}
    chain_rank = {o.id:m.var('chain_rank_'+o.id, 1, Q) for o in problem.orders}
    for o in problem.orders:
        m.le(sum(x for (a,b),x in links.items() if a == o.id), 1)
        m.le(sum(x for (a,b),x in links.items() if b == o.id), 1)
    for (a,b),x in links.items():
        m.conditional_ge(x, issue[b], clean[a], M)
        m.conditional_ge(x, chain_rank[b], chain_rank[a]+1, Q+1)
    pool = m.var('container_pool', 0, min(Q, problem.pool_max))
    m.eq(pool, Q-sum(links.values()))
    m.ge(pool,physical['pool_lower_bound'])
    if problem.transport_budget is not None:
        m.le(cost, problem.transport_budget)
    m.objective = cost if problem.objective_mode == 'transport' else pool
    return m, dict(routes=routes, times=times, issue=issue, clean=clean, pool=pool,
                   links=links, transport_cost=cost, problem=problem,physical_bounds=physical)
