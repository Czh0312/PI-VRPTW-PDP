"""M5 strengthening constraints; preserve the caller-owned model and variable handles."""
from __future__ import annotations



def multibatch_add_distance_certificate_constraints(
    parameters,
    variables,
    *,
    _distance_certificate,
    check_budget,
    component_costs,
    deadline,
    objective,
):
    """Add valid whole-cycle or component distance lower bounds."""
    import gurobipy as gp

    certificate_rows = {}
    if parameters.distance_cut_mode != "none":
        check_budget()
        certificate = _distance_certificate
        if certificate is None:
            from models.routing.model_5.exact.bounds.distance_certificate import distance_certificate

            try:
                certificate = distance_certificate(parameters.problem, deadline=deadline)
            except TimeoutError:
                variables.model.dispose()
                raise
        if certificate.get("scope") == "full_fixed_M5_cycle_distance":
            if parameters.distance_cut_mode == "total" and certificate["bound"] > 0:
                certificate_rows["total"] = variables.model.addConstr(
                    objective >= certificate["bound"], "m5_distance_total"
                )
            elif parameters.distance_cut_mode == "components":
                for row in certificate["components"]:
                    check_budget()
                    key = (
                        row["echelon"],
                        row["batch"] if row["echelon"] == "E1" else row["satellite"],
                    )
                    bound = max(0.0, row["bound"] - 1e-7 * max(1.0, row["bound"]))
                    if bound > 0:
                        component_costs.setdefault(key, gp.LinExpr())
                        certificate_rows[key] = variables.model.addConstr(
                            component_costs[key] >= bound, f"m5_distance_{key[0]}_{key[1]}"
                        )
    return certificate_rows
