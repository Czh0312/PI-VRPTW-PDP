"""Joint vehicle-indexed MILP for the declared integer-time, finite-slot model."""
from __future__ import annotations

import time

# The route-identity LP can be weaker on small models. Use the same structural
# crossover as the single-batch implementation, rather than a customer limit.
M5_COMPRESS_ROUTE_MIN_INTERNAL_ARCS = 10000


def build_model(problem, *, verbose=False, deadline=None, share_e2=True, travel_bounds=None, domain_check=None,
                strengthen_multibatch=True, share_cutoff=True, e2_capacity_cuts=True,
                linear_e2_links=True, trip_symmetry=True, compress_multibatch_routes=None,
                factor_e2_loads=False, distance_cuts=None, _distance_certificate=None,
                single_batch_cycle_cuts=False):
    if single_batch_cycle_cuts and len(problem.operating_policy.batches) != 1:
        raise ValueError('Short-cycle cuts require B=1')
    if len(problem.operating_policy.batches) == 1:
        from .single_batch_formulation import build_model as build_single
        cycle_options = dict(short_cycle_cuts=True) if single_batch_cycle_cuts else {}
        return build_single(problem, verbose=verbose, deadline=deadline, share_e2=share_e2,
                            travel_bounds=travel_bounds, domain_check=domain_check, **cycle_options)
    import gurobipy as gp
    from gurobipy import GRB
    if distance_cuts not in (None, 'none', 'total', 'components'):
        raise ValueError('distance_cuts must be none, total or components')
    distance_cut_mode = 'none' if distance_cuts is None else distance_cuts
    p, f = problem.operating_policy, problem.fleet
    single = len(p.batches) == 1
    free = problem.free_assignment
    if free and not single:
        raise ValueError('Joint assignment is supported only for B=1')
    compact = share_e2
    strengthen = not single and strengthen_multibatch
    compact_cutoff = not single and share_cutoff
    linear_links = compact and linear_e2_links
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
    H, total = p.cycle_end, max(1, problem.total)
    m = gp.Model('multi_batch_cutoff_v1')
    m.Params.OutputFlag = int(verbose)
    m.Params.MIPGap = 0
    m.Params.MIPGapAbs = 0
    m.Params.Threads = 1
    routes1, routes2, objective = [], [], gp.LinExpr()
    component_costs = {}
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
    if linear_links:
        for (s, bid), variable in deliveries.items():
            batch = problem.batch_map[bid]
            variable.LB = batch.start+p.e1_load_time+outward[s]+problem.service(s)
            variable.UB = batch.cutoff
    order_times = {(o.id, k): m.addVar(vtype=GRB.INTEGER, ub=H, name=f'{o.id}_{k}')
                   for o in problem.orders for k in problem.task_kinds}
    # Each task is visited exactly once across all vehicles and trip slots.
    # Its time and post-service load therefore have one well-defined value.
    # Keep route-indexed boundary arcs, but share internal propagation once.
    task_load_w = {key: m.addVar(ub=f.e2_weight_cap) for key in order_times} if compact else {}
    task_load_v = {key: m.addVar(ub=f.e2_volume_cap) for key in order_times} if compact else {}
    if strengthen and compact:
        for key in order_times:
            _, weight, volume = problem.task(*key)
            if key[1] == 'd':
                task_load_w[key].UB = max(0, f.e2_weight_cap-weight)
                task_load_v[key].UB = max(0, f.e2_volume_cap-volume)
            else:
                task_load_w[key].LB = weight
                task_load_v[key].LB = volume
    shared_arcs, aggregate_arcs, global_arcs, e2_arc_cache = {}, {}, {}, {}
    indexed_internal = 0
    causal_limit = max(10, 4*len(problem.orders)+4*len(problem.satellites)*f.e2_per_sat_count*p.e2_max_trips_per_vehicle)
    if linear_links:
        # At most one trip per task. A satellite's causal DAG has its task
        # nodes plus two endpoints per active trip, hence at most 3*T nodes.
        causal_limit = max(1, 3*len(order_times)-1)
    order_steps = {(o.id, k): m.addVar(ub=causal_limit) for o in problem.orders for k in problem.task_kinds}

    def check_budget():
        if deadline is not None and time.perf_counter() >= deadline:
            m.dispose()
            raise TimeoutError('MILP construction reached the total time budget')

    def route_arcs(nodes, base, cap_w, cap_v, unload, layer, batch):
        if layer == 'e2' and base in e2_arc_cache:
            return e2_arc_cache[base]
        count = len(nodes)
        # -1 = source, -2 = sink. There is no empty source-to-sink route.
        end_limit = min(H-p.e1_turnaround_time, batch.end) if layer == 'e1' else H-p.e2_turnaround_time
        arcs = [(-1, j) for j in range(count)
                if p.cycle_start+problem.travel(base, nodes[j]['site']) <= nodes[j]['window'][1]]
        arcs += [(i, -2) for i in range(count)
                 if nodes[i]['window'][0]+nodes[i]['service']+problem.travel(nodes[i]['site'], base)+unload <= end_limit]
        for i, left in enumerate(nodes):
            check_budget()
            for j, right in enumerate(nodes):
                if i == j or (layer == 'e1' and left['kind'] == 'p' and right['kind'] == 'd'):
                    continue
                if (layer == 'e2' and left['kind'] == 'p' and right['kind'] == 'd'
                        and left['order'].id == right['order'].id):
                    continue
                if left['window'][0]+left['service']+problem.travel(left['site'], right['site']) > right['window'][1]:
                    continue
                if strengthen and layer == 'e2':
                    if left['kind'] == right['kind'] == 'd' and left['order'].batch != right['order'].batch:
                        continue
                    # Two deliveries are loaded together; two pickups must
                    # return together. A pickup before a delivery overlaps its
                    # load. Delivery-then-pickup does NOT require their sum.
                    overlap = left['kind'] == right['kind'] or left['kind'] == 'p'
                    if overlap and (left['w']+right['w'] > cap_w+1e-6 or left['v']+right['v'] > cap_v+1e-6):
                        continue
                arcs.append((i, j))
        if layer == 'e2':
            e2_arc_cache[base] = arcs
        return arcs

    e2_nodes, internal_estimate = {}, 0
    for site in problem.satellites:
        check_budget()
        nodes = []
        for order in problem.orders:
            if order.satellite != site:
                continue
            for kind in problem.task_kinds:
                _, weight, volume = problem.task(order.id, kind)
                nodes.append(dict(site=order.customer, order=order, kind=kind, quantity=order.count,
                                  w=weight, v=volume, service=order.service_time,
                                  window=windows[order.id, kind]))
        e2_nodes[site] = nodes
        if not nodes:
            continue
        arcs = route_arcs(nodes, site, f.e2_weight_cap, f.e2_volume_cap,
                          p.e2_return_unload_time, 'e2', None)
        slots = sum(min(p.e2_max_trips_per_vehicle, len(nodes)//(v+1) if trip_symmetry else len(nodes))
                    for v in range(min(f.e2_per_sat_count, len(nodes))))
        internal_estimate += sum(a >= 0 and b >= 0 for a, b in arcs)*slots
    compressed_routes = compact and (internal_estimate >= M5_COMPRESS_ROUTE_MIN_INTERNAL_ARCS
                                    if compress_multibatch_routes is None else compress_multibatch_routes)

    def route(nodes, base, cap_w, cap_v, unload, label, layer, batch=None):
        nonlocal objective, indexed_internal
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
        arcs = route_arcs(nodes, base, cap_w, cap_v, unload, layer, batch)
        if layer == 'e2':
            indexed_internal += sum(a >= 0 and b >= 0 for a, b in arcs)
        if shared and compressed_routes:
            x = {}
            for a, b in arcs:
                if a >= 0 and b >= 0:
                    key = keys[a], keys[b]
                    if key not in global_arcs:
                        global_arcs[key] = m.addVar(vtype=GRB.BINARY, name=f'm5_link_{len(global_arcs)}')
                        shared_arcs[key] = [global_arcs[key]]
                    x[a, b] = global_arcs[key]
                else:
                    x[a, b] = m.addVar(vtype=GRB.BINARY, name=f'{label}_arc[{a},{b}]')
        else:
            x = m.addVars(arcs, vtype=GRB.BINARY, name=label+'_arc')
        incoming, outgoing = {i: [] for i in range(count)}, {i: [] for i in range(count)}
        for a, b in arcs:
            if b >= 0:
                incoming[b].append(x[a, b])
            if a >= 0:
                outgoing[a].append(x[a, b])
        m.addConstr(start <= H*used)
        m.addConstr(end <= H*used)
        if shared and linear_links:
            m.addConstr(start >= p.cycle_start*used)
        else:
            m.addConstr((used == 1) >> (start >= p.cycle_start))
        m.addConstr(gp.quicksum(x[a, b] for a, b in arcs if a == -1) == used)
        m.addConstr(gp.quicksum(x[a, b] for a, b in arcs if b == -2) == used)
        initial_w = gp.quicksum(n['w'] if free and layer == 'e1' else n['w']*visit[i]
                                for i, n in enumerate(nodes) if n['kind'] == 'd')
        initial_v = gp.quicksum(n['v'] if free and layer == 'e1' else n['v']*visit[i]
                                for i, n in enumerate(nodes) if n['kind'] == 'd')
        departure_loads = ()
        if layer == 'e2' and factor_e2_loads:
            # Reusing the sum in every source-arc propagation row creates a
            # dense matrix. Define it once; projection preserves even the LP.
            departure_loads = (m.addVar(ub=cap_w, name=label+'_departure_weight'),
                               m.addVar(ub=cap_v, name=label+'_departure_volume'))
            m.addConstr(departure_loads[0] == initial_w)
            m.addConstr(departure_loads[1] == initial_v)
            initial_w, initial_v = departure_loads
        m.addConstr(initial_w <= cap_w*(used if strengthen else 1))
        m.addConstr(initial_v <= cap_v*(used if strengthen else 1))
        if strengthen:
            # At the end of a route every delivery is unloaded, so all of its
            # pickups must fit simultaneously. Scale both peaks by activation.
            m.addConstr(gp.quicksum(n['w']*visit[i] if layer == 'e2' else n['w']
                                  for i, n in enumerate(nodes) if n['kind'] == 'p') <= cap_w*used)
            m.addConstr(gp.quicksum(n['v']*visit[i] if layer == 'e2' else n['v']
                                  for i, n in enumerate(nodes) if n['kind'] == 'p') <= cap_v*used)
        for i, n in enumerate(nodes):
            if i % 64 == 0:
                check_budget()
            if shared and compressed_routes:
                if (-1, i) in x:
                    m.addConstr(x[-1, i] <= visit[i])
                if (i, -2) in x:
                    m.addConstr(x[i, -2] <= visit[i])
            else:
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
            if not shared or free:
                m.addConstr((visit[i] == 1) >> (times[i] >= n['window'][0]))
                m.addConstr((visit[i] == 1) >> (times[i] <= n['window'][1]))
        for arc_index, (a, b) in enumerate(arcs):
            if arc_index % 64 == 0:
                check_budget()
            left = base if a == -1 else nodes[a]['site']
            right = base if b == -2 else nodes[b]['site']
            if not (shared and compressed_routes and a >= 0 and b >= 0):
                term = problem.distance(left, right)*x[a, b]
                objective += term
                if distance_cut_mode == 'components':
                    key = ('E1', batch.id) if layer == 'e1' else ('E2', base)
                    if key not in component_costs:
                        component_costs[key] = gp.LinExpr()
                    component_costs[key] += term
            if a == -1:
                if shared and linear_links:
                    lower = nodes[b]['window'][0]
                    travel = problem.travel(left, right)
                    m.addConstr(times[b] >= start+travel-(H+travel-lower)*(1-x[a, b]))
                else:
                    m.addConstr((x[a, b] == 1) >> (times[b] >= start+problem.travel(left, right)))
                if rank is not None:
                    m.addConstr((x[a, b] == 1) >> (rank[b] == 1))
                sign = -1 if nodes[b]['kind'] == 'd' else 1
                for load, initial, amount, capacity in (
                        (load_w[b], initial_w, nodes[b]['w'], cap_w),
                        (load_v[b], initial_v, nodes[b]['v'], cap_v)):
                    if shared and linear_links:
                        lower = amount if strengthen and sign == 1 else 0
                        upper = max(0, capacity-amount) if strengthen and sign == -1 else capacity
                        m.addConstr(load >= initial+sign*amount-(capacity+sign*amount-lower)*(1-x[a, b]))
                        m.addConstr(load <= initial+sign*amount+(upper-sign*amount)*(1-x[a, b]))
                    else:
                        m.addConstr((x[a, b] == 1) >> (load == initial+sign*amount))
            elif b == -2:
                tail = nodes[a]['service']+problem.travel(left, right)+unload
                if shared and linear_links:
                    m.addConstr(end >= times[a]+tail-(nodes[a]['window'][1]+tail)*(1-x[a, b]))
                else:
                    m.addConstr((x[a, b] == 1) >> (end >= times[a]+tail))
            else:
                if shared:
                    if not compressed_routes:
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
                    global_internal_arcs=shared and compressed_routes,
                    departure_loads=departure_loads,
                    visit=visit, times=times, x=x, label=label)

    for b in p.batches:
        for k in range(f.e1_count):
            nodes = []
            for s in problem.satellites:
                n = problem.demands[s, b.id]
                if free:
                    q = m.addVar(vtype=GRB.INTEGER, ub=total)
                    w = m.addVar(ub=f.e1_weight_cap)
                    nodes.append(dict(site=s, kind='d', quantity=q, w=w, v=q*f.container_volume,
                                      service=problem.service(s), window=(b.start, b.cutoff-problem.service(s))))
                elif n:
                    nodes.append(dict(site=s, kind='d', quantity=n, w=problem.weights[s, b.id],
                                      v=n*f.container_volume, service=problem.service(s),
                                      window=(b.start, b.cutoff-problem.service(s))))
                if problem.reusable and (not single or n or free):
                    # B=1: all orders must be unloaded by its sole cutoff, so
                    # every satellite's pickup quantity is known in advance.
                    q = n if single and not free else m.addVar(vtype=GRB.INTEGER, ub=total, name=f'q_{b.id}_{k}_{s}')
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
    pickup_returns = {}
    if compact_cutoff:
        for o in problem.orders:
            check_budget()
            inward = travel_bounds['inward'][o.satellite][o.customer]
            tail = o.service_time+inward+p.e2_return_unload_time
            lower = windows[o.id, 'p'][0]+tail
            upper = min(p.batches[-1].cutoff, H-p.e2_turnaround_time)
            returned = m.addVar(vtype=GRB.INTEGER, lb=lower, ub=upper, name=f'pickup_return_{o.id}')
            pickup_returns[o.id] = returned
            m.addConstr(returned >= order_times[o.id, 'p']+tail)
            previous = 0
            for b in p.batches:
                if b.cutoff < lower:
                    eligible = 0
                elif b.cutoff >= upper:
                    eligible = 1
                else:
                    eligible = m.addVar(vtype=GRB.BINARY, name=f'eligible_{o.id}_{b.id}')
                    # Exact integer-time classification with tight bounds.
                    # Equality belongs to this cutoff; lateness starts at +1.
                    m.addConstr(returned <= b.cutoff+(upper-b.cutoff)*(1-eligible))
                    m.addConstr(returned >= b.cutoff+1-(b.cutoff+1-lower)*eligible)
                    m.addConstr(eligible >= previous)
                q_expected[o.satellite, b.id] += o.count*(eligible-previous)
                previous = eligible
    for s in problem.satellites:
        orders = [o for o in problem.orders if (s in domains[o.customer] if free else o.satellite == s)]
        if not orders:
            continue
        first_slots = []
        vehicle_trips = []
        task_count = len(orders)*len(problem.task_kinds)
        for v in range(min(f.e2_per_sat_count, len(orders)*len(problem.task_kinds))):
            previous = None
            used_slots = []
            slot_limit = min(p.e2_max_trips_per_vehicle, task_count//(v+1) if trip_symmetry else task_count)
            for slot in range(slot_limit):
                nodes = e2_nodes[s]
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
                if linear_links:
                    m.addConstr(r['end'] <= (H-p.e2_turnaround_time)*r['used'])
                else:
                    m.addConstr((r['used'] == 1) >> (r['end']+p.e2_turnaround_time <= H))
                previous_route = previous
                if previous_route is not None:
                    m.addConstr(r['used'] <= previous['used'])
                    if linear_links:
                        m.addConstr(r['step_start'] >= previous['step_end']+1-(causal_limit+1)*(1-r['used']))
                    else:
                        m.addConstr((r['used'] == 1) >> (r['start'] >= previous['end']+p.e2_turnaround_time))
                        m.addConstr((r['used'] == 1) >> (r['step_start'] >= previous['step_end']+1))
                previous = r
                # Calendar order makes variable creation independent of the
                # interpreter hash seed, which matters in timed comparisons.
                present_batches = {o.batch for o in orders}
                batches = [b.id for b in p.batches if b.id in present_batches]
                selects = {b: m.addVar(vtype=GRB.BINARY) for b in batches}
                m.addConstr(gp.quicksum(selects.values()) <= (r['used'] if strengthen else 1))
                # Dispatch preparation for a loaded trip occupies its vehicle.
                # It starts after both inbound goods and the previous trip are ready.
                if previous_route is not None:
                    occupied = (previous_route['end']+p.e2_turnaround_time
                                +p.e2_dispatch_prep_time*gp.quicksum(selects.values()))
                    if linear_links:
                        # When unused, all batch selectors are zero, and the
                        # previous route returns by H-turnaround.
                        m.addConstr(r['start'] >= occupied-H*(1-r['used']))
                    else:
                        m.addConstr((r['used'] == 1) >> (r['start'] >= occupied))
                for b in batches:
                    dvis = [r['visit'][i] for i, n in enumerate(nodes) if n['kind'] == 'd' and n['order'].batch == b]
                    m.addConstr(selects[b] <= gp.quicksum(dvis))
                    if linear_links:
                        # selects[b] is exactly the OR of this batch's delivery
                        # visits. One supply link replaces one per order copy.
                        latest = problem.batch_map[b].cutoff+p.e2_dispatch_prep_time
                        m.addConstr(r['start'] >= deliveries[s, b]+p.e2_dispatch_prep_time-latest*(1-selects[b]))
                    if strengthen:
                        for resource, capacity in (('w', f.e2_weight_cap), ('v', f.e2_volume_cap)):
                            m.addConstr(gp.quicksum(n[resource]*r['visit'][i] for i, n in enumerate(nodes)
                                        if n['kind'] == 'd' and n['order'].batch == b) <= capacity*selects[b])
                for i, n in enumerate(nodes):
                    o = n['order']
                    if not compact:
                        m.addConstr((r['visit'][i] == 1) >> (order_times[o.id, n['kind']] == r['times'][i]))
                    if n['kind'] == 'd':
                        m.addConstr(r['visit'][i] <= selects[o.batch])
                        if not linear_links:
                            m.addConstr((r['visit'][i] == 1) >> (r['start'] >= deliveries[s, o.batch]+p.e2_dispatch_prep_time))
                        if len(p.batches) == 1:
                            m.addConstr((r['visit'][i] == 1) >> (r['times'][i]+o.service_time <= p.batches[0].cutoff))
                    elif single:
                        m.addConstr((r['visit'][i] == 1) >> (r['end'] <= p.batches[0].cutoff))
                    elif compact_cutoff:
                        if linear_links:
                            lower = windows[o.id, 'p'][0]+o.service_time+travel_bounds['inward'][s][o.customer]+p.e2_return_unload_time
                            upper = min(p.batches[-1].cutoff, H-p.e2_turnaround_time)
                            m.addConstr(pickup_returns[o.id] >= r['end']-(H-lower)*(1-r['visit'][i]))
                            m.addConstr(pickup_returns[o.id] <= r['end']+upper*(1-r['visit'][i]))
                        else:
                            m.addConstr((r['visit'][i] == 1) >> (pickup_returns[o.id] == r['end']))
                for (i, j), arc in r['x'].items():
                    if compact and i >= 0 and j >= 0:
                        continue
                    left = r['step_start'] if i == -1 else order_steps[nodes[i]['order'].id, nodes[i]['kind']]
                    right = r['step_end'] if j == -2 else order_steps[nodes[j]['order'].id, nodes[j]['kind']]
                    if linear_links:
                        m.addConstr(right >= left+1-(causal_limit+1)*(1-arc))
                    else:
                        m.addConstr((arc == 1) >> (right >= left+1))
                prior_h = 0
                gs = []
                for b in (() if single or compact_cutoff else p.batches):
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
                    if single or compact_cutoff:
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
            if trip_symmetry and vehicle_trips:
                m.addConstr(gp.quicksum(used_slots) <= gp.quicksum(vehicle_trips[-1]))
            vehicle_trips.append(used_slots)
    if compact:
        for key, variable in order_times.items():
            variable.LB, variable.UB = windows[key]
        if compressed_routes:
            memberships = {key: [] for key in order_times}
            entering = {key: [] for key in order_times}
            leaving = {key: [] for key in order_times}
            identities, identity_limits = {}, {}
            for site in problem.satellites:
                local_routes = [r for r in routes2 if r['satellite'] == site]
                for route_id, r in enumerate(local_routes, 1):
                    for i, n in enumerate(r['nodes']):
                        key = n['order'].id, n['kind']
                        if key not in identities:
                            identities[key] = m.addVar(lb=1, ub=len(local_routes), name=f'm5_route_{len(identities)}')
                            identity_limits[key] = len(local_routes)-1
                        memberships[key].append(route_id*r['visit'][i])
                        if (-1, i) in r['x']:
                            entering[key].append(r['x'][-1, i])
                        if (i, -2) in r['x']:
                            leaving[key].append(r['x'][i, -2])
            for (left, right), arc in global_arcs.items():
                check_budget()
                leaving[left].append(arc)
                entering[right].append(arc)
                # Exactly one binary visit makes each continuous identity
                # equal its unique local route number in any integer solution.
                slack = identity_limits[left]
                m.addConstr(identities[left]-identities[right] <= slack*(1-arc))
                m.addConstr(identities[right]-identities[left] <= slack*(1-arc))
            for key in order_times:
                check_budget()
                m.addConstr(identities[key] == gp.quicksum(memberships[key]))
                m.addConstr(gp.quicksum(entering[key]) == 1)
                m.addConstr(gp.quicksum(leaving[key]) == 1)
        for (left, right), indexed in shared_arcs.items():
            check_budget()
            active = global_arcs[left, right] if compressed_routes else m.addVar(vtype=GRB.BINARY)
            aggregate_arcs[left, right] = active
            if compressed_routes:
                term = problem.distance(problem.order_map[left[0]].customer,
                                        problem.order_map[right[0]].customer)*active
                objective += term
                if distance_cut_mode == 'components':
                    key = 'E2', problem.order_map[left[0]].satellite
                    component_costs.setdefault(key, gp.LinExpr())
                    component_costs[key] += term
            else:
                m.addConstr(active == gp.quicksum(indexed))
            a, b = problem.order_map[left[0]], problem.order_map[right[0]]
            travel = a.service_time+problem.travel(a.customer, b.customer)
            if linear_links:
                slack = max(0, windows[left][1]+travel-windows[right][0])
                m.addConstr(order_times[right] >= order_times[left]+travel-slack*(1-active))
            else:
                m.addConstr((active == 1) >> (order_times[right] >= order_times[left]+travel))
            _, weight, volume = problem.task(*right)
            sign = -1 if right[1] == 'd' else 1
            for loads, resource, amount, capacity in (
                    (task_load_w, 1, weight, f.e2_weight_cap), (task_load_v, 2, volume, f.e2_volume_cap)):
                if linear_links:
                    left_amount = problem.task(*left)[resource]
                    lo_left = left_amount if strengthen and left[1] == 'p' else 0
                    hi_left = max(0, capacity-left_amount) if strengthen and left[1] == 'd' else capacity
                    lo_right = amount if strengthen and right[1] == 'p' else 0
                    hi_right = max(0, capacity-amount) if strengthen and right[1] == 'd' else capacity
                    lower_slack = max(0, hi_left+sign*amount-lo_right)
                    upper_slack = max(0, hi_right-lo_left-sign*amount)
                    m.addConstr(loads[right] >= loads[left]+sign*amount-lower_slack*(1-active))
                    m.addConstr(loads[right] <= loads[left]+sign*amount+upper_slack*(1-active))
                else:
                    m.addConstr((active == 1) >> (loads[right] == loads[left]+sign*amount))
            if linear_links:
                m.addConstr(order_steps[right] >= order_steps[left]+1-(causal_limit+1)*(1-active))
            else:
                m.addConstr((active == 1) >> (order_steps[right] >= order_steps[left]+1))
    capacity_separator = None
    if strengthen and compact and e2_capacity_cuts:
        from .multibatch_cuts import build_capacity_separator
        capacity_separator = build_capacity_separator(problem, aggregate_arcs, check_budget)
        if capacity_separator.candidates:
            m.Params.PreCrush = 1
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
    certificate_rows = {}
    if distance_cut_mode != 'none':
        check_budget()
        certificate = _distance_certificate
        if certificate is None:
            from .multibatch_bounds import distance_certificate
            try:
                certificate = distance_certificate(problem, deadline=deadline)
            except TimeoutError:
                m.dispose()
                raise
        if certificate.get('scope') == 'full_fixed_M5_cycle_distance':
            if distance_cut_mode == 'total' and certificate['bound'] > 0:
                certificate_rows['total'] = m.addConstr(objective >= certificate['bound'], 'm5_distance_total')
            elif distance_cut_mode == 'components':
                for row in certificate['components']:
                    check_budget()
                    key = (row['echelon'], row['batch'] if row['echelon'] == 'E1' else row['satellite'])
                    bound = max(0.0, row['bound']-1e-7*max(1.0, row['bound']))
                    if bound > 0:
                        component_costs.setdefault(key, gp.LinExpr())
                        certificate_rows[key] = m.addConstr(component_costs[key] >= bound,
                                                            f'm5_distance_{key[0]}_{key[1]}')
    m.setObjective(objective, GRB.MINIMIZE)
    m.update()
    return m, dict(e1=routes1, e2=routes2, shared_e2=compact, shared_e2_arcs=len(shared_arcs),
                   global_e2_arcs=global_arcs, compressed_e2_routes=compressed_routes,
                   indexed_e2_internal_arcs=indexed_internal,
                   assignments=assignments, capacity_activation_cuts=strengthen,
                   pickup_returns=pickup_returns, shared_cutoff=compact_cutoff,
                   capacity_separator=capacity_separator, trip_count_symmetry=trip_symmetry,
                   linear_e2_links=linear_links, factored_e2_loads=bool(factor_e2_loads),
                   distance_cut_mode=distance_cut_mode, distance_certificate_rows=certificate_rows,
                   distance_component_costs=component_costs)
