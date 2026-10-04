"""Complete second-stage MILP on one fixed M3 task backbone.

Deliveries stay with their original vehicle. A pickup slot remains on its
original vehicle, but may become a relay-receive at another customer location.
Only the corresponding physical pickup/drop operations may be inserted into
another already-used vehicle's route. E1 arcs and all backbone orders are fixed.
"""
import gurobipy as gp

from models.routing.variants.model_5.model import (
    build_model as build_relay_model,
    apply_route_warm_start,
)


def build_model(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                baseline, TFp=9999.0, relay_fixes=None, handoff_fixes=None):
    # Vehicle labels now encode fixed commitments: generic vehicle symmetry
    # constraints are invalid. Keep all physical and timing constraints.
    model, variables, ctx = build_relay_model(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
        max_relocation_candidates=None, strengthen=False,
        relay_fixes=relay_fixes, handoff_fixes=handoff_fixes,
        time_horizon_floor=TFp if TFp < 9000 else 0.0,
    )
    model.ModelName = "Model8_Frozen_M3_Relay"
    e1, e2 = baseline["e1_routes"], baseline["e2_routes"]
    allowed_e1 = {(i, j, k) for k, route in enumerate(e1)
                  for i, j in zip(route, route[1:])}
    for key, var in variables["x"].items():
        var.LB = var.UB = int(key in allowed_e1)
    if TFp < 9000:
        for k, route in enumerate(e1):
            for node in route[1:-1]:
                if ctx["e1_task_map"][node].kind == "p":
                    model.addConstr(variables["A1"][node, k] >= TFp)

    owner = {n: (sat, index)
             for sat, paths in e2.items() for index, path in enumerate(paths)
             for n in path[1:-1]}
    for (i, j, vehicle), var in variables["z"].items():
        # No unused vehicle may be activated during stage two.
        if vehicle[1] >= len(e2.get(vehicle[0], [])):
            var.UB = 0
        node = ctx["e2_info"].get(j)
        if node is not None and node.kind in ("d", "p", "rp"):
            if owner[node.original_task] != vehicle:
                var.UB = 0

    # Positive topological positions prevent zero-time/collocated subtours and
    # express order directly rather than relying on strict travel durations.
    ranks = {}
    for sat, nodes in ctx["e2_nodes_by_sat"].items():
        limit = len(nodes) + 1
        for vehicle in ctx["V_by_sat"][sat]:
            for node in nodes:
                ranks[node, vehicle] = model.addVar(lb=1, ub=limit)
        for (i, j, vehicle), arc in variables["z"].items():
            if vehicle[0] == sat and i != sat and j != sat:
                model.addConstr(ranks[j, vehicle] >= ranks[i, vehicle] + 1
                                - limit * (1 - arc))
        for index, route in enumerate(e2.get(sat, [])):
            vehicle = (sat, index)
            previous = None
            for task in route[1:-1]:
                slot = model.addVar(lb=1, ub=limit)
                candidates = ctx["pickup_candidate_nodes"].get(task, [task])
                for candidate in candidates:
                    selected = variables["y"].get(candidate)
                    if selected is None:
                        model.addConstr(slot == ranks[candidate, vehicle])
                    else:
                        model.addGenConstrIndicator(
                            selected, True, slot == ranks[candidate, vehicle])
                if previous is not None:
                    model.addConstr(slot >= previous + 1)
                previous = slot
    variables["backbone_rank"] = ranks
    ctx["baseline"] = baseline
    ctx["backbone_owner"] = owner
    ctx["proof_scope"] = "conditional_on_one_frozen_m3_solution"
    apply_route_warm_start(variables, ctx, e1, e2)
    model.update()
    return model, variables, ctx
