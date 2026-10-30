"""Assignment-safe load flows, causal conflicts and vehicle symmetry for M4.

Unlike a fixed-site bound, the task graph spans all eligible warehouses.
Each physical source/sink arc retains its vehicle and trip identity; each
internal task arc is counted once. Projecting an original route onto its
remaining deliveries and collected returns satisfies every flow equation.
Conversely, on an integer route, conservation and zero delivery at the sink
recover exactly the original post-service loads. No warehouse is fixed.
"""
from __future__ import annotations


def add_resource_flows(parameters, variables, *, check_budget):
    import gurobipy as gp

    if not parameters.share_e2 or not parameters.problem.reusable:
        return dict(applied=False, reason='requires_shared_reusable_tasks')
    model, problem, fleet = variables.model, parameters.problem, parameters.fleet
    keys = list(variables.task_times)
    amounts = {key: dict(zip(('w', 'v'), problem.task(*key)[1:])) for key in keys}
    edges = [(left, right, active) for (left, right), active in variables.aggregate_arcs.items()]
    sources, sinks = {}, {}
    for route_index, route in enumerate(variables.routes['e2']):
        check_budget()
        sources[route_index], sinks[route_index] = [], []
        for (a, b), active in route['x'].items():
            if a >= 0 and b >= 0:
                continue
            left = (route['nodes'][a]['order'].id, route['nodes'][a]['kind']) if a >= 0 else None
            right = (route['nodes'][b]['order'].id, route['nodes'][b]['kind']) if b >= 0 else None
            index = len(edges)
            edges.append((left, right, active))
            (sources if a == -1 else sinks)[route_index].append(index)
    incoming, outgoing = {key: [] for key in keys}, {key: [] for key in keys}
    for index, (left, right, _) in enumerate(edges):
        if left is not None:
            outgoing[left].append(index)
        if right is not None:
            incoming[right].append(index)
    metadata = dict(applied=True, kind='assignment_safe_delivery_return_arc_flows',
                    arcs=len(edges), continuous_variables=4*len(edges), tasks=len(keys))
    for resource, capacity, task_loads in (
        ('w', fleet.e2_weight_cap, variables.task_load_weight),
        ('v', fleet.e2_volume_cap, variables.task_load_volume),
    ):
        check_budget()
        scale = capacity if capacity > 0 else 1.
        delivery = model.addVars(len(edges), ub=capacity/scale, name=f'e2_{resource}_delivery_flow')
        returned = model.addVars(len(edges), ub=capacity/scale, name=f'e2_{resource}_return_flow')
        for index, (left, right, active) in enumerate(edges):
            if index % 128 == 0:
                check_budget()
            if left is None:
                returned[index].UB = 0
            if right is None:
                delivery[index].UB = 0
            upper = capacity
            if left is not None and left[1] == 'd':
                upper = min(upper, capacity-amounts[left][resource])
            if right is not None and right[1] == 'p':
                upper = min(upper, capacity-amounts[right][resource])
            model.addConstr(delivery[index]+returned[index] <= max(0., upper)/scale*active)
            if right is not None and right[1] == 'd':
                model.addConstr(delivery[index] >= amounts[right][resource]/scale*active)
            if left is not None and left[1] == 'p':
                model.addConstr(returned[index] >= amounts[left][resource]/scale*active)
        for key in keys:
            check_budget()
            amount = amounts[key][resource]/scale
            model.addConstr(gp.quicksum(delivery[i] for i in incoming[key])
                            -gp.quicksum(delivery[i] for i in outgoing[key])
                            == (amount if key[1] == 'd' else 0.))
            model.addConstr(gp.quicksum(returned[i] for i in outgoing[key])
                            -gp.quicksum(returned[i] for i in incoming[key])
                            == (amount if key[1] == 'p' else 0.))
            model.addConstr(task_loads[key]/scale == gp.quicksum(
                delivery[i]+returned[i] for i in outgoing[key]))
        for route_index, route in enumerate(variables.routes['e2']):
            check_budget()
            for kind, flows, boundary in (('d', delivery, sources), ('p', returned, sinks)):
                total = gp.quicksum(node[resource]/scale*route['visit'][i]
                                   for i, node in enumerate(route['nodes']) if node['kind'] == kind)
                model.addConstr(gp.quicksum(flows[i] for i in boundary[route_index]) == total)
    return metadata


def add_precedence_links(parameters, variables, *, check_budget):
    """Forbid two selected arcs that form a cycle with mandatory d -> p."""
    arcs = variables.aggregate_arcs
    conflicts = set()
    lifts = 0
    for (left, right), active in arcs.items():
        check_budget()
        predecessors = [left] + ([(left[0], 'd')] if left[1] == 'p' else [])
        successors = [right] + ([(right[0], 'p')] if right[1] == 'd' else [])
        for source in successors:
            for target in predecessors:
                reverse = source, target
                if reverse in arcs and reverse != (left, right):
                    conflicts.add(tuple(sorted(((left, right), reverse))))
        a, b = parameters.problem.order_map[left[0]], parameters.problem.order_map[right[0]]
        travel = a.service_time+parameters.problem.travel(a.customer, b.customer)
        prefix = [(left, 0)] + ([((left[0], 'd'), a.service_time+a.min_use_time)] if left[1] == 'p' else [])
        suffix = [(right, 0)] + ([((right[0], 'p'), b.service_time+b.min_use_time)] if right[1] == 'd' else [])
        original_slack = max(0, parameters.task_windows[left][1]+travel-parameters.task_windows[right][0])
        for source, before in prefix:
            for target, after in suffix:
                if source == left and target == right:
                    continue
                lag = before+travel+after
                slack = max(0, parameters.task_windows[source][1]+lag-parameters.task_windows[target][0])
                if 0 < slack < original_slack-1e-7:
                    variables.model.addConstr(variables.task_times[target]
                        >= variables.task_times[source]+lag-slack*(1-active))
                    lifts += 1
    for index, (first, second) in enumerate(sorted(conflicts)):
        if index % 64 == 0:
            check_budget()
        variables.model.addConstr(arcs[first]+arcs[second] <= 1, name=f'e2_precedence_conflict_{index}')
    return dict(applied=True, rows=len(conflicts), time_lifts=lifts)


def add_vehicle_symmetry(variables, *, check_budget):
    """Sort first tasks only among vehicles with equal nonempty trip counts.

    M4 already sorts by trip count. Unconditionally sorting first tasks too
    would be invalid; the trip-count difference relaxes the secondary key.
    """
    import gurobipy as gp

    groups = {}
    for route in variables.routes['e2']:
        groups.setdefault(route['satellite'], {}).setdefault(route['vehicle'], []).append(route)
    rows = 0
    for site, vehicles in groups.items():
        previous = None
        for vehicle, routes in sorted(vehicles.items()):
            check_budget()
            first = min(routes, key=lambda route: route['slot'])
            count = gp.quicksum(route['used'] for route in routes)
            key = gp.quicksum((i+1)*active for (a, i), active in first['x'].items() if a == -1)
            if previous is not None:
                last_count, last_key = previous
                size = len(first['nodes'])
                variables.model.addConstr(last_key+first['used'] <= key+size*(1-first['used'])
                    +size*(last_count-count), name=f'e2_equal_trip_first_task_{site}_{vehicle}')
                rows += 1
            previous = count, key
    return rows
