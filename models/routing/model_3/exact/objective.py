"""Distance and cost objective definitions for M3."""
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


def set_cost_objective(parameters, variables):
    """Keep physical distances separate; shared E2 arcs are counted once."""
    import gurobipy as gp
    from models.cost.fixed_calendar_milp import set_fixed_calendar_cost_objective

    distances = {}
    for layer in ('e1', 'e2'):
        seen, terms = set(), []
        for route in variables.routes[layer]:
            for (a, b), arc in route['x'].items():
                if id(arc) in seen:
                    continue
                seen.add(id(arc))
                left = route['base'] if a == -1 else route['nodes'][a]['site']
                right = route['base'] if b == -2 else route['nodes'][b]['site']
                terms.append(parameters.problem.distance(left, right) * arc)
        distances[layer] = gp.quicksum(terms)
    return set_fixed_calendar_cost_objective(
        parameters, variables, distances, parameters.policy.container_pool_size)
