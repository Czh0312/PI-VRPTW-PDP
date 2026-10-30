"""M5 inventory constraints; preserve the caller-owned model and variable handles."""
from __future__ import annotations



def multibatch_add_inventory_constraints(parameters, variables):
    """Require clean containers to be available at every batch loading time."""
    import gurobipy as gp

    cumulative = 0
    pool = variables.pool if variables.pool is not None else parameters.policy.container_pool_size
    for b in parameters.policy.batches if parameters.problem.reusable else ():
        cumulative += parameters.problem.batch_demands[b.id]
        if parameters.single_batch:
            variables.model.addConstr(
                gp.LinExpr(pool) >= cumulative,
                f"clean_stock_{b.id}",
            )
            continue
        released = []
        for r in variables.routes["e1"]:
            ready, late = (variables.multibatch_create_r() for _ in range(2))
            variables.model.addConstr(late == r["used"] - ready)
            variables.model.addConstr(
                (ready == 1) >> (r["end"] + parameters.policy.cleaning_duration <= b.start)
            )
            variables.model.addConstr(
                (late == 1) >> (r["end"] + parameters.policy.cleaning_duration >= b.start + 1)
            )
            q = variables.multibatch_create_quantity_3(parameters.total_containers)
            variables.model.addConstr(q <= r["quantity"])
            variables.model.addConstr(q <= parameters.total_containers * ready)
            variables.model.addConstr(
                q >= r["quantity"] - parameters.total_containers * (1 - ready)
            )
            released.append(q)
        variables.model.addConstr(
            pool + gp.quicksum(released) >= cumulative,
            f"clean_stock_{b.id}",
        )
