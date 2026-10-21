"""M5 recovery constraints; preserve the caller-owned model and variable handles."""
from __future__ import annotations



def multibatch_add_cutoff_quantity_constraints(
    parameters,
    variables,
    *,
    check_budget,
):
    """Assign empty-container quantities to the first eligible recovery cutoff."""
    import gurobipy as gp

    q_expected = {
        (s, b.id): gp.LinExpr()
        for s in parameters.problem.satellites
        for b in parameters.policy.batches
    }
    pickup_returns = {}
    if parameters.share_cutoff:
        for o in parameters.problem.orders:
            check_budget()
            inward = parameters.travel_bounds["inward"][o.satellite][o.customer]
            tail = o.service_time + inward + parameters.policy.e2_return_unload_time
            lower = parameters.task_windows[o.id, "p"][0] + tail
            upper = min(
                parameters.policy.batches[-1].cutoff,
                parameters.horizon - parameters.policy.e2_turnaround_time,
            )
            returned = variables.multibatch_create_pickup_return_time(lower, o, upper)
            pickup_returns[o.id] = returned
            variables.model.addConstr(returned >= variables.task_times[o.id, "p"] + tail)
            previous = 0
            for b in parameters.policy.batches:
                if b.cutoff < lower:
                    eligible = 0
                elif b.cutoff >= upper:
                    eligible = 1
                else:
                    eligible = variables.multibatch_create_eligible(b, o)
                    # Exact integer-time classification with tight bounds.
                    # Equality belongs to this cutoff; lateness starts at +1.
                    variables.model.addConstr(
                        returned <= b.cutoff + (upper - b.cutoff) * (1 - eligible)
                    )
                    variables.model.addConstr(
                        returned >= b.cutoff + 1 - (b.cutoff + 1 - lower) * eligible
                    )
                    variables.model.addConstr(eligible >= previous)
                q_expected[o.satellite, b.id] += o.count * (eligible - previous)
                previous = eligible
    return q_expected, pickup_returns



def multibatch_add_task_and_return_coverage_constraints(
    parameters,
    variables,
    *,
    check_budget,
):
    """Cover each task once and balance first-echelon empty-container returns."""
    import gurobipy as gp

    for o in parameters.problem.orders:
        for kind in parameters.problem.task_kinds:
            for site in (
                parameters.assignment_domains[o.customer]
                if parameters.free_assignment
                else [o.satellite]
            ):
                variables.model.addConstr(
                    gp.quicksum(
                        r["visit"][i]
                        for r in variables.routes["e2"]
                        if r["satellite"] == site
                        for i, n in enumerate(r["nodes"])
                        if n["order"].id == o.id and n["kind"] == kind
                    )
                    == (
                        variables.assignments[o.customer, site] if parameters.free_assignment else 1
                    )
                )
        if parameters.problem.reusable:
            variables.model.addConstr(
                variables.task_times[o.id, "p"]
                >= variables.task_times[o.id, "d"] + o.service_time + o.min_use_time
            )
            variables.model.addConstr(
                variables.task_steps[o.id, "p"] >= variables.task_steps[o.id, "d"] + 1
            )
    for s in parameters.problem.satellites:
        for b in parameters.policy.batches:
            check_budget()
            nodes = [
                (r, i, n)
                for r in variables.routes["e1"]
                if r["batch"].id == b.id
                for i, n in enumerate(r["nodes"])
                if n["site"] == s and n["kind"] == "p"
            ]
            if parameters.single_batch:
                variables.model.addConstr(
                    gp.quicksum(r["visit"][i] for r, i, n in nodes)
                    == (
                        variables.active_sites[s]
                        if parameters.free_assignment and parameters.problem.reusable
                        else 1
                        if parameters.problem.reusable and parameters.problem.demands[s, b.id]
                        else 0
                    )
                )
            else:
                variables.model.addConstr(
                    gp.quicksum(n["quantity"] for r, i, n in nodes)
                    == variables.expected_returns[s, b.id]
                )
            variables.model.addConstr(gp.quicksum(r["visit"][i] for r, i, n in nodes) <= 1)
