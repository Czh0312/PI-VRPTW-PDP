"""M5 batching constraints; preserve the caller-owned model and variable handles."""
from __future__ import annotations



def multibatch_add_availability_constraints(parameters, variables):
    """Encode necessary order-availability contradictions."""
    import gurobipy as gp
    from models.common.cycle.availability import fixed_order_availability_errors

    for index, issue in enumerate(fixed_order_availability_errors(parameters.problem)):
        variables.model.addConstr(
            gp.LinExpr() >= 1, name=f'fixed_availability_{index}_{issue["code"]}'
        )



def multibatch_add_assignment_constraints(parameters, variables):
    """Assign each customer to one satellite and link satellite activation and capacity."""
    import gurobipy as gp

    assignments, active_sites, counts, weights = {}, {}, {}, {}
    if parameters.free_assignment:
        assignments = {
            (c, s): variables.multibatch_create_customer_assignment(c, s)
            for c, sites in parameters.assignment_domains.items()
            for s in sites
        }
        for c, sites in parameters.assignment_domains.items():
            variables.model.addConstr(gp.quicksum(assignments[c, s] for s in sites) == 1)
        for s in parameters.problem.satellites:
            active_sites[s] = variables.multibatch_create_satellite_activation(s)
            members = [z for (c, site), z in assignments.items() if site == s]
            for z in members:
                variables.model.addConstr(z <= active_sites[s])
            variables.model.addConstr(active_sites[s] <= gp.quicksum(members))
            counts[s] = gp.quicksum(
                o.count * assignments[o.customer, s]
                for o in parameters.problem.orders
                if s in parameters.assignment_domains[o.customer]
            )
            weights[s] = gp.quicksum(
                (o.goods_weight + o.count * parameters.fleet.container_weight)
                * assignments[o.customer, s]
                for o in parameters.problem.orders
                if s in parameters.assignment_domains[o.customer]
            )
            variables.model.addConstr(weights[s] <= parameters.fleet.e1_weight_cap)
            variables.model.addConstr(
                counts[s] * parameters.fleet.container_volume <= parameters.fleet.e1_volume_cap
            )
    return assignments, active_sites, counts, weights



def multibatch_add_supply_time_constraints(parameters, variables):
    """Create supply-time variables and apply their safe bounds."""
    import gurobipy as gp

    deliveries = {
        (s, b.id): variables.multibatch_create_supply_time(parameters.horizon, b, s)
        for (s, bid), n in parameters.problem.demands.items()
        for b in parameters.policy.batches
        if bid == b.id and (n or parameters.free_assignment)
    }
    if parameters.linear_e2_links:
        for (s, bid), variable in deliveries.items():
            batch = parameters.problem.batch_map[bid]
            variable.LB = (
                batch.start
                + parameters.policy.e1_load_time
                + parameters.depot_outward[s]
                + parameters.problem.service(s)
            )
            variable.UB = batch.cutoff
    return deliveries



def multibatch_add_supply_coverage_constraints(parameters, variables):
    """Cover each satellite's inbound demand with first-echelon trips."""
    import gurobipy as gp

    for (s, bid), count in parameters.problem.demands.items():
        if count or parameters.free_assignment:
            variables.model.addConstr(
                gp.quicksum(
                    r["visit"][i]
                    for r in variables.routes["e1"]
                    if r["batch"].id == bid
                    for i, n in enumerate(r["nodes"])
                    if n["site"] == s and n["kind"] == "d"
                )
                == (variables.active_sites[s] if parameters.free_assignment else 1)
            )
