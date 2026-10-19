"""M5 routing constraints; preserve the caller-owned model and variable handles."""
from __future__ import annotations

from models.routing.model_5.exact.objective import add_distance_term


def multibatch_add_route_constraints(
    parameters,
    variables,
    *,
    nodes,
    base,
    cap_w,
    cap_v,
    unload,
    label,
    layer,
    check_budget,
    component_costs,
    compressed_routes,
    factor_e2_loads,
    indexed_internal,
    objective,
    route_arcs,
    batch=None,
):
    """Link route arcs, visits, time windows, capacities and load propagation."""
    import gurobipy as gp

    check_budget()
    count = len(nodes)
    used = variables.multibatch_create_route_used(label)
    start = variables.multibatch_create_route_start(parameters.horizon, label)
    end = variables.multibatch_create_route_end(parameters.horizon, label)
    visit = variables.multibatch_create_route_visits(count, label)
    shared = parameters.share_e2 and layer == "e2"
    keys = [(n["order"].id, n["kind"]) for n in nodes] if shared else []
    times = (
        {i: variables.task_times[key] for i, key in enumerate(keys)}
        if shared
        else variables.multibatch_create_route_times(parameters.horizon, count, label)
    )
    # E2 already has strictly increasing global causal steps below.
    rank = variables.multibatch_create_route_rank(count, label) if layer == "e1" else None
    load_w = (
        {i: variables.task_load_weight[key] for i, key in enumerate(keys)}
        if shared
        else variables.multibatch_create_load_w(cap_w, count, label)
    )
    load_v = (
        {i: variables.task_load_volume[key] for i, key in enumerate(keys)}
        if shared
        else variables.multibatch_create_load_v(cap_v, count, label)
    )
    arcs = route_arcs(nodes, base, cap_w, cap_v, unload, layer, batch)
    if layer == "e2":
        indexed_internal += sum(a >= 0 and b >= 0 for a, b in arcs)
    if shared and compressed_routes:
        x = {}
        for a, b in arcs:
            if a >= 0 and b >= 0:
                key = keys[a], keys[b]
                if key not in variables.global_arcs:
                    variables.global_arcs[key] = variables.multibatch_create_global_arcs(
                        variables.global_arcs
                    )
                    variables.shared_arcs[key] = [variables.global_arcs[key]]
                x[a, b] = variables.global_arcs[key]
            else:
                x[a, b] = variables.multibatch_create_route_arcs(a, b, label)
    else:
        x = variables.multibatch_create_route_arcs_2(arcs, label)
    incoming, outgoing = {i: [] for i in range(count)}, {i: [] for i in range(count)}
    for a, b in arcs:
        if b >= 0:
            incoming[b].append(x[a, b])
        if a >= 0:
            outgoing[a].append(x[a, b])
    variables.model.addConstr(start <= parameters.horizon * used)
    variables.model.addConstr(end <= parameters.horizon * used)
    if shared and parameters.linear_e2_links:
        variables.model.addConstr(start >= parameters.policy.cycle_start * used)
    else:
        variables.model.addConstr((used == 1) >> (start >= parameters.policy.cycle_start))
    variables.model.addConstr(gp.quicksum(x[a, b] for a, b in arcs if a == -1) == used)
    variables.model.addConstr(gp.quicksum(x[a, b] for a, b in arcs if b == -2) == used)
    initial_w = gp.quicksum(
        n["w"] if parameters.free_assignment and layer == "e1" else n["w"] * visit[i]
        for i, n in enumerate(nodes)
        if n["kind"] == "d"
    )
    initial_v = gp.quicksum(
        n["v"] if parameters.free_assignment and layer == "e1" else n["v"] * visit[i]
        for i, n in enumerate(nodes)
        if n["kind"] == "d"
    )
    departure_loads = ()
    if layer == "e2" and factor_e2_loads:
        # Reusing the sum in every source-arc propagation row creates a
        # dense matrix. Define it once; projection preserves even the LP.
        departure_loads = (
            variables.multibatch_create_departure_loads(cap_w, label),
            variables.multibatch_create_departure_loads_2(cap_v, label),
        )
        variables.model.addConstr(departure_loads[0] == initial_w)
        variables.model.addConstr(departure_loads[1] == initial_v)
        initial_w, initial_v = departure_loads
    variables.model.addConstr(
        initial_w <= cap_w * ((1))
    )
    variables.model.addConstr(
        initial_v <= cap_v * ((1))
    )
    for i, n in enumerate(nodes):
        if i % 64 == 0:
            check_budget()
        if shared and compressed_routes:
            if (-1, i) in x:
                variables.model.addConstr(x[-1, i] <= visit[i])
            if (i, -2) in x:
                variables.model.addConstr(x[i, -2] <= visit[i])
        else:
            variables.model.addConstr(gp.quicksum(incoming[i]) == visit[i])
            variables.model.addConstr(gp.quicksum(outgoing[i]) == visit[i])
        variables.model.addConstr(visit[i] <= used)
        if parameters.free_assignment and layer == "e1":
            # Quantity and weight belong to the unique vehicle visiting
            # this satellite; linearize assignment totals without products.
            variables.model.addConstr(visit[i] <= variables.active_sites[n["site"]])
            variables.model.addConstr(n["quantity"] <= parameters.total_containers * visit[i])
            variables.model.addConstr(
                (visit[i] == 1) >> (n["quantity"] == variables.demand_counts[n["site"]])
            )
            if n["kind"] == "d":
                variables.model.addConstr(n["w"] <= parameters.fleet.e1_weight_cap * visit[i])
                variables.model.addConstr(
                    (visit[i] == 1) >> (n["w"] == variables.demand_weights[n["site"]])
                )
        if not shared:
            variables.model.addConstr(times[i] <= parameters.horizon * visit[i])
        if n["kind"] == "p" and layer == "e1" and not parameters.single_batch:
            variables.model.addConstr(n["quantity"] <= parameters.total_containers * visit[i])
            variables.model.addConstr(n["quantity"] >= visit[i])
        if not shared or parameters.free_assignment:
            variables.model.addConstr((visit[i] == 1) >> (times[i] >= n["window"][0]))
            variables.model.addConstr((visit[i] == 1) >> (times[i] <= n["window"][1]))
    for arc_index, (a, b) in enumerate(arcs):
        if arc_index % 64 == 0:
            check_budget()
        left = base if a == -1 else nodes[a]["site"]
        right = base if b == -2 else nodes[b]["site"]
        if not (shared and compressed_routes and a >= 0 and b >= 0):
            term = parameters.problem.distance(left, right) * x[a, b]
            objective = add_distance_term(objective, term)
        if a == -1:
            if shared and parameters.linear_e2_links:
                lower = nodes[b]["window"][0]
                travel = parameters.problem.travel(left, right)
                variables.model.addConstr(
                    times[b]
                    >= start + travel - (parameters.horizon + travel - lower) * (1 - x[a, b])
                )
            else:
                variables.model.addConstr(
                    (x[a, b] == 1) >> (times[b] >= start + parameters.problem.travel(left, right))
                )
            if rank is not None:
                variables.model.addConstr((x[a, b] == 1) >> (rank[b] == 1))
            sign = -1 if nodes[b]["kind"] == "d" else 1
            for load, initial, amount, capacity in (
                (load_w[b], initial_w, nodes[b]["w"], cap_w),
                (load_v[b], initial_v, nodes[b]["v"], cap_v),
            ):
                if shared and parameters.linear_e2_links:
                    lower = (0)
                    upper = (
                        (capacity)
                    )
                    variables.model.addConstr(
                        load
                        >= initial
                        + sign * amount
                        - (capacity + sign * amount - lower) * (1 - x[a, b])
                    )
                    variables.model.addConstr(
                        load <= initial + sign * amount + (upper - sign * amount) * (1 - x[a, b])
                    )
                else:
                    variables.model.addConstr((x[a, b] == 1) >> (load == initial + sign * amount))
        elif b == -2:
            tail = nodes[a]["service"] + parameters.problem.travel(left, right) + unload
            if shared and parameters.linear_e2_links:
                variables.model.addConstr(
                    end >= times[a] + tail - (nodes[a]["window"][1] + tail) * (1 - x[a, b])
                )
            else:
                variables.model.addConstr((x[a, b] == 1) >> (end >= times[a] + tail))
        else:
            if shared:
                if not compressed_routes:
                    variables.shared_arcs.setdefault((keys[a], keys[b]), []).append(x[a, b])
                continue
            variables.model.addConstr(
                (x[a, b] == 1)
                >> (
                    times[b]
                    >= times[a] + nodes[a]["service"] + parameters.problem.travel(left, right)
                )
            )
            if rank is not None:
                variables.model.addConstr((x[a, b] == 1) >> (rank[b] >= rank[a] + 1))
            sign = -1 if nodes[b]["kind"] == "d" else 1
            variables.model.addConstr(
                (x[a, b] == 1) >> (load_w[b] == load_w[a] + sign * nodes[b]["w"])
            )
            variables.model.addConstr(
                (x[a, b] == 1) >> (load_v[b] == load_v[a] + sign * nodes[b]["v"])
            )
        if (
            layer == "e1"
            and b != -2
            and nodes[b]["kind"] == "p"
            and (a == -1 or nodes[a]["kind"] == "d")
        ):
            variables.model.addConstr(
                (x[a, b] == 1)
                >> (times[b] >= batch.cutoff + parameters.problem.travel(left, right))
            )
    return (
        dict(
            nodes=nodes,
            base=base,
            used=used,
            start=start,
            end=end,
            global_internal_arcs=shared and compressed_routes,
            departure_loads=departure_loads,
            visit=visit,
            times=times,
            x=x,
            label=label,
        ),
        objective,
        indexed_internal,
    )



