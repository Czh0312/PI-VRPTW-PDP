"""Business constraint groups for M3."""
from __future__ import annotations

import time
from models.routing.model_3.exact.preprocessing.model_bounds import trip_slot_limits
from models.routing.model_3.exact.objective import add_distance_cost


def add_assignment_constraints(parameters, variables):
    """Assign each customer to one satellite and link satellite activation and capacity."""
    import gurobipy as gp

    assignments, active_sites, counts, weights = {}, {}, {}, {}
    if parameters.free_assignment:
        assignments = {
            (c, s): variables.create_customer_assignment(c, s)
            for c, sites in parameters.assignment_domains.items()
            for s in sites
        }
        for c, sites in parameters.assignment_domains.items():
            variables.model.addConstr(gp.quicksum(assignments[c, s] for s in sites) == 1)
        for s in parameters.problem.satellites:
            active_sites[s] = variables.create_satellite_activation(s)
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


def add_supply_time_constraints(parameters, variables):
    """Create supply-time variables and apply their safe bounds."""
    import gurobipy as gp

    deliveries = {
        (s, b.id): variables.create_supply_time(parameters.horizon, b, s)
        for (s, bid), n in parameters.problem.demands.items()
        for b in parameters.policy.batches
        if bid == b.id and (n or parameters.free_assignment)
    }
    for (s, bid), variable in deliveries.items():
        if s not in parameters.bounds["supply"]:
            variable.UB = 0
            continue
        lower, upper = parameters.bounds["supply"][s]
        variable.UB = max(0, upper)
        if parameters.free_assignment:
            variables.model.addConstr(variable >= lower * variables.active_sites[s])
            variables.model.addConstr(variable <= upper * variables.active_sites[s])
            for oid, due in parameters.bounds["dues"][s].items():
                variables.model.addConstr(
                    (variables.assignments[parameters.problem.order_map[oid].customer, s] == 1)
                    >> (variable <= due)
                )
        else:
            variable.LB = lower
    return deliveries


