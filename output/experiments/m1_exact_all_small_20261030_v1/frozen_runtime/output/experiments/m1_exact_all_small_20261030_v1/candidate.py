"""Research-only M1 adaptation of M3 capacity/flow and input-LP ideas.

The original complete M1 model is retained, including load propagation.
Only valid redundant rows and continuous delivery flows are added. No
history, starts, model-number spoofing, pickup tasks or K restrictions.
"""
from __future__ import annotations

import math
import time


def trip_minimum(nodes, weight_cap, volume_cap):
    if not nodes:
        return 0
    return max([1] + [math.ceil(sum(n[key] for n in nodes)/cap-1e-9)
                       for key, cap in (('w', weight_cap), ('v', volume_cap)) if cap > 0])


def site_data(problem, context, site):
    import gurobipy as gp
    routes = [r for r in context['e2'] if r['satellite'] == site]
    if not routes:
        return [], [], {}, []
    nodes = routes[0]['nodes']
    assert all(n['kind'] == 'd' for n in nodes)
    keys = [(n['order'].id, 'd') for n in nodes]
    edges = list(routes[0]['x'])
    active = {}
    for a, b in edges:
        shared = context['variables'].aggregate_arcs.get((keys[a], keys[b])) if a >= 0 and b >= 0 else None
        active[a, b] = shared if shared is not None else gp.quicksum(
            r['x'][a, b] for r in routes if (a, b) in r['x'])
    return nodes, edges, active, routes


def add_capacity_flows(problem, model, context, *, deadline):
    import gurobipy as gp
    assert problem.model_number == 1 and not problem.reusable and not problem.free_assignment
    fleet = problem.fleet
    result = dict(capacity_rows=0, flow_variables=0, sites={})

    def check():
        if time.perf_counter() >= deadline:
            raise TimeoutError('M1 candidate construction reached the total budget')

    for layer, caps in (('e1', (fleet.e1_weight_cap, fleet.e1_volume_cap)),
                        ('e2', (fleet.e2_weight_cap, fleet.e2_volume_cap))):
        for r in context[layer]:
            check()
            for key, capacity in zip(('w', 'v'), caps):
                model.addConstr(gp.quicksum(n[key]*r['visit'][i] for i, n in enumerate(r['nodes']))
                                <= capacity*r['used'], name=f"transfer_{r['label']}_{key}_activation")
                result['capacity_rows'] += 1
    for site in problem.satellites:
        check()
        nodes, edges, active, routes = site_data(problem, context, site)
        if not nodes:
            continue
        required = trip_minimum(nodes, fleet.e2_weight_cap, fleet.e2_volume_cap)
        model.addConstr(gp.quicksum(r['used'] for r in routes) >= required,
                        name=f'transfer_minimum_trips_{site}')
        incoming = {i: [e for e in edges if e[1] == i] for i in range(len(nodes))}
        outgoing = {i: [e for e in edges if e[0] == i] for i in range(len(nodes))}
        for resource, capacity, loads in (
                ('w', fleet.e2_weight_cap, context['variables'].task_load_weight),
                ('v', fleet.e2_volume_cap, context['variables'].task_load_volume)):
            check()
            scale = capacity if capacity > 0 else 1.
            flow = model.addVars(edges, ub=capacity/scale, name=f'transfer_{site}_{resource}_delivery')
            result['flow_variables'] += len(edges)
            for a, b in edges:
                check()
                upper = capacity - nodes[a][resource] if a >= 0 else capacity
                model.addConstr(flow[a, b] <= max(0., upper)/scale*active[a, b])
                if b == -2:
                    flow[a, b].UB = 0
                elif b >= 0:
                    model.addConstr(flow[a, b] >= nodes[b][resource]/scale*active[a, b])
            for i, node in enumerate(nodes):
                model.addConstr(gp.quicksum(flow[e] for e in incoming[i])
                                -gp.quicksum(flow[e] for e in outgoing[i]) == node[resource]/scale)
                key = node['order'].id, 'd'
                if key in loads:
                    model.addConstr(loads[key]/scale == gp.quicksum(flow[e] for e in outgoing[i]))
        result['sites'][site] = dict(tasks=len(nodes), arcs=len(edges), minimum_trips=required)
    return result


