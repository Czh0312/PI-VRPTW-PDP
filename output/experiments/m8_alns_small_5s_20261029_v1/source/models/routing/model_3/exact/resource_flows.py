"""Valid delivery/return arc flows strengthening the original M3 route model.

On each physical arc, remaining deliveries and collected returns share the
original capacity. Project any original route onto these four continuous
flows; every added equation holds. The complete original model remains in
place, so its integer feasible schedules and objective are unchanged.
"""
from __future__ import annotations


def add_resource_flows(parameters, variables, nodes_by_site, arcs_by_site, *, check_budget):
    import gurobipy as gp

    if parameters.free_assignment or not parameters.problem.reusable:
        return dict(applied=False, reason='outside_fixed_reusable_scope')
    model, fleet = variables.model, parameters.fleet
    metadata = dict(applied=True, sites={}, continuous_variables=0,
                    kind='delivery_return_arc_capacity_flows')
    for site, nodes in nodes_by_site.items():
        check_budget()
        if not nodes:
            continue
        arcs = arcs_by_site[site]
        routes = [route for route in variables.routes['e2'] if route['satellite'] == site]
        keys = [(node['order'].id, node['kind']) for node in nodes]
        active = {}
        for a, b in arcs:
            shared = variables.aggregate_arcs.get((keys[a], keys[b])) if a >= 0 and b >= 0 else None
            active[a, b] = shared if shared is not None else gp.quicksum(
                route['x'][a, b] for route in routes if (a, b) in route['x'])
        incoming = {i: [] for i in range(len(nodes))}
        outgoing = {i: [] for i in range(len(nodes))}
        for edge in arcs:
            if edge[0] >= 0:
                outgoing[edge[0]].append(edge)
            if edge[1] >= 0:
                incoming[edge[1]].append(edge)
        for resource, capacity, task_loads in (
            ('w', fleet.e2_weight_cap, variables.task_load_weight),
            ('v', fleet.e2_volume_cap, variables.task_load_volume),
        ):
            check_budget()
            scale = capacity if capacity > 0 else 1.
            remaining = model.addVars(arcs, ub=capacity/scale, name=f'e2_{site}_{resource}_delivery_flow')
            returned = model.addVars(arcs, ub=capacity/scale, name=f'e2_{site}_{resource}_return_flow')
            for index, (a, b) in enumerate(arcs):
                if index % 128 == 0:
                    check_budget()
                if a == -1:
                    returned[a, b].UB = 0
                if b == -2:
                    remaining[a, b].UB = 0
                # Just after a delivery / just before a pickup, that task's
                # amount cannot occupy the arc. Each bound is independently
                # valid on the original physical route.
                upper = capacity
                if a >= 0 and nodes[a]['kind'] == 'd':
                    upper = min(upper, capacity-nodes[a][resource])
                if b >= 0 and nodes[b]['kind'] == 'p':
                    upper = min(upper, capacity-nodes[b][resource])
                model.addConstr(remaining[a, b]+returned[a, b] <= max(0., upper)/scale*active[a, b])
                if b >= 0 and nodes[b]['kind'] == 'd':
                    model.addConstr(remaining[a, b] >= nodes[b][resource]/scale*active[a, b])
                if a >= 0 and nodes[a]['kind'] == 'p':
                    model.addConstr(returned[a, b] >= nodes[a][resource]/scale*active[a, b])
            for i, node in enumerate(nodes):
                check_budget()
                delivery = node[resource] if node['kind'] == 'd' else 0.
                pickup = node[resource] if node['kind'] == 'p' else 0.
                model.addConstr(gp.quicksum(remaining[e] for e in incoming[i])
                                -gp.quicksum(remaining[e] for e in outgoing[i]) == delivery/scale)
                model.addConstr(gp.quicksum(returned[e] for e in outgoing[i])
                                -gp.quicksum(returned[e] for e in incoming[i]) == pickup/scale)
                if keys[i] in task_loads:
                    model.addConstr(task_loads[keys[i]]/scale == gp.quicksum(
                        remaining[e]+returned[e] for e in outgoing[i]))
            metadata['continuous_variables'] += 2*len(arcs)
        metadata['sites'][site] = dict(tasks=len(nodes), arcs=len(arcs))
    return metadata