def add_route_constraints(
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
    compressed,
    e2_arc_cache,
    factor_loads,
    linear_boundary,
    linear_schedule,
    objective,
    route_arcs,
    batch=None,
    capacity_links=False,
    resource_flows=False,
):
    """Link route arcs, visits, time windows, capacities and load propagation."""
    import gurobipy as gp

    check_budget()
    count = len(nodes)
    used = variables.create_route_used(label)
    start = variables.create_route_start(parameters.horizon, label)
    end = variables.create_route_end(parameters.horizon, label)
    visit = variables.create_route_visits(count, label)
    shared = parameters.share_e2 and layer == "e2"
    keys = [(n["order"].id, n["kind"]) for n in nodes] if shared else []
    times = (
        {i: variables.task_times[key] for i, key in enumerate(keys)}
        if shared
        else variables.create_route_times(parameters.horizon, count, label)
    )
    # E2 already has strictly increasing global causal steps below.
    rank = variables.create_route_rank(count, label) if layer == "e1" else None
    load_w = (
        {i: variables.task_load_weight[key] for i, key in enumerate(keys)}
        if shared
        else variables.create_load_w(cap_w, count, label)
    )
    load_v = (
        {i: variables.task_load_volume[key] for i, key in enumerate(keys)}
        if shared
        else variables.create_load_v(cap_v, count, label)
    )
    # -1 = source, -2 = sink. There is no empty source-to-sink route.
    end_limit = (
        min(parameters.horizon - parameters.policy.e1_turnaround_time, batch.end)
        if layer == "e1"
        else parameters.horizon - parameters.policy.e2_turnaround_time
    )
    if layer == "e2" and base in e2_arc_cache:
        arcs = e2_arc_cache[base]
    else:
        arcs = route_arcs(parameters.problem, nodes, base, layer, batch, check_budget)
        if layer == "e2":
            e2_arc_cache[base] = arcs
    if shared and compressed:
        x = {}
        for a, b in arcs:
            if a >= 0 and b >= 0:
                key = keys[a], keys[b]
                if key not in variables.global_arcs:
                    variables.global_arcs[key] = variables.create_global_arcs(variables.global_arcs)
                    variables.shared_arcs[key] = [variables.global_arcs[key]]
                x[a, b] = variables.global_arcs[key]
            else:
                x[a, b] = variables.create_route_arcs(a, b, label)
    else:
        x = variables.create_route_arcs_2(arcs, label)
    incoming, outgoing = {i: [] for i in range(count)}, {i: [] for i in range(count)}
    for a, b in arcs:
        if b >= 0:
            incoming[b].append(x[a, b])
        if a >= 0:
            outgoing[a].append(x[a, b])
    variables.model.addConstr(start <= parameters.horizon * used)
    variables.model.addConstr(end <= parameters.horizon * used)
    end.UB = max(0, end_limit)
    if shared and linear_schedule:
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
    if shared and factor_loads:
        # Keep the dense delivery sum in one equality per resource. Each
        # source arc then has a sparse load link rather than expanding
        # the same delivery membership sum twice for every task.
        departure_w = variables.create_departure_w(cap_w, label)
        departure_v = variables.create_departure_v(cap_v, label)
        variables.model.addConstr(departure_w == initial_w)
        variables.model.addConstr(departure_v == initial_v)
        initial_w, initial_v = departure_w, departure_v
        departure_loads = departure_w, departure_v
    else:
        variables.model.addConstr(initial_w <= cap_w)
        variables.model.addConstr(initial_v <= cap_v)
    if capacity_links:
        # These hold for every original binary route at any instance size.
        # They prevent a fractionally enabled trip from carrying a full load.
        variables.model.addConstr(initial_w <= cap_w*used)
        variables.model.addConstr(initial_v <= cap_v*used)
        returned_w = gp.quicksum(n['w']*visit[i] for i, n in enumerate(nodes) if n['kind'] == 'p')
        returned_v = gp.quicksum(n['v']*visit[i] for i, n in enumerate(nodes) if n['kind'] == 'p')
        variables.model.addConstr(returned_w <= cap_w*used)
        variables.model.addConstr(returned_v <= cap_v*used)
    for i, n in enumerate(nodes):
        if i % 64 == 0:
            check_budget()
        if not (shared and compressed):
            variables.model.addConstr(gp.quicksum(incoming[i]) == visit[i])
            variables.model.addConstr(gp.quicksum(outgoing[i]) == visit[i])
        else:
            if (-1, i) in x:
                variables.model.addConstr(x[-1, i] <= visit[i])
            if (i, -2) in x:
                variables.model.addConstr(x[i, -2] <= visit[i])
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
        if layer == "e1":
            times[i].UB = max(0, n["window"][1])
            variables.model.addConstr(times[i] >= n["window"][0] * visit[i])
            variables.model.addConstr(times[i] <= n["window"][1] * visit[i])
        elif not shared or parameters.free_assignment:
            if shared and linear_boundary:
                lower, upper = parameters.task_windows[keys[i]]
                variables.model.addConstr(
                    times[i] >= n["window"][0] - max(0, n["window"][0] - lower) * (1 - visit[i])
                )
                variables.model.addConstr(
                    times[i] <= n["window"][1] + max(0, upper - n["window"][1]) * (1 - visit[i])
                )
            else:
                variables.model.addConstr((visit[i] == 1) >> (times[i] >= n["window"][0]))
                variables.model.addConstr((visit[i] == 1) >> (times[i] <= n["window"][1]))
    for arc_index, (a, b) in enumerate(arcs):
        if arc_index % 64 == 0:
            check_budget()
        left = base if a == -1 else nodes[a]["site"]
        right = base if b == -2 else nodes[b]["site"]
        if not (shared and compressed and a >= 0 and b >= 0):
            objective = add_distance_cost(objective, parameters.problem, left, right, x[a, b])
        if a == -1:
            travel = parameters.problem.travel(left, right)
            if shared and linear_boundary:
                slack = max(0, parameters.horizon + travel - parameters.task_windows[keys[b]][0])
                variables.model.addConstr(times[b] >= start + travel - slack * (1 - x[a, b]))
            else:
                variables.model.addConstr((x[a, b] == 1) >> (times[b] >= start + travel))
            if rank is not None:
                variables.model.addConstr((x[a, b] == 1) >> (rank[b] == 1))
            sign = -1 if nodes[b]["kind"] == "d" else 1
            for load, initial, amount, capacity, offset in (
                (load_w[b], initial_w, nodes[b]["w"], cap_w, 0),
                (load_v[b], initial_v, nodes[b]["v"], cap_v, 2),
            ):
                if resource_flows and layer == 'e2':
                    continue
                if shared and linear_boundary:
                    lower, upper = parameters.bounds["loads"][keys[b]][offset : offset + 2]
                    variables.model.addConstr(
                        load
                        >= initial
                        + sign * amount
                        - max(0, capacity + sign * amount - lower) * (1 - x[a, b])
                    )
                    variables.model.addConstr(
                        load
                        <= initial + sign * amount + max(0, upper - sign * amount) * (1 - x[a, b])
                    )
                else:
                    variables.model.addConstr((x[a, b] == 1) >> (load == initial + sign * amount))
        elif b == -2:
            tail = nodes[a]["service"] + parameters.problem.travel(left, right) + unload
            if shared and linear_boundary:
                slack = max(0, parameters.task_windows[keys[a]][1] + tail)
                variables.model.addConstr(end >= times[a] + tail - slack * (1 - x[a, b]))
            else:
                variables.model.addConstr((x[a, b] == 1) >> (end >= times[a] + tail))
        else:
            if shared:
                if not compressed:
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
            if not (resource_flows and layer == 'e2'):
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
            global_internal_arcs=shared and compressed,
            departure_loads=departure_loads,
            visit=visit,
            times=times,
            x=x,
            label=label,
        ),
        objective,
    )


