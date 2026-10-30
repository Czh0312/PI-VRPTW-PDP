"""Distance and cost objective definitions for M9."""
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
    """Minimize container-pool, route, work, trip and physical-vehicle costs."""
    import gurobipy as gp
    from gurobipy import GRB

    rates = parameters.problem.cost_profile.rates
    parts = {"container_pool": rates["container_pool_cost_per_period"] * variables.pool}
    for layer in ("e1", "e2"):
        parts[layer + "_distance"] = (
            gp.quicksum(r["distance"] for r in variables.routes[layer])
            * rates["distance_scale_to_km"]
            * rates[layer + "_distance_cost_per_km"]
        )
        parts[layer + "_work"] = (
            gp.quicksum(r["work_ticks"] for r in variables.routes[layer])
            * parameters.policy.minutes(1)
            * rates[layer + "_work_cost_per_minute"]
        )
        parts[layer + "_trips"] = (
            gp.quicksum(r["used"] for r in variables.routes[layer]) * rates[layer + "_trip_cost"]
        )
        parts[layer + "_vehicles"] = (
            gp.quicksum(variables.physical_vehicles[layer])
            * rates[layer + "_vehicle_cost_per_cycle"]
        )
    parts.update(parameters.problem.cost_profile.constant_cost(parameters.total_containers))
    variables.model.setObjective(gp.quicksum(parts.values()), GRB.MINIMIZE)
    return parts
