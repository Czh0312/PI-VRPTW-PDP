"""Business constraint groups for M8."""
from __future__ import annotations

import time
from models.routing.model_8.exact.objective import add_distance_cost


def validate_strengthening(profile):
    if profile not in ('none', 'physical', 'physical_time'):
        raise ValueError('M8 strengthening must be none, physical or physical_time')
    return profile


def add_physical_bounds(parameters, variables):
    """Expose necessary stock and productive-visit inequalities to the LP.

    Each batch debits its entire demand at loading, so D_b <= N. Summing
    these inequalities and using integer N gives N >= ceil(Q/K); each
    indivisible order also needs its own count of containers simultaneously.
    Every productive batch has positive delivery and recovery quantities,
    hence at least one E1 visit of each kind, across all satellites/vehicles.
    No calendar, assignment, route or incumbent is fixed by these bounds.
    """
    import gurobipy as gp

    batches = parameters.policy.batches
    total = parameters.total_containers
    lower = max((total + len(batches) - 1) // len(batches),
                max((o.count for o in parameters.problem.orders), default=0))
    variables.pool.LB = max(parameters.problem.pool_min, lower)
    for batch in batches:
        demand = gp.quicksum(variables.demand_counts[s, batch.id]
                             for s in parameters.problem.satellites)
        variables.model.addConstr(demand <= variables.pool,
                                 name=f'physical_batch_pool_{batch.id}')
        if parameters.problem.require_productive_batches:
            for kind in ('d', 'p'):
                visits = gp.quicksum(
                    route['visit'][i]
                    for route in variables.routes['e1'] if route['batch'].id == batch.id
                    for i, node in enumerate(route['nodes']) if node['kind'] == kind
                )
                variables.model.addConstr(visits >= 1,
                                          name=f'physical_productive_visit_{batch.id}_{kind}')
    return dict(profile='physical', pool_lower_bound=lower,
                batch_pool_rows=len(batches),
                productive_visit_rows=2*len(batches) if parameters.problem.require_productive_batches else 0)


def add_physical_time_bounds(parameters, variables, statistics):
    from models.routing.model_8.exact.parameters import necessary_time_bounds
    import gurobipy as gp

    bounds = necessary_time_bounds(parameters.problem)
    variables.pool.LB = max(parameters.problem.pool_min, statistics['pool_lower_bound'], bounds['pool_lower_bound'])
    first = parameters.policy.batches[0].id
    for row in bounds['forced_first_batch']:
        variable = variables.assignments.get((row['order'], first))
        # An empty first-batch domain makes this block infeasible, rather
        # than silently discarding the implication or the original K.
        lhs = gp.LinExpr() if variable is None else variable
        variables.model.addConstr(lhs == 1, name=f'physical_time_first_{row["order"]}')
    statistics.update(profile='physical_time', time_bounds=bounds)


def add_calendar_constraints(parameters, variables):
    """Define calendar boundaries and delivery/recovery cutoffs."""
    import gurobipy as gp

    variable_calendar = getattr(parameters.problem, "variable_calendar", False)
    if variable_calendar:
        starts = {
            b.id: variables.create_batch_start(b, parameters.problem)
            for b in parameters.policy.batches
        }
        ends = {
            b.id: (
                starts[parameters.policy.batches[i + 1].id]
                if i + 1 < len(parameters.policy.batches)
                else parameters.policy.cycle_end
            )
            for i, b in enumerate(parameters.policy.batches)
        }
    else:
        starts = {b.id: b.start for b in parameters.policy.batches}
        ends = {b.id: b.end for b in parameters.policy.batches}
    if parameters.problem.shared_cutoff:
        lo, hi = parameters.problem.shared_cutoff_bounds
        offset = variables.create_shared_cutoff_offset(hi, lo)
        cutoffs = {b.id: b.start + offset for b in parameters.policy.batches}
    else:
        cutoffs = {
            b.id: (
                variables.create_batch_cutoff(b, parameters.problem)
                if parameters.problem.optimize_cutoffs
                else b.cutoff
            )
            for b in parameters.policy.batches
        }
    if variable_calendar:
        for b in parameters.policy.batches:
            variables.model.addConstr(
                cutoffs[b.id] >= starts[b.id] + parameters.problem.minimum_phase_ticks
            )
            variables.model.addConstr(
                cutoffs[b.id] <= ends[b.id] - parameters.problem.minimum_phase_ticks
            )
    step = getattr(parameters.problem, 'calendar_decision_step', 1)
    if step > 1:
        for kind, values in (('start', starts), ('cutoff', cutoffs)):
            for bid, value in values.items():
                if isinstance(value, (int, float)):
                    if value % step:
                        raise ValueError('Fixed calendar time outside the decision grid')
                else:
                    index = variables.model.addVar(vtype=gp.GRB.INTEGER,
                        lb=0, ub=parameters.policy.cycle_end//step,
                        name=f'calendar_grid_{kind}_{bid}')
                    variables.model.addConstr(value == step*index)
    return variable_calendar, starts, ends, cutoffs