def add_first_echelon_constraints(
    parameters,
    variables,
    *,
    route,
):
    """Build first-echelon trips and link loading, recovery and physical vehicles."""
    import gurobipy as gp

    for b in parameters.policy.batches:
        for k in range(parameters.fleet.e1_count):
            nodes = []
            for s in parameters.problem.satellites:
                n = parameters.problem.demands[s, b.id]
                if parameters.free_assignment and s not in parameters.bounds["supply"]:
                    continue
                delivery_window = (
                    parameters.bounds["visits"][s, "d"]
                    if n or parameters.free_assignment
                    else (b.start, b.cutoff - parameters.problem.service(s))
                )
                if parameters.free_assignment:
                    q = variables.create_quantity(s, parameters.bounds)
                    w = variables.create_goods_weight(parameters.fleet)
                    nodes.append(
                        dict(
                            site=s,
                            kind="d",
                            quantity=q,
                            w=w,
                            v=q * parameters.fleet.container_volume,
                            service=parameters.problem.service(s),
                            window=delivery_window,
                        )
                    )
                elif n:
                    nodes.append(
                        dict(
                            site=s,
                            kind="d",
                            quantity=n,
                            w=parameters.problem.weights[s, b.id],
                            v=n * parameters.fleet.container_volume,
                            service=parameters.problem.service(s),
                            window=delivery_window,
                        )
                    )
                if parameters.problem.reusable and (n or parameters.free_assignment):
                    # B=1: all orders must be unloaded by its sole cutoff, so
                    # every satellite's pickup quantity is known in advance.
                    q = (
                        n
                        if not parameters.free_assignment
                        else variables.create_quantity_2(b, k, s, parameters.bounds)
                    )
                    nodes.append(
                        dict(
                            site=s,
                            kind="p",
                            quantity=q,
                            w=q * parameters.fleet.container_weight,
                            v=q * parameters.fleet.container_volume,
                            service=parameters.problem.service(s),
                            window=parameters.bounds["visits"][s, "p"],
                        )
                    )
            r = route(
                nodes,
                parameters.problem.depot,
                parameters.fleet.e1_weight_cap,
                parameters.fleet.e1_volume_cap,
                parameters.policy.depot_return_unload_time,
                f"e1_{b.id}_{k}",
                "e1",
                b,
            )
            r.update(batch=b, vehicle=k)
            r["has_delivery"] = variables.create_r()
            dvis = [r["visit"][i] for i, n in enumerate(nodes) if n["kind"] == "d"]
            for v in dvis:
                variables.model.addConstr(r["has_delivery"] >= v)
            variables.model.addConstr(r["has_delivery"] <= gp.quicksum(dvis))
            only_pick = variables.create_r()
            variables.model.addConstr(only_pick == r["used"] - r["has_delivery"])
            variables.model.addConstr(
                (r["has_delivery"] == 1) >> (r["start"] == b.start + parameters.policy.e1_load_time)
            )
            variables.model.addConstr((only_pick == 1) >> (r["start"] >= b.cutoff))
            variables.model.addConstr((r["used"] == 1) >> (r["end"] <= b.end))
            variables.model.addConstr(
                (r["used"] == 1)
                >> (r["end"] + parameters.policy.e1_turnaround_time <= parameters.horizon)
            )
            r["quantity"] = gp.quicksum(
                n["quantity"] * r["visit"][i] if not parameters.free_assignment else n["quantity"]
                for i, n in enumerate(nodes)
                if n["kind"] == "p"
            )
            for i, n in enumerate(nodes):
                if n["kind"] == "d":
                    variables.model.addConstr(
                        (r["visit"][i] == 1)
                        >> (variables.supply_times[n["site"], b.id] == r["times"][i] + n["service"])
                    )
                else:
                    variables.model.addConstr(
                        (r["visit"][i] == 1)
                        >> (r["end"] + parameters.policy.cleaning_duration <= parameters.horizon)
                    )
            variables.routes["e1"].append(r)
            if k:
                variables.model.addConstr(r["used"] <= variables.routes["e1"][-2]["used"])


