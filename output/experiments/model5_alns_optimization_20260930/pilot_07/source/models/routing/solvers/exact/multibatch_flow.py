"""Valid continuous load flows on the aggregate fixed-satellite E2 graph.

Forward goods enter only at a route source and are consumed at deliveries.
Returned containers are generated at pickups and leave only at a route sink.
Their sum on an arc fits its activated capacity. Every feasible indexed route
induces these flows, including directed/nonmetric and zero-duration routes.
"""


def add_load_flows(model, problem, routes, internal_arcs, check_budget):
    import gurobipy as gp

    variable_count = constraint_count = 0
    for site in problem.satellites:
        check_budget()
        local = [r for r in routes if r['satellite'] == site]
        if not local:
            continue
        tasks = [(o.id, k) for o in problem.orders if o.satellite == site for k in problem.task_kinds]
        task_set = set(tasks)
        # None is the base: direction distinguishes a source from a sink.
        arcs = {key: value for key, value in internal_arcs.items() if key[0] in task_set}
        boundary = {}
        for route in local:
            check_budget()
            for (a, b), variable in route['x'].items():
                if a >= 0 and b >= 0:
                    continue
                source = None if a == -1 else (route['nodes'][a]['order'].id, route['nodes'][a]['kind'])
                target = None if b == -2 else (route['nodes'][b]['order'].id, route['nodes'][b]['kind'])
                boundary.setdefault((source, target), []).append(variable)
        arcs.update({key: gp.quicksum(values) for key, values in boundary.items()})
        for name, resource, capacity in (('weight', 1, problem.fleet.e2_weight_cap),
                                          ('volume', 2, problem.fleet.e2_volume_cap)):
            quantities = {key: problem.task(*key)[resource] for key in tasks}
            if not any(quantities.values()):
                continue
            forward_in = {key: [] for key in tasks}
            forward_out = {key: [] for key in tasks}
            reverse_in = {key: [] for key in tasks}
            reverse_out = {key: [] for key in tasks}
            for index, ((source, target), active) in enumerate(arcs.items()):
                check_budget()
                # No undelivered goods return to the base; no empty boxes are
                # supplied by the base on these secondary routes.
                forward = model.addVar(ub=capacity, name=f'flow_d_{site}_{name}_{index}') if target is not None else 0
                reverse = model.addVar(ub=capacity, name=f'flow_p_{site}_{name}_{index}') if source is not None else 0
                variable_count += int(target is not None)+int(source is not None)
                model.addConstr(forward+reverse <= capacity*active)
                constraint_count += 1
                if source is not None:
                    forward_out[source].append(forward)
                    reverse_out[source].append(reverse)
                if target is not None:
                    forward_in[target].append(forward)
                    reverse_in[target].append(reverse)
            for key in tasks:
                check_budget()
                model.addConstr(gp.quicksum(forward_in[key])-gp.quicksum(forward_out[key]) ==
                                (quantities[key] if key[1] == 'd' else 0))
                model.addConstr(gp.quicksum(reverse_out[key])-gp.quicksum(reverse_in[key]) ==
                                (quantities[key] if key[1] == 'p' else 0))
                constraint_count += 2
    return dict(variables=variable_count, constraints=constraint_count)