def add_order_batch_constraints(
    parameters,
    variables,
    *,
    variable_calendar,
):
    """Assign orders to legal batches and link batch activation and release times."""
    import gurobipy as gp

    y = {
        (o.id, b): variables.create_order_batch_assignment(b, o)
        for o in parameters.problem.orders
        for b in parameters.problem.legal_batches[o.id]
    }
    z = {b.id: variables.create_batch_activation(b) for b in parameters.policy.batches}
    for o in parameters.problem.orders:
        variables.model.addConstr(
            gp.quicksum(y[o.id, b] for b in parameters.problem.legal_batches[o.id]) == 1
        )
        if variable_calendar:
            for bid in parameters.problem.legal_batches[o.id]:
                variables.model.addConstr(
                    (y[o.id, bid] == 1) >> (variables.calendar["starts"][bid] >= o.release_time)
                )
    for b in parameters.policy.batches:
        members = [v for (oid, bid), v in y.items() if bid == b.id]
        variables.model.addConstr(z[b.id] == 1, name=f"batch_delivery_active_{b.id}")
        for v in members:
            variables.model.addConstr(v <= z[b.id])
        variables.model.addConstr(z[b.id] <= gp.quicksum(members))
    return y, z


def add_supply_time_constraints(parameters, variables):
    """Create supply-time variables and apply their safe bounds."""
    import gurobipy as gp

    supplied = {
        (s, b.id): variables.create_supply_time(b, parameters.problem, s)
        for s in parameters.problem.satellites
        for b in parameters.policy.batches
    }
    for (s, bid), value in supplied.items():
        variables.model.addConstr(value <= variables.calendar["cutoffs"][bid])
    return supplied


