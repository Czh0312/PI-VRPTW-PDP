"""Distance and cost objective definitions for M5."""
from __future__ import annotations


def add_distance_cost(expression, problem, left, right, arc):
    """Count a selected physical arc once in the distance objective."""
    expression += problem.distance(left, right) * arc
    return expression


def add_distance_term(expression, term):
    expression += term
    return expression


def set_distance_objective(model, expression):
    from gurobipy import GRB

    model.setObjective(expression, GRB.MINIMIZE)


def set_pool_cost_objective(parameters, variables, distances):
    """Use the same pool, transport, work and activation costs as M6–M8."""
    from collections import defaultdict
    import gurobipy as gp
    from gurobipy import GRB

    profile, policy = parameters.problem.cost_profile, parameters.policy
    rates = profile.rates
    parts = dict(container_pool=rates['container_pool_cost_per_period'] * variables.pool)
    for layer in ('e1', 'e2'):
        routes = variables.routes[layer]
        prep = policy.e1_load_time if layer == 'e1' else policy.e2_dispatch_prep_time
        parts[layer + '_distance'] = (distances.get(layer, 0) * rates['distance_scale_to_km']
                                     * rates[layer + '_distance_cost_per_km'])
        parts[layer + '_work'] = (gp.quicksum(r['end'] - r['start'] + prep * r['has_delivery']
                                             for r in routes)
                                 * policy.minutes(1) * rates[layer + '_work_cost_per_minute'])
        parts[layer + '_trips'] = gp.quicksum(r['used'] for r in routes) * rates[layer + '_trip_cost']
        groups = defaultdict(list)
        for r in routes:
            key = r['vehicle'] if layer == 'e1' else (r['satellite'], r['vehicle'])
            groups[key].append(r['used'])
        active = []
        for i, uses in enumerate(groups.values()):
            flag = variables.model.addVar(vtype=GRB.BINARY, name=f'active_{layer}_{i}')
            variables.model.addConstr(flag <= gp.quicksum(uses))
            for used in uses:
                variables.model.addConstr(flag >= used)
            active.append(flag)
        variables.physical_vehicles[layer] = active
        parts[layer + '_vehicles'] = gp.quicksum(active) * rates[layer + '_vehicle_cost_per_cycle']
    parts.update(profile.constant_cost(parameters.problem.total))
    variables.model.setObjective(gp.quicksum(parts.values()), GRB.MINIMIZE)
    return parts