def add_input_bounds(problem, model, context, *, deadline, seed=42):
    """Delivery-only LP; M3's generic subset separator is reused as research code.

    M3's full route-bound function assumes a pickup for every delivery and
    is deliberately not called. An incomplete LP contributes no primal bound.
    """
    import gurobipy as gp
    from models.routing.model_3.exact.network_cuts import separate
    from models.routing.model_3.exact.route_partition import e1_distance_bound
    begun = time.perf_counter()
    result = dict(applied=False, sites={}, historical_information_used=False)
    e1 = e1_distance_bound(problem, deadline=begun+max(0., deadline-begun)*.15)
    lower_sum = 0.
    sites = [s for s in problem.satellites if any(o.satellite == s for o in problem.orders)]
    for index, site in enumerate(sites):
        now = time.perf_counter()
        site_deadline = now+max(0., deadline-now)/(len(sites)-index)
        nodes, edges, active, _ = site_data(problem, context, site)
        incoming = {i: [e for e in edges if e[1] == i] for i in range(len(nodes))}
        outgoing = {i: [e for e in edges if e[0] == i] for i in range(len(nodes))}
        costs = {(a, b): problem.distance(site if a == -1 else nodes[a]['site'],
                                        site if b == -2 else nodes[b]['site']) for a, b in edges}
        lower = max(sum(min((costs[e] for e in values), default=0.) for values in incoming.values()),
                    sum(min((costs[e] for e in values), default=0.) for values in outgoing.values()))
        cuts, known, calls = [], set(), []
        if time.perf_counter() < site_deadline:
            relaxation = gp.Model('m1_transfer_delivery_arc_LP')
            try:
                relaxation.Params.OutputFlag = 0
                relaxation.Params.Threads = 1
                relaxation.Params.Seed = seed
                x = relaxation.addVars(edges, ub=1., name='arc')
                for i in range(len(nodes)):
                    relaxation.addConstr(gp.quicksum(x[e] for e in incoming[i]) == 1)
                    relaxation.addConstr(gp.quicksum(x[e] for e in outgoing[i]) == 1)
                required = trip_minimum(nodes, problem.fleet.e2_weight_cap, problem.fleet.e2_volume_cap)
                relaxation.addConstr(gp.quicksum(x[a, b] for a, b in edges if a == -1) >= required)
                relaxation.addConstr(gp.quicksum(x[a, b] for a, b in edges if b == -2) >= required)
                for a, b in edges:
                    if 0 <= a < b and (b, a) in x:
                        relaxation.addConstr(x[a, b]+x[b, a] <= 1)
                relaxation.setObjective(gp.quicksum(costs[e]*x[e] for e in edges))
                while time.perf_counter() < site_deadline:
                    relaxation.Params.TimeLimit = max(0., site_deadline-time.perf_counter())
                    relaxation.optimize()
                    calls.append(dict(status=relaxation.Status, seconds=relaxation.Runtime))
                    if relaxation.Status != gp.GRB.OPTIMAL:
                        break
                    lower = max(lower, float(relaxation.ObjVal))
                    values = {e: x[e].X for e in edges}
                    generated = separate(nodes, edges, values,
                        weight_cap=problem.fleet.e2_weight_cap, volume_cap=problem.fleet.e2_volume_cap,
                        distance=problem.distance, known=known, deadline=site_deadline)
                    if not generated:
                        break
                    for cut in generated:
                        subset = set(cut['tasks'])
                        relaxation.addConstr(gp.quicksum(x[a, b] for a, b in edges
                            if a in subset and b not in subset) >= cut['required'])
                        known.add(tuple(cut['tasks']))
                        cuts.append(cut)
            finally:
                relaxation.dispose()
        for j, cut in enumerate(cuts):
            if time.perf_counter() >= deadline:
                break
            subset = set(cut['tasks'])
            model.addConstr(gp.quicksum(active[a, b] for a, b in edges if a in subset and b not in subset)
                            >= cut['required'], name=f'transfer_{site}_subset_{j}')
        result['sites'][site] = dict(lower_bound=lower, capacity_cuts=len(cuts), LP_calls=calls)
        lower_sum += lower
    lower = e1+lower_sum
    model.addConstr(model.getObjective() >= math.nextafter(lower, -math.inf),
                    name='transfer_input_distance_lower_bound')
    model.update()
    result.update(applied=True, e1_lower_bound=e1, e2_lower_bound=lower_sum,
                  lower_bound=lower, seconds=time.perf_counter()-begun)
    return result


def strengthen(problem, model, context, *, deadline, seed=42):
    begun = time.perf_counter()
    flows = add_capacity_flows(problem, model, context, deadline=deadline)
    remaining = max(0., deadline-time.perf_counter())
    bounds = add_input_bounds(problem, model, context,
        deadline=min(deadline, time.perf_counter()+min(2., remaining*.15)), seed=seed)
    return dict(capacity_flows=flows, input_bounds=bounds, seconds=time.perf_counter()-begun)
