"""B=1 joint MILP with implied bounds and homogeneous vehicle symmetry."""
from __future__ import annotations

import time

from .strengthening import route_arcs, single_batch_bounds, trip_slot_limits


def build_model(problem, *, verbose=False, deadline=None, share_e2=True, travel_bounds=None, domain_check=None):
    import gurobipy as gp
    from gurobipy import GRB
    p, f = problem.operating_policy, problem.fleet
    single = len(p.batches) == 1
    if not single:
        raise ValueError('Single-batch formulation requires B=1')
    free = problem.free_assignment
    if free and not single:
        raise ValueError('Joint assignment is supported only for B=1')
    compact = share_e2
    windows, site_windows = {}, {}
    if free:
        from .domains import assignment_domains
        domain_check = domain_check or assignment_domains(problem, travel_bounds)
        if not domain_check['passed']:
            raise ValueError('Joint model requires nonempty necessary assignment domains')
        domains = domain_check['domains']
        site_windows = domain_check['windows']
        for o in problem.orders:
            for kind in problem.task_kinds:
                choices = [site_windows[s, o.id, kind] for s in domains[o.customer]]
                windows[o.id, kind] = (min(w[0] for w in choices), max(w[1] for w in choices))
    if not free:
        from models.routing.common.cycle.precheck import travel_lower_bounds
        travel_bounds = travel_bounds or travel_lower_bounds(problem)
        outward = travel_bounds['outward'][problem.depot]
        for s in problem.satellites:
            travel_out = travel_bounds['outward'][s]
            travel_in = travel_bounds['inward'][s]
            for o in problem.orders:
                if o.satellite != s:
                    continue
                batch = problem.batch_map[o.batch]
                supplied = batch.start+p.e1_load_time+outward[s]+problem.service(s)+p.e2_dispatch_prep_time
                delivered = max(o.delivery_window[0], supplied+travel_out[o.customer])
                windows[o.id, 'd'] = (delivered, min(o.delivery_window[1], batch.cutoff-o.service_time)
                                      if single else o.delivery_window[1])
                windows[o.id, 'p'] = (max(o.pickup_window[0], delivered+o.service_time+o.min_use_time),
                    min(o.pickup_window[1], p.batches[-1].cutoff-o.service_time-travel_in[o.customer]-p.e2_return_unload_time))
    from models.routing.common.cycle.precheck import travel_lower_bounds
    travel_bounds = travel_bounds or travel_lower_bounds(problem)
    tightened = single_batch_bounds(problem, travel_bounds, domains if free else None)
    H, total = p.cycle_end, max(1, problem.total)
    m = gp.Model('multi_batch_cutoff_v1')
    m.Params.OutputFlag = int(verbose)
    m.Params.MIPGap = 0
    m.Params.MIPGapAbs = 0
    m.Params.Threads = 1
    routes1, routes2, objective = [], [], gp.LinExpr()
    assignments, active_sites, counts, weights = {}, {}, {}, {}
    if free:
        assignments = {(c, s): m.addVar(vtype=GRB.BINARY, name=f'assign_{c}_{s}')
                       for c, sites in domains.items() for s in sites}
        for c, sites in domains.items():
            m.addConstr(gp.quicksum(assignments[c, s] for s in sites) == 1)
        for s in problem.satellites:
            active_sites[s] = m.addVar(vtype=GRB.BINARY, name=f'active_{s}')
            members = [z for (c, site), z in assignments.items() if site == s]
            for z in members:
                m.addConstr(z <= active_sites[s])
            m.addConstr(active_sites[s] <= gp.quicksum(members))
            counts[s] = gp.quicksum(o.count*assignments[o.customer, s]
                                    for o in problem.orders if s in domains[o.customer])
            weights[s] = gp.quicksum((o.goods_weight+o.count*f.container_weight)*assignments[o.customer, s]
                                     for o in problem.orders if s in domains[o.customer])
            m.addConstr(weights[s] <= f.e1_weight_cap)
            m.addConstr(counts[s]*f.container_volume <= f.e1_volume_cap)
    deliveries = {(s, b.id): m.addVar(vtype=GRB.INTEGER, ub=H, name=f'delivery_{s}_{b.id}')
                  for (s, bid), n in problem.demands.items() for b in p.batches if bid == b.id and (n or free)}
    for (s, bid), variable in deliveries.items():
        if s not in tightened['supply']:
            variable.UB = 0
            continue
        lower, upper = tightened['supply'][s]
        variable.UB = max(0, upper)
        if free:
            m.addConstr(variable >= lower*active_sites[s])
            m.addConstr(variable <= upper*active_sites[s])
            for oid, due in tightened['dues'][s].items():
                m.addConstr((assignments[problem.order_map[oid].customer, s] == 1) >> (variable <= due))
        else:
            variable.LB = lower
    order_times = {(o.id, k): m.addVar(vtype=GRB.INTEGER, ub=H, name=f'{o.id}_{k}')
                   for o in problem.orders for k in problem.task_kinds}
    # Each task is visited exactly once across all vehicles and trip slots.
    # Its time and post-service load therefore have one well-defined value.
    # Keep route-indexed boundary arcs, but share internal propagation once.
    task_load_w = {key: m.addVar(ub=f.e2_weight_cap) for key in order_times} if compact else {}
    task_load_v = {key: m.addVar(ub=f.e2_volume_cap) for key in order_times} if compact else {}
    if compact:
        for key, (lw, uw, lv, uv) in tightened['loads'].items():
            task_load_w[key].LB, task_load_w[key].UB = lw, uw
            task_load_v[key].LB, task_load_v[key].UB = lv, uv
    shared_arcs = {}
    causal_limit = tightened['causal_limit']
    order_steps = {(o.id, k): m.addVar(ub=causal_limit) for o in problem.orders for k in problem.task_kinds}
    e2_arc_cache = {}

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
        shared = compact and layer == 'e2'
        keys = [(n['order'].id, n['kind']) for n in nodes] if shared else []
        times = ({i: order_times[key] for i, key in enumerate(keys)} if shared else
                 m.addVars(count, vtype=GRB.INTEGER, ub=H, name=label+'_time'))
        # E2 already has strictly increasing global causal steps below.
        rank = m.addVars(count, lb=0, ub=max(1, count), name=label+'_rank') if layer == 'e1' else None
        load_w = ({i: task_load_w[key] for i, key in enumerate(keys)} if shared else
                  m.addVars(count, ub=cap_w, name=label+'_weight'))
        load_v = ({i: task_load_v[key] for i, key in enumerate(keys)} if shared else
                  m.addVars(count, ub=cap_v, name=label+'_volume'))
        # -1 = source, -2 = sink. There is no empty source-to-sink route.
        end_limit = min(H-p.e1_turnaround_time, batch.end) if layer == 'e1' else H-p.e2_turnaround_time
        if layer == 'e2' and base in e2_arc_cache:
            arcs = e2_arc_cache[base]
        else:
            arcs = route_arcs(problem, nodes, base, layer, batch, check_budget)
            if layer == 'e2':
                e2_arc_cache[base] = arcs
        x = m.addVars(arcs, vtype=GRB.BINARY, name=label+'_arc')
        incoming, outgoing = {i: [] for i in range(count)}, {i: [] for i in range(count)}
        for a, b in arcs:
            if b >= 0:
                incoming[b].append(x[a, b])
            if a >= 0:
                outgoing[a].append(x[a, b])
        m.addConstr(start <= H*used)
        m.addConstr(end <= H*used)
        end.UB = max(0, end_limit)
        m.addConstr((used == 1) >> (start >= p.cycle_start))
        m.addConstr(gp.quicksum(x[a, b] for a, b in arcs if a == -1) == used)
        m.addConstr(gp.quicksum(x[a, b] for a, b in arcs if b == -2) == used)
        initial_w = gp.quicksum(n['w'] if free and layer == 'e1' else n['w']*visit[i]
                                for i, n in enumerate(nodes) if n['kind'] == 'd')
        initial_v = gp.quicksum(n['v'] if free and layer == 'e1' else n['v']*visit[i]
                                for i, n in enumerate(nodes) if n['kind'] == 'd')
        m.addConstr(initial_w <= cap_w)
        m.addConstr(initial_v <= cap_v)
        for i, n in enumerate(nodes):
            if i % 64 == 0:
                check_budget()
            m.addConstr(gp.quicksum(incoming[i]) == visit[i])
            m.addConstr(gp.quicksum(outgoing[i]) == visit[i])
            m.addConstr(visit[i] <= used)
            if free and layer == 'e1':
                # Quantity and weight belong to the unique vehicle visiting
                # this satellite; linearize assignment totals without products.
                m.addConstr(visit[i] <= active_sites[n['site']])
                m.addConstr(n['quantity'] <= total*visit[i])
                m.addConstr((visit[i] == 1) >> (n['quantity'] == counts[n['site']]))
                if n['kind'] == 'd':
                    m.addConstr(n['w'] <= f.e1_weight_cap*visit[i])
                    m.addConstr((visit[i] == 1) >> (n['w'] == weights[n['site']]))
            if not shared:
                m.addConstr(times[i] <= H*visit[i])
            if n['kind'] == 'p' and layer == 'e1' and not single:
                m.addConstr(n['quantity'] <= total*visit[i])
                m.addConstr(n['quantity'] >= visit[i])
            if layer == 'e1':
                times[i].UB = max(0, n['window'][1])
                m.addConstr(times[i] >= n['window'][0]*visit[i])
                m.addConstr(times[i] <= n['window'][1]*visit[i])
            elif not shared or free:
                m.addConstr((visit[i] == 1) >> (times[i] >= n['window'][0]))
                m.addConstr((visit[i] == 1) >> (times[i] <= n['window'][1]))
        for arc_index, (a, b) in enumerate(arcs):
            if arc_index % 64 == 0:
                check_budget()
            left = base if a == -1 else nodes[a]['site']
            right = base if b == -2 else nodes[b]['site']
            objective += problem.distance(left, right)*x[a, b]
            if a == -1:
                m.addConstr((x[a, b] == 1) >> (times[b] >= start+problem.travel(left, right)))
                if rank is not None:
                    m.addConstr((x[a, b] == 1) >> (rank[b] == 1))
                sign = -1 if nodes[b]['kind'] == 'd' else 1
                m.addConstr((x[a, b] == 1) >> (load_w[b] == initial_w+sign*nodes[b]['w']))
                m.addConstr((x[a, b] == 1) >> (load_v[b] == initial_v+sign*nodes[b]['v']))
            elif b == -2:
                m.addConstr((x[a, b] == 1) >> (end >= times[a]+nodes[a]['service']+problem.travel(left, right)+unload))
            else:
                if shared:
                    shared_arcs.setdefault((keys[a], keys[b]), []).append(x[a, b])
                    continue
                m.addConstr((x[a, b] == 1) >> (times[b] >= times[a]+nodes[a]['service']+problem.travel(left, right)))
                if rank is not None:
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
                if free and s not in tightened['supply']:
                    continue
                delivery_window = tightened['visits'][s, 'd'] if n or free else (b.start, b.cutoff-problem.service(s))
                if free:
                    q = m.addVar(vtype=GRB.INTEGER, ub=tightened['quantities'][s])
                    w = m.addVar(ub=f.e1_weight_cap)
                    nodes.append(dict(site=s, kind='d', quantity=q, w=w, v=q*f.container_volume,
                                      service=problem.service(s), window=delivery_window))
                elif n:
                    nodes.append(dict(site=s, kind='d', quantity=n, w=problem.weights[s, b.id],
                                      v=n*f.container_volume, service=problem.service(s),
                                      window=delivery_window))
                if problem.reusable and (not single or n or free):
                    # B=1: all orders must be unloaded by its sole cutoff, so
                    # every satellite's pickup quantity is known in advance.
                    q = n if not free else m.addVar(vtype=GRB.INTEGER,
                        ub=tightened['quantities'][s], name=f'q_{b.id}_{k}_{s}')
                    nodes.append(dict(site=s, kind='p', quantity=q, w=q*f.container_weight,
                                      v=q*f.container_volume, service=problem.service(s),
                                      window=tightened['visits'][s, 'p']))
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
            r['quantity'] = gp.quicksum(n['quantity']*r['visit'][i] if single and not free else n['quantity']
                                       for i, n in enumerate(nodes) if n['kind'] == 'p')
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
            if single and k:
                m.addConstr(r['used'] <= routes1[-2]['used'])
    for (s, bid), count in problem.demands.items():
        if count or free:
            m.addConstr(gp.quicksum(r['visit'][i] for r in routes1 if r['batch'].id == bid
                                   for i, n in enumerate(r['nodes']) if n['site'] == s and n['kind'] == 'd')
                        == (active_sites[s] if free else 1))

    q_expected = {(s, b.id): gp.LinExpr() for s in problem.satellites for b in p.batches}
    for s in problem.satellites:
        orders = [o for o in problem.orders if (s in domains[o.customer] if free else o.satellite == s)]
        if not orders:
            continue
        first_slots = []
        vehicle_trips = []
        for v, slot_limit in enumerate(trip_slot_limits(problem, len(orders)*len(problem.task_kinds))):
            previous = None
            used_slots = []
            for slot in range(slot_limit):
                nodes = []
                for o in orders:
                    for kind in problem.task_kinds:
                        _, w, volume = problem.task(o.id, kind)
                        nodes.append(dict(site=o.customer, order=o, kind=kind, quantity=o.count,
                                          w=w, v=volume, service=o.service_time,
                                          window=site_windows[s, o.id, kind] if free else windows[o.id, kind]))
                r = route(nodes, s, f.e2_weight_cap, f.e2_volume_cap,
                          p.e2_return_unload_time, f'e2_{s}_{v}_{slot}', 'e2')
                r.update(satellite=s, vehicle=v, slot=slot)
                used_slots.append(r['used'])
                if slot == 0:
                    if first_slots:
                        m.addConstr(r['used'] <= first_slots[-1])
                    first_slots.append(r['used'])
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
                    if not compact:
                        m.addConstr((r['visit'][i] == 1) >> (order_times[o.id, n['kind']] == r['times'][i]))
                    if n['kind'] == 'd':
                        m.addConstr(r['visit'][i] <= selects[o.batch])
                        m.addConstr((r['visit'][i] == 1) >> (r['start'] >= deliveries[s, o.batch]+p.e2_dispatch_prep_time))
                        if len(p.batches) == 1:
                            m.addConstr((r['visit'][i] == 1) >> (r['times'][i]+o.service_time <= p.batches[0].cutoff))
                    elif single:
                        m.addConstr((r['visit'][i] == 1) >> (r['end'] <= p.batches[0].cutoff))
                for (i, j), arc in r['x'].items():
                    if compact and i >= 0 and j >= 0:
                        continue
                    left = r['step_start'] if i == -1 else order_steps[nodes[i]['order'].id, nodes[i]['kind']]
                    right = r['step_end'] if j == -2 else order_steps[nodes[j]['order'].id, nodes[j]['kind']]
                    m.addConstr((arc == 1) >> (right >= left+1))
                prior_h = 0
                gs = []
                for b in (() if single else p.batches):
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
                    if single:
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
            if vehicle_trips:
                m.addConstr(gp.quicksum(used_slots) <= gp.quicksum(vehicle_trips[-1]))
            vehicle_trips.append(used_slots)
    if compact:
        for key, variable in order_times.items():
            variable.LB, variable.UB = windows[key]
        for (left, right), indexed in shared_arcs.items():
            check_budget()
            active = m.addVar(vtype=GRB.BINARY)
            m.addConstr(active == gp.quicksum(indexed))
            a, b = problem.order_map[left[0]], problem.order_map[right[0]]
            m.addConstr((active == 1) >> (order_times[right] >= order_times[left]
                        +a.service_time+problem.travel(a.customer, b.customer)))
            _, weight, volume = problem.task(*right)
            sign = -1 if right[1] == 'd' else 1
            m.addConstr((active == 1) >> (task_load_w[right] == task_load_w[left]+sign*weight))
            m.addConstr((active == 1) >> (task_load_v[right] == task_load_v[left]+sign*volume))
            m.addConstr((active == 1) >> (order_steps[right] >= order_steps[left]+1))
    for o in problem.orders:
        for kind in problem.task_kinds:
            for site in (domains[o.customer] if free else [o.satellite]):
                m.addConstr(gp.quicksum(r['visit'][i] for r in routes2 if r['satellite'] == site
                                       for i, n in enumerate(r['nodes']) if n['order'].id == o.id and n['kind'] == kind)
                            == (assignments[o.customer, site] if free else 1))
        if problem.reusable:
            m.addConstr(order_times[o.id, 'p'] >= order_times[o.id, 'd']+o.service_time+o.min_use_time)
            m.addConstr(order_steps[o.id, 'p'] >= order_steps[o.id, 'd']+1)
    for s in problem.satellites:
        for b in p.batches:
            check_budget()
            nodes = [(r, i, n) for r in routes1 if r['batch'].id == b.id
                     for i, n in enumerate(r['nodes']) if n['site'] == s and n['kind'] == 'p']
            if single:
                m.addConstr(gp.quicksum(r['visit'][i] for r, i, n in nodes)
                            == (active_sites[s] if free and problem.reusable else
                                1 if problem.reusable and problem.demands[s, b.id] else 0))
            else:
                m.addConstr(gp.quicksum(n['quantity'] for r, i, n in nodes) == q_expected[s, b.id])
            m.addConstr(gp.quicksum(r['visit'][i] for r, i, n in nodes) <= 1)

    cumulative = 0
    for b in p.batches if problem.reusable else ():
        cumulative += problem.batch_demands[b.id]
        if single:
            m.addConstr(gp.LinExpr(p.container_pool_size) >= cumulative, f'clean_stock_{b.id}')
            continue
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
    return m, dict(e1=routes1, e2=routes2, shared_e2=compact, shared_e2_arcs=len(shared_arcs),
                   assignments=assignments, trip_count_symmetry=True, single_batch_bounds=True)