def multibatch_add_shared_route_constraints(
    parameters,
    variables,
    *,
    causal_limit,
    check_budget,
    component_costs,
    compressed_routes,
    objective,
):
    """Link shared task arcs and route identities, times, loads and causal steps."""
    import gurobipy as gp

    if parameters.share_e2:
        for key, variable in variables.task_times.items():
            variable.LB, variable.UB = parameters.task_windows[key]
        if compressed_routes:
            memberships = {key: [] for key in variables.task_times}
            entering = {key: [] for key in variables.task_times}
            leaving = {key: [] for key in variables.task_times}
            identities, identity_limits = {}, {}
            for site in parameters.problem.satellites:
                local_routes = [r for r in variables.routes["e2"] if r["satellite"] == site]
                for route_id, r in enumerate(local_routes, 1):
                    for i, n in enumerate(r["nodes"]):
                        key = n["order"].id, n["kind"]
                        if key not in identities:
                            identities[key] = variables.multibatch_create_identities(
                                identities, local_routes
                            )
                            identity_limits[key] = len(local_routes) - 1
                        memberships[key].append(route_id * r["visit"][i])
                        if (-1, i) in r["x"]:
                            entering[key].append(r["x"][-1, i])
                        if (i, -2) in r["x"]:
                            leaving[key].append(r["x"][i, -2])
            for (left, right), arc in variables.global_arcs.items():
                check_budget()
                leaving[left].append(arc)
                entering[right].append(arc)
                # Exactly one binary visit makes each continuous identity
                # equal its unique local route number in any integer solution.
                slack = identity_limits[left]
                variables.model.addConstr(identities[left] - identities[right] <= slack * (1 - arc))
                variables.model.addConstr(identities[right] - identities[left] <= slack * (1 - arc))
            for key in variables.task_times:
                check_budget()
                variables.model.addConstr(identities[key] == gp.quicksum(memberships[key]))
                variables.model.addConstr(gp.quicksum(entering[key]) == 1)
                variables.model.addConstr(gp.quicksum(leaving[key]) == 1)
        for (left, right), indexed in variables.shared_arcs.items():
            check_budget()
            active = (
                variables.global_arcs[left, right]
                if compressed_routes
                else variables.multibatch_create_r()
            )
            variables.aggregate_arcs[left, right] = active
            if compressed_routes:
                term = (
                    parameters.problem.distance(
                        parameters.problem.order_map[left[0]].customer,
                        parameters.problem.order_map[right[0]].customer,
                    )
                    * active
                )
                objective = add_distance_term(objective, term)
            else:
                variables.model.addConstr(active == gp.quicksum(indexed))
            a, b = parameters.problem.order_map[left[0]], parameters.problem.order_map[right[0]]
            travel = a.service_time + parameters.problem.travel(a.customer, b.customer)
            if parameters.linear_e2_links:
                slack = max(
                    0, parameters.task_windows[left][1] + travel - parameters.task_windows[right][0]
                )
                variables.model.addConstr(
                    variables.task_times[right]
                    >= variables.task_times[left] + travel - slack * (1 - active)
                )
            else:
                variables.model.addConstr(
                    (active == 1)
                    >> (variables.task_times[right] >= variables.task_times[left] + travel)
                )
            _, weight, volume = parameters.problem.task(*right)
            sign = -1 if right[1] == "d" else 1
            for loads, resource, amount, capacity in (
                (variables.task_load_weight, 1, weight, parameters.fleet.e2_weight_cap),
                (variables.task_load_volume, 2, volume, parameters.fleet.e2_volume_cap),
            ):
                if parameters.linear_e2_links:
                    left_amount = parameters.problem.task(*left)[resource]
                    lo_left = (
                        (0)
                    )
                    hi_left = (
                        (capacity)
                    )
                    lo_right = (0)
                    hi_right = (
                        (capacity)
                    )
                    lower_slack = max(0, hi_left + sign * amount - lo_right)
                    upper_slack = max(0, hi_right - lo_left - sign * amount)
                    variables.model.addConstr(
                        loads[right] >= loads[left] + sign * amount - lower_slack * (1 - active)
                    )
                    variables.model.addConstr(
                        loads[right] <= loads[left] + sign * amount + upper_slack * (1 - active)
                    )
                else:
                    variables.model.addConstr(
                        (active == 1) >> (loads[right] == loads[left] + sign * amount)
                    )
            if parameters.linear_e2_links:
                variables.model.addConstr(
                    variables.task_steps[right]
                    >= variables.task_steps[left] + 1 - (causal_limit + 1) * (1 - active)
                )
            else:
                variables.model.addConstr(
                    (active == 1) >> (variables.task_steps[right] >= variables.task_steps[left] + 1)
                )
    return objective
