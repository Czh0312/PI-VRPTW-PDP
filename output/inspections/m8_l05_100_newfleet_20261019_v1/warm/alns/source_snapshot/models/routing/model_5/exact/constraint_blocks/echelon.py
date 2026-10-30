"""M5 echelon constraints; preserve the caller-owned model and variable handles."""
from __future__ import annotations



def multibatch_add_first_echelon_constraints(
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
                if parameters.free_assignment:
                    q = variables.multibatch_create_quantity(parameters.total_containers)
                    w = variables.multibatch_create_goods_weight(parameters.fleet)
                    nodes.append(
                        dict(
                            site=s,
                            kind="d",
                            quantity=q,
                            w=w,
                            v=q * parameters.fleet.container_volume,
                            service=parameters.problem.service(s),
                            window=(b.start, b.cutoff - parameters.problem.service(s)),
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
                            window=(b.start, b.cutoff - parameters.problem.service(s)),
                        )
                    )
                if parameters.problem.reusable and (
                    not parameters.single_batch or n or parameters.free_assignment
                ):
                    # B=1: all orders must be unloaded by its sole cutoff, so
                    # every satellite's pickup quantity is known in advance.
                    q = (
                        n
                        if parameters.single_batch and not parameters.free_assignment
                        else variables.multibatch_create_quantity_2(
                            b, k, s, parameters.total_containers
                        )
                    )
                    nodes.append(
                        dict(
                            site=s,
                            kind="p",
                            quantity=q,
                            w=q * parameters.fleet.container_weight,
                            v=q * parameters.fleet.container_volume,
                            service=parameters.problem.service(s),
                            window=(b.cutoff, b.end),
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
            r["has_delivery"] = variables.multibatch_create_r()
            dvis = [r["visit"][i] for i, n in enumerate(nodes) if n["kind"] == "d"]
            for v in dvis:
                variables.model.addConstr(r["has_delivery"] >= v)
            variables.model.addConstr(r["has_delivery"] <= gp.quicksum(dvis))
            only_pick = variables.multibatch_create_r()
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
                n["quantity"] * r["visit"][i]
                if parameters.single_batch and not parameters.free_assignment
                else n["quantity"]
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
            for previous in variables.routes["e1"]:
                if previous["vehicle"] == k:
                    both = variables.multibatch_create_r()
                    variables.model.addConstr(both <= previous["used"])
                    variables.model.addConstr(both <= r["used"])
                    variables.model.addConstr(both >= previous["used"] + r["used"] - 1)
                    # Loading is occupied time; pickup-only trips have no loading.
                    variables.model.addConstr(
                        (both == 1)
                        >> (
                            r["start"] - parameters.policy.e1_load_time * r["has_delivery"]
                            >= previous["end"] + parameters.policy.e1_turnaround_time
                        )
                    )
            variables.routes["e1"].append(r)
            if parameters.single_batch and k:
                variables.model.addConstr(r["used"] <= variables.routes["e1"][-2]["used"])



def multibatch_add_second_echelon_constraints(
    parameters,
    variables,
    *,
    causal_limit,
    e2_nodes,
    route,
    trip_symmetry,
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
        task_count = len(orders) * len(parameters.problem.task_kinds)
        for v in range(
            min(parameters.fleet.e2_per_sat_count, len(orders) * len(parameters.problem.task_kinds))
        ):
            previous = None
            used_slots = []
            slot_limit = min(
                parameters.policy.e2_max_trips_per_vehicle,
                task_count // (v + 1) if trip_symmetry else task_count,
            )
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
                r["step_start"] = variables.multibatch_create_task_step(causal_limit)
                r["step_end"] = variables.multibatch_create_task_step(causal_limit)
                if parameters.linear_e2_links:
                    variables.model.addConstr(
                        r["end"]
                        <= (parameters.horizon - parameters.policy.e2_turnaround_time) * r["used"]
                    )
                else:
                    variables.model.addConstr(
                        (r["used"] == 1)
                        >> (r["end"] + parameters.policy.e2_turnaround_time <= parameters.horizon)
                    )
                previous_route = previous
                if previous_route is not None:
                    variables.model.addConstr(r["used"] <= previous["used"])
                    if parameters.linear_e2_links:
                        variables.model.addConstr(
                            r["step_start"]
                            >= previous["step_end"] + 1 - (causal_limit + 1) * (1 - r["used"])
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
                # Calendar order makes variable creation independent of the
                # interpreter hash seed, which matters in timed comparisons.
                present_batches = {o.batch for o in orders}
                batches = [b.id for b in parameters.policy.batches if b.id in present_batches]
                selects = {b: variables.multibatch_create_r() for b in batches}
                variables.model.addConstr(
                    gp.quicksum(selects.values())
                    <= ((1))
                )
                # Dispatch preparation for a loaded trip occupies its vehicle.
                # It starts after both inbound goods and the previous trip are ready.
                if previous_route is not None:
                    occupied = (
                        previous_route["end"]
                        + parameters.policy.e2_turnaround_time
                        + parameters.policy.e2_dispatch_prep_time * gp.quicksum(selects.values())
                    )
                    if parameters.linear_e2_links:
                        # When unused, all batch selectors are zero, and the
                        # previous route returns by H-turnaround.
                        variables.model.addConstr(
                            r["start"] >= occupied - parameters.horizon * (1 - r["used"])
                        )
                    else:
                        variables.model.addConstr((r["used"] == 1) >> (r["start"] >= occupied))
                for b in batches:
                    dvis = [
                        r["visit"][i]
                        for i, n in enumerate(nodes)
                        if n["kind"] == "d" and n["order"].batch == b
                    ]
                    variables.model.addConstr(selects[b] <= gp.quicksum(dvis))
                    if parameters.linear_e2_links:
                        # selects[b] is exactly the OR of this batch's delivery
                        # visits. One supply link replaces one per order copy.
                        latest = (
                            parameters.problem.batch_map[b].cutoff
                            + parameters.policy.e2_dispatch_prep_time
                        )
                        variables.model.addConstr(
                            r["start"]
                            >= variables.supply_times[s, b]
                            + parameters.policy.e2_dispatch_prep_time
                            - latest * (1 - selects[b])
                        )
                for i, n in enumerate(nodes):
                    o = n["order"]
                    if not parameters.share_e2:
                        variables.model.addConstr(
                            (r["visit"][i] == 1)
                            >> (variables.task_times[o.id, n["kind"]] == r["times"][i])
                        )
                    if n["kind"] == "d":
                        variables.model.addConstr(r["visit"][i] <= selects[o.batch])
                        if not parameters.linear_e2_links:
                            variables.model.addConstr(
                                (r["visit"][i] == 1)
                                >> (
                                    r["start"]
                                    >= variables.supply_times[s, o.batch]
                                    + parameters.policy.e2_dispatch_prep_time
                                )
                            )
                        if len(parameters.policy.batches) == 1:
                            variables.model.addConstr(
                                (r["visit"][i] == 1)
                                >> (
                                    r["times"][i] + o.service_time
                                    <= parameters.policy.batches[0].cutoff
                                )
                            )
                    elif parameters.single_batch:
                        variables.model.addConstr(
                            (r["visit"][i] == 1)
                            >> (r["end"] <= parameters.policy.batches[0].cutoff)
                        )
                    elif parameters.share_cutoff:
                        if parameters.linear_e2_links:
                            lower = (
                                parameters.task_windows[o.id, "p"][0]
                                + o.service_time
                                + parameters.travel_bounds["inward"][s][o.customer]
                                + parameters.policy.e2_return_unload_time
                            )
                            upper = min(
                                parameters.policy.batches[-1].cutoff,
                                parameters.horizon - parameters.policy.e2_turnaround_time,
                            )
                            variables.model.addConstr(
                                variables.pickup_returns[o.id]
                                >= r["end"] - (parameters.horizon - lower) * (1 - r["visit"][i])
                            )
                            variables.model.addConstr(
                                variables.pickup_returns[o.id]
                                <= r["end"] + upper * (1 - r["visit"][i])
                            )
                        else:
                            variables.model.addConstr(
                                (r["visit"][i] == 1) >> (variables.pickup_returns[o.id] == r["end"])
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
                    if parameters.linear_e2_links:
                        variables.model.addConstr(
                            right >= left + 1 - (causal_limit + 1) * (1 - arc)
                        )
                    else:
                        variables.model.addConstr((arc == 1) >> (right >= left + 1))
                prior_h = 0
                gs = []
                for b in (
                    ()
                    if parameters.single_batch or parameters.share_cutoff
                    else parameters.policy.batches
                ):
                    h, late, g = (variables.multibatch_create_r() for _ in range(3))
                    variables.model.addConstr(late == r["used"] - h)
                    variables.model.addConstr((h == 1) >> (r["end"] <= b.cutoff))
                    variables.model.addConstr((late == 1) >> (r["end"] >= b.cutoff + 1))
                    variables.model.addConstr(g == h - prior_h)
                    prior_h = h
                    gs.append((b, g))
                for i, n in enumerate(nodes):
                    if n["kind"] != "p":
                        continue
                    if parameters.single_batch or parameters.share_cutoff:
                        continue
                    allocated = []
                    for b, g in gs:
                        w = variables.multibatch_create_r()
                        variables.model.addConstr(w <= r["visit"][i])
                        variables.model.addConstr(w <= g)
                        variables.model.addConstr(w >= r["visit"][i] + g - 1)
                        variables.expected_returns[s, b.id] += n["quantity"] * w
                        allocated.append(w)
                    variables.model.addConstr(gp.quicksum(allocated) == r["visit"][i])
                variables.routes["e2"].append(r)
            if trip_symmetry and vehicle_trips:
                variables.model.addConstr(gp.quicksum(used_slots) <= gp.quicksum(vehicle_trips[-1]))
            vehicle_trips.append(used_slots)