def add_route_constraints(
    parameters,
    variables,
    *,
    nodes,
    base,
    layer,
    label,
    budget,
    batch=None,
):
    """Link route arcs, visits, time windows, capacities and load propagation."""
    import gurobipy as gp

    budget()
    cap_w, cap_v = (
        (parameters.fleet.e1_weight_cap, parameters.fleet.e1_volume_cap)
        if layer == "e1"
        else (parameters.fleet.e2_weight_cap, parameters.fleet.e2_volume_cap)
    )
    unload = (
        parameters.policy.depot_return_unload_time
        if layer == "e1"
        else parameters.policy.e2_return_unload_time
    )
    used = variables.create_route_used(label)
    start = variables.create_route_start(parameters.horizon, label)
    end = variables.create_route_end(parameters.horizon, label)
    visit = variables.create_route_visits(label, nodes)
    times = (
        {i: variables.task_times[n["order"].id, n["kind"]] for i, n in enumerate(nodes)}
        if layer == "e2"
        else variables.create_route_times(parameters.horizon, label, nodes)
    )
    if layer == "e2" and parameters.compact_e2:
        # Each task is visited exactly once, so its post-service load is a
        # physical task state, independent of the chosen vehicle/trip slot.
        if base not in variables.task_load_weight:
            variables.task_load_weight[base] = variables.create_route_weight(cap_w, f"e2_{base}_task", nodes)
            variables.task_load_volume[base] = variables.create_route_volume(cap_v, f"e2_{base}_task", nodes)
        lw = variables.task_load_weight[base]
        lv = variables.task_load_volume[base]
    else:
        lw = variables.create_route_weight(cap_w, label, nodes)
        lv = variables.create_route_volume(cap_v, label, nodes)
    rank = variables.create_route_rank(label, nodes) if layer == "e1" else None
    arcs = [(-1, j) for j in range(len(nodes))] + [(i, -2) for i in range(len(nodes))]
    for i, left in enumerate(nodes):
        budget()
        for j, right in enumerate(nodes):
            if i == j or (layer == "e1" and left["kind"] == "p" and right["kind"] == "d"):
                continue
            if (
                layer == "e2"
                and left["kind"] == "p"
                and right["kind"] == "d"
                and left["order"].id == right["order"].id
            ):
                continue
            if (
                left["window"][0]
                + left["service"]
                + parameters.problem.travel(left["site"], right["site"])
                > right["window"][1]
            ):
                continue
            arcs.append((i, j))
    # Internal route copies inherit integrality from the binary physical edge
    # and the unique binary visit of its tail. They need not be branch variables.
    x = variables.create_route_arcs(arcs, label, continuous_internal=layer == "e2" and parameters.compact_e2)
    incoming = {i: [] for i in range(len(nodes))}
    outgoing = {i: [] for i in range(len(nodes))}
    for a, b in arcs:
        if b >= 0:
            incoming[b].append(x[a, b])
        if a >= 0:
            outgoing[a].append(x[a, b])
    variables.model.addConstr(start <= parameters.horizon * used)
    variables.model.addConstr(end <= parameters.horizon * used)
    variables.model.addConstr(start >= parameters.policy.cycle_start * used)
    variables.model.addConstr(gp.quicksum(x[a, b] for a, b in arcs if a == -1) == used)
    variables.model.addConstr(gp.quicksum(x[a, b] for a, b in arcs if b == -2) == used)
    initial_w = gp.quicksum(
        n["w"] * visit[i] if layer == "e2" else n["w"]
        for i, n in enumerate(nodes)
        if n["kind"] == "d"
    )
    initial_v = gp.quicksum(
        n["v"] * visit[i] if layer == "e2" else n["v"]
        for i, n in enumerate(nodes)
        if n["kind"] == "d"
    )
    variables.model.addConstr(initial_w <= cap_w * used)
    variables.model.addConstr(initial_v <= cap_v * used)
    for i, n in enumerate(nodes):
        variables.model.addConstr(gp.quicksum(incoming[i]) == visit[i])
        variables.model.addConstr(gp.quicksum(outgoing[i]) == visit[i])
        variables.model.addConstr(visit[i] <= used)
        if layer == "e1" or not parameters.compact_e2:
            variables.model.addConstr((visit[i] == 1) >> (times[i] >= n["window"][0]))
            variables.model.addConstr((visit[i] == 1) >> (times[i] <= n["window"][1]))
        if layer == "e1":
            variables.model.addConstr(times[i] <= parameters.horizon * visit[i])
            variables.model.addConstr(n["quantity"] <= parameters.total_containers * visit[i])
            variables.model.addConstr(n["quantity"] >= visit[i])
            if n["kind"] == "d":
                variables.model.addConstr(
                    (visit[i] == 1)
                    >> (times[i] + n["service"] <= variables.calendar["cutoffs"][batch.id])
                )
                variables.model.addConstr(n["w"] <= cap_w * visit[i])
                variables.model.addConstr(
                    (visit[i] == 1)
                    >> (n["quantity"] == variables.demand_counts[n["site"], batch.id])
                )
                variables.model.addConstr(
                    (visit[i] == 1) >> (n["w"] == variables.demand_weights[n["site"], batch.id])
                )
                variables.model.addConstr(
                    (visit[i] == 1)
                    >> (variables.supply_times[n["site"], batch.id] == times[i] + n["service"])
                )
            else:
                variables.model.addConstr(
                    (visit[i] == 1) >> (times[i] >= variables.calendar["cutoffs"][batch.id])
                )
                variables.model.addConstr(
                    (visit[i] == 1)
                    >> (end + parameters.policy.cleaning_duration <= parameters.horizon)
                )
    distance = gp.LinExpr()
    travel_ticks = gp.LinExpr()
    for a, b in arcs:
        left = base if a == -1 else nodes[a]["site"]
        right = base if b == -2 else nodes[b]["site"]
        distance = add_distance_cost(distance, parameters.problem, left, right, x[a, b])
        travel_ticks += parameters.problem.travel(left, right) * x[a, b]
        if a == -1:
            variables.model.addConstr(
                (x[a, b] == 1) >> (times[b] >= start + parameters.problem.travel(left, right))
            )
            if rank is not None:
                variables.model.addConstr((x[a, b] == 1) >> (rank[b] >= 1))
            sign = -1 if nodes[b]["kind"] == "d" else 1
            variables.model.addConstr((x[a, b] == 1) >> (lw[b] == initial_w + sign * nodes[b]["w"]))
            variables.model.addConstr((x[a, b] == 1) >> (lv[b] == initial_v + sign * nodes[b]["v"]))
        elif b == -2:
            variables.model.addConstr(
                (x[a, b] == 1)
                >> (
                    end
                    >= times[a]
                    + nodes[a]["service"]
                    + parameters.problem.travel(left, right)
                    + unload
                )
            )
        elif layer == "e2" and parameters.compact_e2:
            # Task coverage implies sum of these route copies is binary.
            # One indicator per physical adjacency replaces one per trip.
            key = (base, a, b)
            if key not in variables.aggregate_arcs:
                edge = variables.create_only_pick()
                variables.global_arcs[key] = edge
                variables.aggregate_arcs[key] = []
                variables.model.addConstr((edge == 1) >> (
                    times[b] >= times[a] + nodes[a]["service"]
                    + parameters.problem.travel(left, right)))
                sign = -1 if nodes[b]["kind"] == "d" else 1
                variables.model.addConstr((edge == 1) >> (lw[b] == lw[a] + sign * nodes[b]["w"]))
                variables.model.addConstr((edge == 1) >> (lv[b] == lv[a] + sign * nodes[b]["v"]))
                before = variables.task_steps[nodes[a]["order"].id, nodes[a]["kind"]]
                after = variables.task_steps[nodes[b]["order"].id, nodes[b]["kind"]]
                variables.model.addConstr((edge == 1) >> (after >= before + 1))
            variables.aggregate_arcs[key].append(x[a, b])
        else:
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
            variables.model.addConstr((x[a, b] == 1) >> (lw[b] == lw[a] + sign * nodes[b]["w"]))
            variables.model.addConstr((x[a, b] == 1) >> (lv[b] == lv[a] + sign * nodes[b]["v"]))
        if (
            layer == "e1"
            and b != -2
            and nodes[b]["kind"] == "p"
            and (a == -1 or nodes[a]["kind"] == "d")
        ):
            variables.model.addConstr(
                (x[a, b] == 1)
                >> (
                    times[b]
                    >= variables.calendar["cutoffs"][batch.id]
                    + parameters.problem.travel(left, right)
                )
            )
    # Every physical trip includes its traversed arcs, service and
    # unloading. Expose this implication in the LP relaxation too:
    # indicator-only timing otherwise permits negative paid duration.
    variables.model.addConstr(
        end - start
        >= travel_ticks
        + gp.quicksum(n["service"] * visit[i] for i, n in enumerate(nodes))
        + unload * used,
        name=label + "_duration_lower_bound",
    )
    return dict(
        nodes=nodes,
        base=base,
        used=used,
        start=start,
        end=end,
        visit=visit,
        times=times,
        x=x,
        label=label,
        distance=distance,
    )


