"""Alternative starting schedule for orders spread across a long static cycle."""
from models.routing.model_1.alns.evaluator import _e1_trip, route_distance


def reassigned_state(problem, result, deadline, *, scorer=None):
    """Keep unaffected routes when customer ownership changes, then repair.

    A projected state is only a proposal: new inbound quantities can make old
    routes infeasible, so the fixed solver must decode and audit it afresh.
    """
    from models.routing.model_1.alns.neighborhoods import state_from_result

    return repair_assignment_state(
        problem, state_from_result(problem, result), deadline, scorer=scorer
    )


def repair_assignment_state(problem, original, deadline, *, scorer=None):
    """Project complete search tasks, even when the current schedule is infeasible.

    Work from the search state rather than a partial decoder result. Customer
    reassignment must preserve every order and both delivery/pickup tasks.
    """
    from models.routing.model_1.alns.evaluator import _e1_trip, route_distance
    from models.routing.model_1.alns.neighborhoods import clone_state, repair_orders

    state = clone_state(original)
    moved = {
        oid
        for route in state["e2"]
        for oid, _ in route["tasks"]
        if problem.order_map[oid].satellite != route["satellite"]
    }
    for batch in problem.operating_policy.batches:
        rows = state["deliveries"][batch.id]
        for row in rows:
            row[:] = [s for s in row if problem.demands[s, batch.id]]
        existing = {s for row in rows for s in row}
        for site in problem.satellites:
            if not problem.demands[site, batch.id] or site in existing:
                continue
            choices = []
            for vehicle, row in enumerate(rows):
                for pos in range(len(row) + 1):
                    candidate = row[:pos] + [site] + row[pos:]
                    trip, issues = _e1_trip(
                        problem,
                        batch,
                        vehicle,
                        candidate,
                        [],
                        {},
                        problem.operating_policy.cycle_start,
                    )
                    choices.append((len(issues), route_distance(problem, trip, "e1"), vehicle, pos))
            _, _, vehicle, pos = min(choices)
            rows[vehicle].insert(pos, site)
    return repair_orders(
        problem, state, [problem.order_map[oid] for oid in sorted(moved)], deadline, scorer=scorer
    )


def chronological_state(problem, rng):
    state = initial_state(problem, rng)
    fleet = problem.fleet
    # Dedicated initial E1 routes avoid visiting a distant satellite before
    # an early delivery. ALNS may subsequently combine or reassign them.
    if fleet.e1_count >= len(problem.satellites):
        for batch in problem.operating_policy.batches:
            state["deliveries"][batch.id] = [[] for _ in range(fleet.e1_count)]
            for vehicle, site in enumerate(problem.satellites):
                if problem.demands[site, batch.id]:
                    state["deliveries"][batch.id][vehicle] = [site]
                state["pickup_vehicle"][batch.id][site] = vehicle
    state["e2"] = []
    for site in problem.satellites:
        orders = sorted(
            (o for o in problem.orders if o.satellite == site),
            key=lambda o: (
                o.pickup_window[0] if problem.reusable else o.delivery_window[0],
                o.delivery_window[0],
                o.id,
            ),
        )
        for index, order in enumerate(orders):
            # Dedicated paired trips preserve early pickups even when B=1
            # includes late deliveries. The decoder still enforces physical
            # vehicle occupancy and the configured trip-slot limit.
            state["e2"].append(
                dict(
                    satellite=site,
                    vehicle=index % fleet.e2_per_sat_count,
                    tasks=[(order.id, k) for k in problem.task_kinds],
                )
            )
    return state