def add_supply_coverage_constraints(parameters, variables):
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


def add_second_echelon_constraints(
    parameters,
    variables,
    *,
    causal_limit,
    e2_nodes,
    linear_boundary,
    linear_schedule,
    route,
    step_slack,
    task_step_bounds,
):
    """Build second-echelon trips, supply synchronization and vehicle calendars."""
    import gurobipy as gp

    for s in parameters.problem.satellites:
        orders = [
            o
            for o in parameters.problem.orders
            if (
                s in parameters.assignment_domains[o.customer]
                if parameters.free_assignment
                else o.satellite == s
            )
        ]
        if not orders:
            continue
        first_slots = []
        vehicle_trips = []
        for v, slot_limit in enumerate(
            trip_slot_limits(parameters.problem, len(orders) * len(parameters.problem.task_kinds))
        ):
            previous = None
            used_slots = []
            for slot in range(slot_limit):
                nodes = e2_nodes[s]
                r = route(
                    nodes,
                    s,
                    parameters.fleet.e2_weight_cap,
                    parameters.fleet.e2_volume_cap,
                    parameters.policy.e2_return_unload_time,
                    f"e2_{s}_{v}_{slot}",
                    "e2",
                )
                r.update(satellite=s, vehicle=v, slot=slot)
                used_slots.append(r["used"])
                if slot == 0:
                    if first_slots:
                        variables.model.addConstr(r["used"] <= first_slots[-1])
                    first_slots.append(r["used"])
                # Used trip k has k nonempty predecessors. Each consumes
                # start -> >=1 task -> end -> next start: at least 3 steps.
                # Unused step variables may take these bounds too, because
                # they have no selected head/tail and their inactive links
                # use the recomputed endpoint slack below.
                r["step_start_bounds"] = (
                    ((0, causal_limit))
                )
                r["step_end_bounds"] = (
                    ((0, causal_limit))
                )
                r["step_start"] = variables.create_r_2(r)
                r["step_end"] = variables.create_r_3(r)
                if not linear_schedule or parameters.horizon < parameters.policy.e2_turnaround_time:
                    variables.model.addConstr(
                        (r["used"] == 1)
                        >> (r["end"] + parameters.policy.e2_turnaround_time <= parameters.horizon)
                    )
                previous_route = previous
                if previous_route is not None:
                    variables.model.addConstr(r["used"] <= previous["used"])
                    if linear_schedule:
                        variables.model.addConstr(
                            r["step_start"]
                            >= previous["step_end"]
                            + 1
                            - step_slack(previous["step_end_bounds"], r["step_start_bounds"])
                            * (1 - r["used"])
                        )
                    else:
                        variables.model.addConstr(
                            (r["used"] == 1)
                            >> (
                                r["start"] >= previous["end"] + parameters.policy.e2_turnaround_time
                            )
                        )
                        variables.model.addConstr(
                            (r["used"] == 1) >> (r["step_start"] >= previous["step_end"] + 1)
                        )
                previous = r
                batches = {o.batch for o in orders}
                selects = {b: variables.create_r() for b in batches}
                r['has_delivery'] = gp.quicksum(selects.values())
                variables.model.addConstr(gp.quicksum(selects.values()) <= 1)
                # Dispatch preparation for a loaded trip occupies its vehicle.
                # It starts after both inbound goods and the previous trip are ready.
                if previous_route is not None:
                    if linear_schedule:
                        slack = (
                            max(0, parameters.horizon - parameters.policy.e2_turnaround_time)
                            + parameters.policy.e2_turnaround_time
                            + parameters.policy.e2_dispatch_prep_time
                        )
                        variables.model.addConstr(
                            r["start"]
                            >= previous_route["end"]
                            + parameters.policy.e2_turnaround_time
                            + parameters.policy.e2_dispatch_prep_time
                            * gp.quicksum(selects.values())
                            - slack * (1 - r["used"])
                        )
                    else:
                        variables.model.addConstr(
                            (r["used"] == 1)
                            >> (
                                r["start"]
                                >= previous_route["end"]
                                + parameters.policy.e2_turnaround_time
                                + parameters.policy.e2_dispatch_prep_time
                                * gp.quicksum(selects.values())
                            )
                        )
                for b in batches:
                    dvis = [
                        r["visit"][i]
                        for i, n in enumerate(nodes)
                        if n["kind"] == "d" and n["order"].batch == b
                    ]
                    variables.model.addConstr(selects[b] <= gp.quicksum(dvis))
                for i, n in enumerate(nodes):
                    o = n["order"]
                    if not parameters.share_e2:
                        variables.model.addConstr(
                            (r["visit"][i] == 1)
                            >> (variables.task_times[o.id, n["kind"]] == r["times"][i])
                        )
                    if n["kind"] == "d":
                        variables.model.addConstr(r["visit"][i] <= selects[o.batch])
                        if linear_schedule:
                            slack = (
                                max(0, parameters.bounds["supply"][s][1])
                                + parameters.policy.e2_dispatch_prep_time
                            )
                            variables.model.addConstr(
                                r["start"]
                                >= variables.supply_times[s, o.batch]
                                + parameters.policy.e2_dispatch_prep_time
                                - slack * (1 - r["visit"][i])
                            )
                            # The mandatory shared time's UB already includes
                            # cutoff-service for every possible assignment.
                        else:
                            variables.model.addConstr(
                                (r["visit"][i] == 1)
                                >> (
                                    r["start"]
                                    >= variables.supply_times[s, o.batch]
                                    + parameters.policy.e2_dispatch_prep_time
                                )
                            )
                            variables.model.addConstr(
                                (r["visit"][i] == 1)
                                >> (
                                    r["times"][i] + o.service_time
                                    <= parameters.policy.batches[0].cutoff
                                )
                            )
                    else:
                        if linear_schedule:
                            slack = max(
                                0,
                                parameters.horizon
                                - parameters.policy.e2_turnaround_time
                                - parameters.policy.batches[0].cutoff,
                            )
                            variables.model.addConstr(
                                r["end"]
                                <= parameters.policy.batches[0].cutoff + slack * (1 - r["visit"][i])
                            )
                        else:
                            variables.model.addConstr(
                                (r["visit"][i] == 1)
                                >> (r["end"] <= parameters.policy.batches[0].cutoff)
                            )
                for (i, j), arc in r["x"].items():
                    if parameters.share_e2 and i >= 0 and j >= 0:
                        continue
                    left = (
                        r["step_start"]
                        if i == -1
                        else variables.task_steps[nodes[i]["order"].id, nodes[i]["kind"]]
                    )
                    right = (
                        r["step_end"]
                        if j == -2
                        else variables.task_steps[nodes[j]["order"].id, nodes[j]["kind"]]
                    )
                    if linear_boundary:
                        left_bounds = (
                            r["step_start_bounds"]
                            if i == -1
                            else task_step_bounds[nodes[i]["order"].id, nodes[i]["kind"]]
                        )
                        right_bounds = (
                            r["step_end_bounds"]
                            if j == -2
                            else task_step_bounds[nodes[j]["order"].id, nodes[j]["kind"]]
                        )
                        variables.model.addConstr(
                            right >= left + 1 - step_slack(left_bounds, right_bounds) * (1 - arc)
                        )
                    else:
                        variables.model.addConstr((arc == 1) >> (right >= left + 1))
                variables.routes["e2"].append(r)
            if vehicle_trips:
                variables.model.addConstr(gp.quicksum(used_slots) <= gp.quicksum(vehicle_trips[-1]))
            vehicle_trips.append(used_slots)