def add_first_echelon_constraints(
    parameters,
    variables,
    *,
    make_route,
    variable_calendar,
):
    """Build first-echelon trips and link loading, recovery and physical vehicles."""
    import gurobipy as gp

    for b in parameters.policy.batches:
        for k in range(parameters.fleet.e1_count):
            nodes = []
            for s in parameters.problem.satellites:
                for kind in ("d", "p"):
                    q = variables.create_quantity(parameters.total_containers, b, k, kind, s)
                    w = (
                        variables.create_goods_weight(b, parameters.fleet, k, s)
                        if kind == "d"
                        else q * parameters.fleet.container_weight
                    )
                    nodes.append(
                        dict(
                            site=s,
                            kind=kind,
                            quantity=q,
                            w=w,
                            v=q * parameters.fleet.container_volume,
                            service=parameters.problem.service(s),
                            window=(
                                (
                                    parameters.problem.start_bounds[b.id][0]
                                    if variable_calendar
                                    else b.start
                                ),
                                parameters.problem.cutoff_bounds[b.id][1]
                                - parameters.problem.service(s),
                            )
                            if kind == "d"
                            else (
                                parameters.problem.cutoff_bounds[b.id][0],
                                parameters.problem.end_bounds[b.id][1]
                                if variable_calendar
                                else b.end,
                            ),
                        )
                    )
            r = make_route(nodes, parameters.problem.depot, "e1", f"e1_{b.id}_{k}", b)
            r.update(batch=b, vehicle=k)
            hd = variables.create_hd(r)
            ds = [r["visit"][i] for i, n in enumerate(nodes) if n["kind"] == "d"]
            for v in ds:
                variables.model.addConstr(hd >= v)
            variables.model.addConstr(hd <= gp.quicksum(ds))
            only_pick = variables.create_only_pick()
            variables.model.addConstr(only_pick == r["used"] - hd)
            variables.model.addConstr(
                (hd == 1)
                >> (
                    r["start"]
                    == variables.calendar["starts"][b.id] + parameters.policy.e1_load_time
                )
            )
            variables.model.addConstr(
                (only_pick == 1) >> (r["start"] >= variables.calendar["cutoffs"][b.id])
            )
            variables.model.addConstr(
                (r["used"] == 1) >> (r["end"] <= variables.calendar["ends"][b.id])
            )
            variables.model.addConstr(
                (r["used"] == 1)
                >> (r["end"] + parameters.policy.e1_turnaround_time <= parameters.horizon)
            )
            r["loaded"] = hd
            r["return_quantity"] = gp.quicksum(n["quantity"] for n in nodes if n["kind"] == "p")
            r["work_ticks"] = r["end"] - r["start"] + parameters.policy.e1_load_time * hd
            for prior in variables.routes["e1"]:
                if prior["vehicle"] == k:
                    both = variables.create_only_pick()
                    variables.model.addConstr(both <= prior["used"])
                    variables.model.addConstr(both <= r["used"])
                    variables.model.addConstr(both >= prior["used"] + r["used"] - 1)
                    variables.model.addConstr(
                        (both == 1)
                        >> (
                            r["start"] - parameters.policy.e1_load_time * hd
                            >= prior["end"] + parameters.policy.e1_turnaround_time
                        )
                    )
            variables.routes["e1"].append(r)


