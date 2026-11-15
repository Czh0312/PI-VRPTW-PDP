"""Per-period objective for the variable-calendar MILP."""
from __future__ import annotations


def add_distance_cost(expression, problem, left, right, arc):
    """Count a selected physical arc once in the route distance."""
    expression += problem.distance(left, right) * arc
    return expression


def set_cost_objective(parameters, variables):
    """Minimize container-pool, route, work, trip and physical-vehicle costs."""
    import gurobipy as gp
    from gurobipy import GRB

    parts = parameters.problem.cost_profile.period_components(
        distances={layer: gp.quicksum(r["distance"] for r in routes)
                   for layer, routes in variables.routes.items()},
        working_minutes={layer: gp.quicksum(r["work_ticks"] for r in routes) * parameters.policy.minutes(1)
                         for layer, routes in variables.routes.items()},
        vehicles={layer: gp.quicksum(active) for layer, active in variables.physical_vehicles.items()},
        trips={layer: gp.quicksum(r["used"] for r in routes)
               for layer, routes in variables.routes.items()},
        pool=variables.pool, issued=parameters.total_containers, returned=parameters.total_containers)
    variables.model.setObjective(gp.quicksum(parts.values()), GRB.MINIMIZE)
    return parts
