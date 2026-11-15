"""One objective builder for the fixed-calendar MILPs."""


def set_fixed_calendar_cost_objective(parameters, variables, check_budget):
    from collections import defaultdict
    import gurobipy as gp
    from gurobipy import GRB

    problem, policy = parameters.problem, parameters.policy
    distances = {}
    for layer, routes in variables.routes.items():
        seen, terms = set(), []
        for route in routes:
            check_budget()
            for (a, b), arc in route['x'].items():
                if id(arc) in seen:
                    continue
                seen.add(id(arc))
                left = route['base'] if a == -1 else route['nodes'][a]['site']
                right = route['base'] if b == -2 else route['nodes'][b]['site']
                terms.append(problem.distance(left, right) * arc)
        distances[layer] = gp.quicksum(terms)
    profile = getattr(problem, 'cost_profile', None)
    if profile is None:
        # Legacy physical fixtures supply only distances.
        variables.model.setObjective(gp.quicksum(distances.values()), GRB.MINIMIZE)
        return

    rates = profile.rates
    working_minutes, trips, vehicles = {}, {}, {}
    for layer, routes in variables.routes.items():
        prep = policy.e1_load_time if layer == 'e1' else policy.e2_dispatch_prep_time
        working_minutes[layer] = gp.quicksum(
            r['end'] - r['start'] + prep * r['has_delivery'] for r in routes) * policy.minutes(1)
        trips[layer] = gp.quicksum(r['used'] for r in routes)
        vehicles[layer] = 0
        if not rates[layer + '_vehicle_cost_per_cycle']:
            continue
        groups = defaultdict(list)
        for r in routes:
            key = r['vehicle'] if layer == 'e1' else (r['satellite'], r['vehicle'])
            groups[key].append(r['used'])
        active = []
        for i, uses in enumerate(groups.values()):
            check_budget()
            flag = variables.model.addVar(vtype=GRB.BINARY, name=f'active_{layer}_{i}')
            variables.model.addConstr(flag <= gp.quicksum(uses))
            for used in uses:
                variables.model.addConstr(flag >= used)
            active.append(flag)
        variables.physical_vehicles[layer] = active
        vehicles[layer] = gp.quicksum(active)
    parts = profile.period_components(
        distances=distances, working_minutes=working_minutes, vehicles=vehicles, trips=trips,
        pool=policy.container_pool_size, issued=problem.total, returned=problem.total,
        reusable=problem.reusable)
    variables.model.setObjective(gp.quicksum(parts.values()), GRB.MINIMIZE)