def add_supply_coverage_constraints(parameters, variables):
    """Cover each satellite's inbound demand with first-echelon trips."""
    import gurobipy as gp

    for s in parameters.problem.satellites:
        for b in parameters.policy.batches:
            entries = [
                (r, i, n)
                for r in variables.routes["e1"]
                if r["batch"].id == b.id
                for i, n in enumerate(r["nodes"])
                if n["site"] == s and n["kind"] == "d"
            ]
            variables.model.addConstr(
                gp.quicksum(n["quantity"] for r, i, n in entries)
                == variables.demand_counts[s, b.id]
            )
            variables.model.addConstr(gp.quicksum(r["visit"][i] for r, i, n in entries) <= 1)


def add_cutoff_quantity_constraints(parameters, variables):
    """Assign empty-container quantities to the first eligible recovery cutoff."""
    import gurobipy as gp

    returned = {
        o.id: variables.create_pickup_return_time(o, parameters.policy, parameters.problem)
        for o in parameters.problem.orders
    }
    q_expected = {
        (s, b.id): gp.LinExpr()
        for s in parameters.problem.satellites
        for b in parameters.policy.batches
    }
    for o in parameters.problem.orders:
        previous = 0
        for b in parameters.policy.batches:
            h = variables.create_h(b, o)
            variables.model.addConstr(
                (h == 1) >> (returned[o.id] <= variables.calendar["cutoffs"][b.id])
            )
            variables.model.addConstr(
                (h == 0) >> (returned[o.id] >= variables.calendar["cutoffs"][b.id] + 1)
            )
            variables.model.addConstr(h >= previous)
            q_expected[o.satellite, b.id] += o.count * (h - previous)
            previous = h
        variables.model.addConstr(previous == 1)
    return returned, q_expected