def add_shared_route_constraints(
    parameters,
    variables,
    *,
    check_budget,
    compressed,
    e2_arc_cache,
    e2_nodes,
    linear_identity,
    linear_internal,
    objective,
    step_slack,
    task_step_bounds,
    resource_flows=False,
):
    """Link shared task arcs and route identities, times, loads and causal steps."""
    import gurobipy as gp

    identities, site_arcs = {}, {}
    if parameters.share_e2:
        for key, variable in variables.task_times.items():
            variable.LB, variable.UB = parameters.task_windows[key]
        if compressed:
            # Every task has exactly one visit across all route copies. Route
            # identity is consequently integral without an extra integer var.
            identities = {
                key: variables.create_identities(key, variables.routes["e2"])
                for key in variables.task_times
            }
            entering = {key: [] for key in variables.task_times}
            leaving = {key: [] for key in variables.task_times}
            memberships = {key: [] for key in variables.task_times}
            available_routes = {key: [] for key in variables.task_times}
            for route_id, r in enumerate(variables.routes["e2"], 1):
                for i, n in enumerate(r["nodes"]):
                    key = n["order"].id, n["kind"]
                    memberships[key].append(route_id * r["visit"][i])
                    available_routes[key].append(route_id)
                    if (-1, i) in r["x"]:
                        entering[key].append(r["x"][-1, i])
                    if (i, -2) in r["x"]:
                        leaving[key].append(r["x"][i, -2])
            identity_bounds = {
                key: ((1, len(variables.routes["e2"])))
                for key, ids in available_routes.items()
            }
            for key, (lower, upper) in identity_bounds.items():
                identities[key].LB, identities[key].UB = lower, upper
            for (left, right), arc in variables.global_arcs.items():
                leaving[left].append(arc)
                entering[right].append(arc)
                if linear_identity:
                    variables.model.addConstr(
                        identities[left] - identities[right]
                        <= max(0, identity_bounds[left][1] - identity_bounds[right][0]) * (1 - arc)
                    )
                    variables.model.addConstr(
                        identities[right] - identities[left]
                        <= max(0, identity_bounds[right][1] - identity_bounds[left][0]) * (1 - arc)
                    )
                else:
                    variables.model.addConstr((arc == 1) >> (identities[left] == identities[right]))
            for key in variables.task_times:
                check_budget()
                variables.model.addConstr(identities[key] == gp.quicksum(memberships[key]))
                variables.model.addConstr(gp.quicksum(entering[key]) == 1)
                variables.model.addConstr(gp.quicksum(leaving[key]) == 1)
        for (left, right), indexed in variables.shared_arcs.items():
            check_budget()
            if compressed:
                active = variables.global_arcs[left, right]
                objective = add_distance_cost(
                    objective,
                    parameters.problem,
                    parameters.problem.order_map[left[0]].customer,
                    parameters.problem.order_map[right[0]].customer,
                    active,
                )
            else:
                active = variables.create_r()
                variables.model.addConstr(active == gp.quicksum(indexed))
            variables.aggregate_arcs[left, right] = active
            a, b = parameters.problem.order_map[left[0]], parameters.problem.order_map[right[0]]
            travel = a.service_time + parameters.problem.travel(a.customer, b.customer)
            if linear_internal:
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
            for loads, amount, offset in (
                (variables.task_load_weight, weight, 0),
                (variables.task_load_volume, volume, 2),
            ):
                if resource_flows:
                    continue
                if linear_internal:
                    lo_left, hi_left = parameters.bounds["loads"][left][offset : offset + 2]
                    lo_right, hi_right = parameters.bounds["loads"][right][offset : offset + 2]
                    variables.model.addConstr(
                        loads[right]
                        >= loads[left]
                        + sign * amount
                        - max(0, hi_left + sign * amount - lo_right) * (1 - active)
                    )
                    variables.model.addConstr(
                        loads[right]
                        <= loads[left]
                        + sign * amount
                        + max(0, hi_right - lo_left - sign * amount) * (1 - active)
                    )
                else:
                    variables.model.addConstr(
                        (active == 1) >> (loads[right] == loads[left] + sign * amount)
                    )
            if linear_internal:
                variables.model.addConstr(
                    variables.task_steps[right]
                    >= variables.task_steps[left]
                    + 1
                    - step_slack(task_step_bounds[left], task_step_bounds[right]) * (1 - active)
                )
            else:
                variables.model.addConstr(
                    (active == 1) >> (variables.task_steps[right] >= variables.task_steps[left] + 1)
                )
    return objective, identities, site_arcs