def appointment_state(problem, rng):
    """Pack nearby overlapping appointments before reusing physical vehicles.

    Uses only the problem's orders and resources, never dataset provenance or
    a reference solution. This is an additional feasible-start heuristic.
    """
    from models.routing.model_1.alns.evaluator import _e1_trip

    state = chronological_state(problem, rng)
    state["e2"] = []
    p, f = problem.operating_policy, problem.fleet
    supply = {}
    for b in p.batches:
        for v, sites in enumerate(state["deliveries"][b.id]):
            trip, _ = _e1_trip(problem, b, v, sites, [], {}, p.cycle_start)
            for event in trip["events"] if trip else ():
                supply[event["site_id"], b.id] = (
                    event["start"] + problem.service(event["site_id"]) + p.e2_dispatch_prep_time
                )

    for site in problem.satellites:
        remaining = {o.id: o for o in problem.orders if o.satellite == site}
        available = [p.cycle_start] * f.e2_per_sat_count
        slots = [0] * f.e2_per_sat_count

        def simulate(orders, vehicle):
            if slots[vehicle] >= p.e2_max_trips_per_vehicle:
                return None
            if (
                sum(o.goods_weight + o.count * f.container_weight for o in orders)
                > f.e2_weight_cap + 1e-6
            ):
                return None
            if sum(o.count * f.container_volume for o in orders) > f.e2_volume_cap + 1e-6:
                return None
            t = max(
                available[vehicle] + p.e2_dispatch_prep_time,
                supply[site, orders[0].batch],
                orders[0].delivery_window[0] - problem.travel(site, orders[0].customer),
            )
            depart, last, delivered = t, site, {}
            for o in orders:
                t = max(t + problem.travel(last, o.customer), o.delivery_window[0])
                if t > o.delivery_window[1]:
                    return None
                if len(p.batches) == 1 and t + o.service_time > p.batches[0].cutoff:
                    return None
                delivered[o.id] = t
                t += o.service_time
                last = o.customer
            for o in orders if problem.reusable else ():
                t = max(
                    t + problem.travel(last, o.customer),
                    o.pickup_window[0],
                    delivered[o.id] + o.service_time + o.min_use_time,
                )
                if t > o.pickup_window[1]:
                    return None
                t += o.service_time
                last = o.customer
            finish = t + problem.travel(last, site) + p.e2_return_unload_time
            if finish + p.e2_turnaround_time > p.cycle_end:
                return None
            if problem.reusable and finish > p.batches[-1].cutoff:
                return None
            return depart, finish

        while remaining:
            # Nearby first stops whose appointment windows overlap the next
            # release band; later waves cannot block today's earlier jobs.
            earliest = min(
                o.delivery_window[0] - problem.travel(site, o.customer) for o in remaining.values()
            )
            band = [
                o
                for o in remaining.values()
                if o.delivery_window[0] - problem.travel(site, o.customer) <= earliest + p.ticks(1)
            ]
            first = min(band, key=lambda o: (problem.travel(site, o.customer), o.id))
            candidates = []
            for vehicle in range(f.e2_per_sat_count):
                for second in [None] + sorted(
                    (
                        o
                        for o in remaining.values()
                        if o.id != first.id
                        and o.batch == first.batch
                        and o.delivery_window[0]
                        <= first.delivery_window[1]
                        + first.service_time
                        + problem.travel(first.customer, o.customer)
                    ),
                    key=lambda o: (problem.travel(first.customer, o.customer), o.id),
                ):
                    orders = [first] if second is None else [first, second]
                    times = simulate(orders, vehicle)
                    if times is not None:
                        extra = (
                            0
                            if second is None
                            else problem.distance(first.customer, second.customer)
                        )
                        candidates.append((-len(orders), extra, times[1], vehicle, orders, times))
            if not candidates:
                # Preserve coverage for the normal penalized repair path. The
                # decoder and independent audit still decide feasibility.
                vehicle = min(range(f.e2_per_sat_count), key=lambda v: available[v])
                orders, times = [first], (
                    max(available[vehicle], supply[site, first.batch]),
                    available[vehicle],
                )
            else:
                _, _, _, vehicle, orders, times = min(candidates, key=lambda c: c[:4])
            state["e2"].append(
                dict(
                    satellite=site,
                    vehicle=vehicle,
                    depart_after=times[0],
                    tasks=[(o.id, "d") for o in orders]
                    + ([(o.id, "p") for o in orders] if problem.reusable else []),
                )
            )
            available[vehicle] = times[1] + p.e2_turnaround_time
            slots[vehicle] += 1
            for o in orders:
                del remaining[o.id]
    return state


def initial_state(problem, rng, randomized=False):
    p, f = problem.operating_policy, problem.fleet
    state = dict(deliveries={}, e2=[], pickup_order={}, pickup_vehicle={})
    for b in p.batches:
        state["deliveries"][b.id] = [[] for _ in range(f.e1_count)]
        state["pickup_order"][b.id] = list(problem.satellites)
        state["pickup_vehicle"][b.id] = {}
        sites = [s for s in problem.satellites if problem.demands[s, b.id]]
        sites.sort(key=lambda s: -problem.weights[s, b.id])
        if randomized:
            rng.shuffle(sites)
            rng.shuffle(state["pickup_order"][b.id])
        for s in sites:
            choices = []
            for k, row in enumerate(state["deliveries"][b.id]):
                new = row + [s]
                trip, issues = _e1_trip(problem, b, k, new, [], {}, p.cycle_start)
                choices.append((len(issues), route_distance(problem, trip, "e1"), k))
            k = min(choices)[2]
            state["deliveries"][b.id][k].append(s)
    for s in problem.satellites:
        route_count = 0
        for b in p.batches:
            orders = [o for o in problem.orders if o.satellite == s and o.batch == b.id]
            orders.sort(key=lambda o: (o.delivery_window[1], o.pickup_window[0]))
            if randomized:
                rng.shuffle(orders)
            packed, weight, volume = [], 0, 0
            for o in orders:
                _, w, v = problem.task(o.id, "d")
                if packed and (weight + w > f.e2_weight_cap or volume + v > f.e2_volume_cap):
                    state["e2"].append(
                        dict(
                            satellite=s,
                            vehicle=route_count % f.e2_per_sat_count,
                            tasks=[(o.id, "d") for o in packed]
                            + ([(o.id, "p") for o in packed] if problem.reusable else []),
                        )
                    )
                    route_count += 1
                    packed, weight, volume = [], 0, 0
                packed.append(o)
                weight, volume = weight + w, volume + v
            if packed:
                # Deliver-first often avoids waiting at the first customer's
                # late pickup window before other deliveries have completed.
                tasks = [(o.id, "d") for o in packed] + (
                    [(o.id, "p") for o in packed] if problem.reusable else []
                )
                state["e2"].append(
                    dict(satellite=s, vehicle=route_count % f.e2_per_sat_count, tasks=tasks)
                )
                route_count += 1
    return state