def add_second_echelon_constraints(
    parameters,
    variables,
    *,
    causal_limit,
    make_route,
    vehicle_symmetry=False,
):
    """Build second-echelon trips, supply synchronization and vehicle calendars."""
    import gurobipy as gp

    symmetry_rows = 0
    for s in parameters.problem.satellites:
        local = [o for o in parameters.problem.orders if o.satellite == s]
        if not local:
            continue
        nodes = [
            dict(
                site=o.customer,
                order=o,
                kind=kind,
                quantity=o.count,
                w=parameters.problem.task(o.id, kind)[1],
                v=parameters.problem.task(o.id, kind)[2],
                service=o.service_time,
                window=o.delivery_window if kind == "d" else o.pickup_window,
            )
            for o in local
            for kind in ("d", "p")
        ]
        first_slots = []
        first_keys = []
        for k in range(parameters.fleet.e2_per_sat_count):
            previous = None
            for slot in range(min(parameters.policy.e2_max_trips_per_vehicle, len(nodes))):
                r = make_route(nodes, s, "e2", f"e2_{s}_{k}_{slot}")
                r.update(satellite=s, vehicle=k, slot=slot)
                if slot == 0:
                    if first_slots:
                        variables.model.addConstr(r["used"] <= first_slots[-1])
                    if vehicle_symmetry:
                        key = gp.quicksum((i+1)*r['x'][-1, i] for i in range(len(nodes)))
                        if first_keys:
                            # Rename complete homogeneous vehicle schedules by
                            # their first trip's first task. Unique tasks make
                            # these keys distinct; no physical schedule is lost.
                            variables.model.addConstr(
                                first_keys[-1]+r['used'] <= key+len(nodes)*(1-r['used']),
                                name=f'e2_first_task_order_{s}_{k}')
                            symmetry_rows += 1
                        first_keys.append(key)
                    first_slots.append(r["used"])
                selects = {b.id: variables.create_only_pick() for b in parameters.policy.batches}
                loaded = gp.quicksum(selects.values())
                variables.model.addConstr(loaded <= r["used"])
                r["loaded"] = loaded
                r["work_ticks"] = (
                    r["end"] - r["start"] + parameters.policy.e2_dispatch_prep_time * loaded
                )
                ss = variables.create_ss(causal_limit)
                se = variables.create_ss(causal_limit)
                variables.model.addConstr(ss <= causal_limit * r["used"])
                variables.model.addConstr(se <= causal_limit * r["used"])
                r["step_end"] = se
                variables.model.addConstr(
                    (r["used"] == 1)
                    >> (r["end"] + parameters.policy.e2_turnaround_time <= parameters.horizon)
                )
                variables.model.addConstr(
                    (r["used"] == 1)
                    >> (
                        r["start"]
                        >= parameters.policy.cycle_start
                        + parameters.policy.e2_dispatch_prep_time * loaded
                    )
                )
                if previous is not None:
                    variables.model.addConstr(r["used"] <= previous["used"])
                    variables.model.addConstr(
                        (r["used"] == 1)
                        >> (
                            r["start"]
                            >= previous["end"]
                            + parameters.policy.e2_turnaround_time
                            + parameters.policy.e2_dispatch_prep_time * loaded
                        )
                    )
                    variables.model.addConstr((r["used"] == 1) >> (ss >= previous["step_end"] + 1))
                if parameters.compact_e2:
                    deliveries = [r["visit"][i] for i, n in enumerate(nodes) if n["kind"] == "d"]
                    variables.model.addConstr(loaded <= gp.quicksum(deliveries))
                    for b in parameters.policy.batches:
                        for i, n in enumerate(nodes):
                            if n["kind"] != "d" or (n["order"].id, b.id) not in variables.assignments:
                                continue
                            variables.model.addConstr(
                                selects[b.id] >= r["visit"][i] + variables.assignments[n["order"].id, b.id] - 1
                            )
                        variables.model.addConstr(
                            (selects[b.id] == 1)
                            >> (
                                r["start"]
                                >= variables.supply_times[s, b.id]
                                + parameters.policy.e2_dispatch_prep_time
                            )
                        )
                else:
                    for b in parameters.policy.batches:
                        conjunctions = []
                        for i, n in enumerate(nodes):
                            if n["kind"] != "d" or (n["order"].id, b.id) not in variables.assignments:
                                continue
                            a = variables.create_only_pick()
                            variables.model.addConstr(a <= r["visit"][i])
                            variables.model.addConstr(a <= variables.assignments[n["order"].id, b.id])
                            variables.model.addConstr(
                                a >= r["visit"][i] + variables.assignments[n["order"].id, b.id] - 1
                            )
                            variables.model.addConstr(a <= selects[b.id])
                            conjunctions.append(a)
                        variables.model.addConstr(selects[b.id] <= gp.quicksum(conjunctions))
                        variables.model.addConstr(
                            (selects[b.id] == 1)
                            >> (
                                r["start"]
                                >= variables.supply_times[s, b.id]
                                + parameters.policy.e2_dispatch_prep_time
                            )
                        )
                for i, n in enumerate(nodes):
                    if n["kind"] == "p":
                        variables.model.addConstr(
                            (r["visit"][i] == 1)
                            >> (variables.pickup_returns[n["order"].id] == r["end"])
                        )
                for (a, b), x in r["x"].items():
                    if parameters.compact_e2 and a >= 0 and b >= 0:
                        continue  # Shared task adjacency already enforces causality.
                    left = (
                        ss
                        if a == -1
                        else variables.task_steps[nodes[a]["order"].id, nodes[a]["kind"]]
                    )
                    right = (
                        se
                        if b == -2
                        else variables.task_steps[nodes[b]["order"].id, nodes[b]["kind"]]
                    )
                    variables.model.addConstr((x == 1) >> (right >= left + 1))
                variables.routes["e2"].append(r)
                previous = r
    for key, copies in variables.aggregate_arcs.items():
        variables.model.addConstr(variables.global_arcs[key] == gp.quicksum(copies))
    return symmetry_rows


