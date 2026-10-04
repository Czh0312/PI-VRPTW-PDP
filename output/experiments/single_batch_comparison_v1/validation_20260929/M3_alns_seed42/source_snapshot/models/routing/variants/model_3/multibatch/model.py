"""Joint vehicle-indexed MILP for the declared integer-time, finite-slot model."""
from __future__ import annotations

import time


def build_model(problem, *, verbose=False, deadline=None):
    import gurobipy as gp
    from gurobipy import GRB
    p, f = problem.operating_policy, problem.fleet
    H, total = p.cycle_end, max(1, problem.total)
    m = gp.Model('multi_batch_cutoff_v1')
    m.Params.OutputFlag = int(verbose)
    m.Params.MIPGap = 0
    m.Params.MIPGapAbs = 0
    m.Params.Threads = 1
    routes1, routes2, objective = [], [], gp.LinExpr()
    deliveries = {(s, b.id): m.addVar(vtype=GRB.INTEGER, ub=H, name=f'delivery_{s}_{b.id}')
                  for (s, bid), n in problem.demands.items() for b in p.batches if bid == b.id and n}
    order_times = {(o.id, k): m.addVar(vtype=GRB.INTEGER, ub=H, name=f'{o.id}_{k}')
                   for o in problem.orders for k in problem.task_kinds}
    causal_limit = max(10, 4*len(problem.orders)+4*len(problem.satellites)*f.e2_per_sat_count*p.e2_max_trips_per_vehicle)
    order_steps = {(o.id, k): m.addVar(ub=causal_limit) for o in problem.orders for k in problem.task_kinds}

    def check_budget():
        if deadline is not None and time.perf_counter() >= deadline:
            m.dispose()
            raise TimeoutError('MILP construction reached the total time budget')

    def route(nodes, base, cap_w, cap_v, unload, label, layer, batch=None):
        nonlocal objective
        check_budget()
        count = len(nodes)
        used = m.addVar(vtype=GRB.BINARY, name=label+'_used')
        start = m.addVar(vtype=GRB.INTEGER, ub=H, name=label+'_start')
        end = m.addVar(vtype=GRB.INTEGER, ub=H, name=label+'_end')
        visit = m.addVars(count, vtype=GRB.BINARY, name=label+'_visit')
        times = m.addVars(count, vtype=GRB.INTEGER, ub=H, name=label+'_time')
        rank = m.addVars(count, lb=0, ub=max(1, count), name=label+'_rank')
        load_w = m.addVars(count, ub=cap_w, name=label+'_weight')
        load_v = m.addVars(count, ub=cap_v, name=label+'_volume')
        # -1 = source, -2 = sink. There is no empty source-to-sink route.
        arcs = [(-1, j) for j in range(count)] + [(i, -2) for i in range(count)]
        arcs += [(i, j) for i in range(count) for j in range(count) if i != j
                 and not (layer == 'e1' and nodes[i]['kind'] == 'p' and nodes[j]['kind'] == 'd')]
        x = m.addVars(arcs, vtype=GRB.BINARY, name=label+'_arc')
        m.addConstr(start <= H*used)
        m.addConstr(end <= H*used)
        m.addConstr((used == 1) >> (start >= p.cycle_start))
        m.addConstr(gp.quicksum(x[-1, j] for j in range(count)) == used)
        m.addConstr(gp.quicksum(x[i, -2] for i in range(count)) == used)
        initial_w = gp.quicksum(n['w']*visit[i] for i, n in enumerate(nodes) if n['kind'] == 'd')
        initial_v = gp.quicksum(n['v']*visit[i] for i, n in enumerate(nodes) if n['kind'] == 'd')
        m.addConstr(initial_w <= cap_w)
        m.addConstr(initial_v <= cap_v)
        for i, n in enumerate(nodes):
            m.addConstr(gp.quicksum(x[a, i] for a, b in arcs if b == i) == visit[i])
            m.addConstr(gp.quicksum(x[i, b] for a, b in arcs if a == i) == visit[i])
            m.addConstr(visit[i] <= used)
            m.addConstr(times[i] <= H*visit[i])
            if n['kind'] == 'p' and layer == 'e1':
                m.addConstr(n['quantity'] <= total*visit[i])
                m.addConstr(n['quantity'] >= visit[i])
            m.addConstr((visit[i] == 1) >> (times[i] >= n['window'][0]))
            m.addConstr((visit[i] == 1) >> (times[i] <= n['window'][1]))
        for a, b in arcs:
            left = base if a == -1 else nodes[a]['site']
            right = base if b == -2 else nodes[b]['site']
            objective += problem.distance(left, right)*x[a, b]
            if a == -1:
                m.addConstr((x[a, b] == 1) >> (times[b] >= start+problem.travel(left, right)))
                m.addConstr((x[a, b] == 1) >> (rank[b] == 1))
                sign = -1 if nodes[b]['kind'] == 'd' else 1
                m.addConstr((x[a, b] == 1) >> (load_w[b] == initial_w+sign*nodes[b]['w']))
                m.addConstr((x[a, b] == 1) >> (load_v[b] == initial_v+sign*nodes[b]['v']))
            elif b == -2:
                m.addConstr((x[a, b] == 1) >> (end >= times[a]+nodes[a]['service']+problem.travel(left, right)+unload))
            else:
                m.addConstr((x[a, b] == 1) >> (times[b] >= times[a]+nodes[a]['service']+problem.travel(left, right)))
                m.addConstr((x[a, b] == 1) >> (rank[b] >= rank[a]+1))
                sign = -1 if nodes[b]['kind'] == 'd' else 1
                m.addConstr((x[a, b] == 1) >> (load_w[b] == load_w[a]+sign*nodes[b]['w']))
                m.addConstr((x[a, b] == 1) >> (load_v[b] == load_v[a]+sign*nodes[b]['v']))
            if layer == 'e1' and b != -2 and nodes[b]['kind'] == 'p' and (a == -1 or nodes[a]['kind'] == 'd'):
                m.addConstr((x[a, b] == 1) >> (times[b] >= batch.cutoff+problem.travel(left, right)))
        return dict(nodes=nodes, base=base, used=used, start=start, end=end,
                    visit=visit, times=times, x=x, label=label)

    for b in p.batches:
        for k in range(f.e1_count):
            nodes = []
            for s in problem.satellites:
                n = problem.demands[s, b.id]
                if n:
                    nodes.append(dict(site=s, kind='d', quantity=n, w=problem.weights[s, b.id],
                                      v=n*f.container_volume, service=problem.service(s),
                                      window=(b.start, b.cutoff-problem.service(s))))
                if problem.reusable:
                    q = m.addVar(vtype=GRB.INTEGER, ub=total, name=f'q_{b.id}_{k}_{s}')
                    nodes.append(dict(site=s, kind='p', quantity=q, w=q*f.container_weight,
                                      v=q*f.container_volume, service=problem.service(s),
                                      window=(b.cutoff, b.end)))
            r = route(nodes, problem.depot, f.e1_weight_cap, f.e1_volume_cap,
                      p.depot_return_unload_time, f'e1_{b.id}_{k}', 'e1', b)
            r.update(batch=b, vehicle=k)
            r['has_delivery'] = m.addVar(vtype=GRB.BINARY)
            dvis = [r['visit'][i] for i, n in enumerate(nodes) if n['kind'] == 'd']
            for v in dvis:
                m.addConstr(r['has_delivery'] >= v)
            m.addConstr(r['has_delivery'] <= gp.quicksum(dvis))
            only_pick = m.addVar(vtype=GRB.BINARY)
            m.addConstr(only_pick == r['used']-r['has_delivery'])
            m.addConstr((r['has_delivery'] == 1) >> (r['start'] == b.start+p.e1_load_time))
            m.addConstr((only_pick == 1) >> (r['start'] >= b.cutoff))
            m.addConstr((r['used'] == 1) >> (r['end'] <= b.end))
            m.addConstr((r['used'] == 1) >> (r['end']+p.e1_turnaround_time <= H))
            r['quantity'] = gp.quicksum(n['quantity'] for n in nodes if n['kind'] == 'p')
            for i, n in enumerate(nodes):
                if n['kind'] == 'd':
                    m.addConstr((r['visit'][i] == 1) >> (deliveries[n['site'], b.id] == r['times'][i]+n['service']))
                else:
                    m.addConstr((r['visit'][i] == 1) >> (r['end']+p.cleaning_duration <= H))
            for previous in routes1:
                if previous['vehicle'] == k:
                    both = m.addVar(vtype=GRB.BINARY)
                    m.addConstr(both <= previous['used'])
                    m.addConstr(both <= r['used'])
                    m.addConstr(both >= previous['used']+r['used']-1)
                    # Loading is occupied time; pickup-only trips have no loading.
                    m.addConstr((both == 1) >> (r['start']-p.e1_load_time*r['has_delivery'] >= previous['end']+p.e1_turnaround_time))
            routes1.append(r)
    for (s, bid), count in problem.demands.items():
        if count:
            m.addConstr(gp.quicksum(r['visit'][i] for r in routes1 if r['batch'].id == bid
                                   for i, n in enumerate(r['nodes']) if n['site'] == s and n['kind'] == 'd') == 1)

    q_expected = {(s, b.id): gp.LinExpr() for s in problem.satellites for b in p.batches}
    for s in problem.satellites:
        orders = [o for o in problem.orders if o.satellite == s]
        for v in range(f.e2_per_sat_count):
            previous = None
            for slot in range(p.e2_max_trips_per_vehicle):
                nodes = []
                for o in orders:
                    for kind in problem.task_kinds:
                        _, w, volume = problem.task(o.id, kind)
                        nodes.append(dict(site=o.customer, order=o, kind=kind, quantity=o.count,
                                          w=w, v=volume, service=o.service_time,
                                          window=o.delivery_window if kind == 'd' else o.pickup_window))
                r = route(nodes, s, f.e2_weight_cap, f.e2_volume_cap,
                          p.e2_return_unload_time, f'e2_{s}_{v}_{slot}', 'e2')
                r.update(satellite=s, vehicle=v, slot=slot)
                r['step_start'] = m.addVar(ub=causal_limit)
                r['step_end'] = m.addVar(ub=causal_limit)
                m.addConstr((r['used'] == 1) >> (r['end']+p.e2_turnaround_time <= H))
                previous_route = previous
                if previous_route is not None:
                    m.addConstr(r['used'] <= previous['used'])
                    m.addConstr((r['used'] == 1) >> (r['start'] >= previous['end']+p.e2_turnaround_time))
                    m.addConstr((r['used'] == 1) >> (r['step_start'] >= previous['step_end']+1))
                previous = r
                batches = {o.batch for o in orders}
                selects = {b: m.addVar(vtype=GRB.BINARY) for b in batches}
                m.addConstr(gp.quicksum(selects.values()) <= 1)
                # Dispatch preparation for a loaded trip occupies its vehicle.
                # It starts after both inbound goods and the previous trip are ready.
                if previous_route is not None:
                    m.addConstr((r['used'] == 1) >> (
                        r['start'] >= previous_route['end']+p.e2_turnaround_time
                        +p.e2_dispatch_prep_time*gp.quicksum(selects.values())))
                for b in batches:
                    dvis = [r['visit'][i] for i, n in enumerate(nodes) if n['kind'] == 'd' and n['order'].batch == b]
                    m.addConstr(selects[b] <= gp.quicksum(dvis))
                for i, n in enumerate(nodes):
                    o = n['order']
                    m.addConstr((r['visit'][i] == 1) >> (order_times[o.id, n['kind']] == r['times'][i]))
                    if n['kind'] == 'd':
                        m.addConstr(r['visit'][i] <= selects[o.batch])
                        m.addConstr((r['visit'][i] == 1) >> (r['start'] >= deliveries[s, o.batch]+p.e2_dispatch_prep_time))
                        if len(p.batches) == 1:
                            m.addConstr((r['visit'][i] == 1) >> (r['times'][i]+o.service_time <= p.batches[0].cutoff))
                for (i, j), arc in r['x'].items():
                    left = r['step_start'] if i == -1 else order_steps[nodes[i]['order'].id, nodes[i]['kind']]
                    right = r['step_end'] if j == -2 else order_steps[nodes[j]['order'].id, nodes[j]['kind']]
                    m.addConstr((arc == 1) >> (right >= left+1))
                prior_h = 0
                gs = []
                for b in p.batches:
                    h, late, g = (m.addVar(vtype=GRB.BINARY) for _ in range(3))
                    m.addConstr(late == r['used']-h)
                    m.addConstr((h == 1) >> (r['end'] <= b.cutoff))
                    m.addConstr((late == 1) >> (r['end'] >= b.cutoff+1))
                    m.addConstr(g == h-prior_h)
                    prior_h = h
                    gs.append((b, g))
                for i, n in enumerate(nodes):
                    if n['kind'] != 'p':
                        continue
                    allocated = []
                    for b, g in gs:
                        w = m.addVar(vtype=GRB.BINARY)
                        m.addConstr(w <= r['visit'][i])
                        m.addConstr(w <= g)
                        m.addConstr(w >= r['visit'][i]+g-1)
                        q_expected[s, b.id] += n['quantity']*w
                        allocated.append(w)
                    m.addConstr(gp.quicksum(allocated) == r['visit'][i])
                routes2.append(r)
    for o in problem.orders:
        for kind in problem.task_kinds:
            m.addConstr(gp.quicksum(r['visit'][i] for r in routes2 if r['satellite'] == o.satellite
                                   for i, n in enumerate(r['nodes']) if n['order'].id == o.id and n['kind'] == kind) == 1)
        if problem.reusable:
            m.addConstr(order_times[o.id, 'p'] >= order_times[o.id, 'd']+o.service_time+o.min_use_time)
            m.addConstr(order_steps[o.id, 'p'] >= order_steps[o.id, 'd']+1)
    for s in problem.satellites:
        for b in p.batches:
            nodes = [(r, i, n) for r in routes1 if r['batch'].id == b.id
                     for i, n in enumerate(r['nodes']) if n['site'] == s and n['kind'] == 'p']
            m.addConstr(gp.quicksum(n['quantity'] for r, i, n in nodes) == q_expected[s, b.id])
            m.addConstr(gp.quicksum(r['visit'][i] for r, i, n in nodes) <= 1)

    cumulative = 0
    for b in p.batches if problem.reusable else ():
        cumulative += problem.batch_demands[b.id]
        released = []
        for r in routes1:
            ready, late = (m.addVar(vtype=GRB.BINARY) for _ in range(2))
            m.addConstr(late == r['used']-ready)
            m.addConstr((ready == 1) >> (r['end']+p.cleaning_duration <= b.start))
            m.addConstr((late == 1) >> (r['end']+p.cleaning_duration >= b.start+1))
            q = m.addVar(lb=0, ub=total, vtype=GRB.INTEGER)
            m.addConstr(q <= r['quantity'])
            m.addConstr(q <= total*ready)
            m.addConstr(q >= r['quantity']-total*(1-ready))
            released.append(q)
        m.addConstr(p.container_pool_size+gp.quicksum(released) >= cumulative, f'clean_stock_{b.id}')
    m.setObjective(objective, GRB.MINIMIZE)
    m.update()
    return m, dict(e1=routes1, e2=routes2)