def add_task_and_return_coverage_constraints(
    parameters,
    variables,
    *,
    check_budget,
):
    """Cover each task once and balance first-echelon empty-container returns."""
    import gurobipy as gp

    # Cache coverage once instead of scanning every route's complete node list
    # again for each order, task kind and eligible warehouse.
    coverage = {}
    for r in variables.routes["e2"]:
        check_budget()
        for i, n in enumerate(r["nodes"]):
            coverage.setdefault((r["satellite"], n["order"].id, n["kind"]), []).append(
                r["visit"][i]
            )
    for o in parameters.problem.orders:
        for kind in parameters.problem.task_kinds:
            for site in (
                parameters.assignment_domains[o.customer]
                if parameters.free_assignment
                else [o.satellite]
            ):
                variables.model.addConstr(
                    gp.quicksum(coverage.get((site, o.id, kind), ()))
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
            variables.model.addConstr(gp.quicksum(r["visit"][i] for r, i, n in nodes) <= 1)


def add_inventory_constraints(parameters, variables):
    """Require clean containers to be available at every batch loading time."""
    import gurobipy as gp

    cumulative = 0
    for b in parameters.policy.batches if parameters.problem.reusable else ():
        cumulative += parameters.problem.batch_demands[b.id]
        variables.model.addConstr(
            gp.LinExpr(parameters.policy.container_pool_size) >= cumulative, f"clean_stock_{b.id}"
        )