def add_task_and_return_coverage_constraints(parameters, variables):
    """Cover each task once and balance first-echelon empty-container returns."""
    import gurobipy as gp

    for b in parameters.policy.batches:
        variables.model.addConstr(
            gp.quicksum(variables.expected_returns[s, b.id] for s in parameters.problem.satellites) >= 1,
            name=f"batch_recovery_positive_{b.id}",
        )

    for o in parameters.problem.orders:
        for kind in ("d", "p"):
            variables.model.addConstr(
                gp.quicksum(
                    r["visit"][i]
                    for r in variables.routes["e2"]
                    for i, n in enumerate(r["nodes"])
                    if n["order"].id == o.id and n["kind"] == kind
                )
                == 1
            )
        variables.model.addConstr(
            variables.task_times[o.id, "p"]
            >= variables.task_times[o.id, "d"] + o.service_time + o.min_use_time
        )
        variables.model.addConstr(
            variables.task_steps[o.id, "p"] >= variables.task_steps[o.id, "d"] + 1
        )
    for s in parameters.problem.satellites:
        for b in parameters.policy.batches:
            entries = [
                (r, i, n)
                for r in variables.routes["e1"]
                if r["batch"].id == b.id
                for i, n in enumerate(r["nodes"])
                if n["site"] == s and n["kind"] == "p"
            ]
            variables.model.addConstr(
                gp.quicksum(n["quantity"] for r, i, n in entries)
                == variables.expected_returns[s, b.id]
            )
            variables.model.addConstr(gp.quicksum(r["visit"][i] for r, i, n in entries) <= 1)


def add_inventory_constraints(
    parameters,
    variables,
    *,
    budget,
):
    """Require clean containers to be available at every batch loading time."""
    import gurobipy as gp

    cumulative = gp.LinExpr()
    for b in parameters.policy.batches:
        budget()
        cumulative += gp.quicksum(
            variables.demand_counts[s, b.id] for s in parameters.problem.satellites
        )
        releases = []
        for r in variables.routes["e1"]:
            ready, late = (variables.create_only_pick() for _ in range(2))
            variables.model.addConstr(ready + late == r["used"])
            variables.model.addConstr(
                (ready == 1)
                >> (
                    r["end"] + parameters.policy.cleaning_duration
                    <= variables.calendar["starts"][b.id]
                )
            )
            variables.model.addConstr(
                (late == 1)
                >> (
                    r["end"] + parameters.policy.cleaning_duration
                    >= variables.calendar["starts"][b.id] + 1
                )
            )
            released = variables.create_released(parameters.total_containers)
            variables.model.addConstr(released <= r["return_quantity"])
            variables.model.addConstr(released <= parameters.total_containers * ready)
            variables.model.addConstr(
                released >= r["return_quantity"] - parameters.total_containers * (1 - ready)
            )
            releases.append(released)
        variables.model.addConstr(
            variables.pool + gp.quicksum(releases) >= cumulative, name=f"clean_stock_{b.id}"
        )


def add_vehicle_activation_constraints(parameters, variables):
    """Link physical-vehicle activation to all of that vehicle's trips."""
    import gurobipy as gp

    physical_vehicles = {}
    for layer in ("e1", "e2"):
        groups = {}
        for r in variables.routes[layer]:
            key = r["vehicle"] if layer == "e1" else (r["satellite"], r["vehicle"])
            groups.setdefault(key, []).append(r["used"])
        enabled = []
        for key, visits in groups.items():
            active = variables.create_active(key, layer)
            for v in visits:
                variables.model.addConstr(active >= v)
            variables.model.addConstr(active <= gp.quicksum(visits))
            enabled.append(active)
        physical_vehicles[layer] = enabled
    return physical_vehicles
