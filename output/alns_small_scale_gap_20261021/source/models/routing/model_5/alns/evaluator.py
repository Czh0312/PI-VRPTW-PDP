"""ALNS schedule decoder. Entire downstream state is rebuilt on every move."""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from models.routing.model_5.alns.state import state_key


from models.common.cycle.state import empty_result


def _e1_trip(problem, batch, vehicle, deliveries, pickups, quantities, ready):
    p, f = problem.operating_policy, problem.fleet
    if not deliveries and not pickups:
        return None, []
    problems = []
    start = batch.start + p.e1_load_time if deliveries else max(batch.cutoff, ready)
    if deliveries and ready > batch.start:
        problems.append("E1_VEHICLE_OVERLAP")
    current, site = start, problem.depot
    trip = dict(
        trip_id=f"e1_{batch.id}_{vehicle}",
        batch_id=batch.id,
        vehicle_id=vehicle,
        departure=start,
        events=[],
    )
    dw = sum(problem.weights[s, batch.id] for s in deliveries)
    dv = sum(problem.demands[s, batch.id] * f.container_volume for s in deliveries)
    pq = sum(quantities.get(s, 0) for s in pickups)
    if dw > f.e1_weight_cap + 1e-6 or pq * f.container_weight > f.e1_weight_cap + 1e-6:
        problems.append("E1_WEIGHT_CAPACITY")
    if dv > f.e1_volume_cap + 1e-6 or pq * f.container_volume > f.e1_volume_cap + 1e-6:
        problems.append("E1_VOLUME_CAPACITY")
    for kind, sequence in (("d", deliveries), ("p", pickups)):
        if kind == "p" and sequence:
            current = max(current, batch.cutoff)
        for s in sequence:
            current += problem.travel(site, s)
            n = problem.demands[s, batch.id] if kind == "d" else quantities[s]
            trip["events"].append(dict(kind=kind, site_id=s, start=current, quantity=n))
            current += problem.service(s)
            if kind == "d" and current > batch.cutoff:
                problems.append("E1_DELIVERY_DEADLINE")
            site = s
    trip["return_complete"] = (
        current + problem.travel(site, problem.depot) + p.depot_return_unload_time
    )
    if trip["return_complete"] > batch.end:
        problems.append("E1_RETURN_DEADLINE")
    if trip["return_complete"] + p.e1_turnaround_time > p.cycle_end:
        problems.append("E1_TERMINAL_VEHICLE")
    if pickups and trip["return_complete"] + p.cleaning_duration > p.cycle_end:
        problems.append("TERMINAL_DIRTY")
    return trip, problems


def route_distance(problem, trip, layer):
    base = problem.depot if layer == "e1" else trip["satellite_id"]
    sites = (
        [base]
        + [
            e["site_id"] if layer == "e1" else problem.order_map[e["order_id"]].customer
            for e in trip["events"]
        ]
        + [base]
    )
    return sum(problem.distance(a, b) for a, b in zip(sites, sites[1:]))


def _e1_returns(problem, state, quantities):
    p, f = problem.operating_policy, problem.fleet
    trips, violations = [], []
    ready = [p.cycle_start] * f.e1_count
    releases = []
    for b in p.batches:
        picked = [[] for _ in range(f.e1_count)]
        q = quantities[b.id]
        active = [s for s in state["pickup_order"][b.id] if q[s]]
        # Delivery times are independent of pickup insertion. Decode that
        # prefix once; pickup stops have no individual time windows, so their
        # return deadline checks use exact integer travel/service deltas.
        forward = []
        for k in range(f.e1_count) if len(active) > 1 else ():
            deliveries = state["deliveries"][b.id][k]
            trip, issues = _e1_trip(problem, b, k, deliveries, [], {}, ready[k])
            end = (trip["events"][-1]["start"] + problem.service(deliveries[-1])
                   if deliveries else max(b.cutoff, ready[k]))
            forward.append((
                max(end, b.cutoff),
                sum(code in ("E1_VEHICLE_OVERLAP", "E1_DELIVERY_DEADLINE") for code in issues),
                sum(problem.weights[c, b.id] for c in deliveries),
                sum(problem.demands[c, b.id] * f.container_volume for c in deliveries),
            ))
        for s in active:
            options = []
            hint = state["pickup_vehicle"][b.id].get(s)
            if len(active) == 1:
                # One pickup has no alternative insertion positions; avoid
                # preparing prefixes that cannot be reused.
                for k in range(f.e1_count):
                    trip, issues = _e1_trip(
                        problem, b, k, state["deliveries"][b.id][k], [s], q, ready[k])
                    options.append((len(issues), int(hint is not None and hint != k),
                                    route_distance(problem, trip, "e1"), k))
                k = min(options)[3]
                picked[k] = [s]
                continue
            for k in range(f.e1_count):
                deliveries = state["deliveries"][b.id][k]
                start, fixed_issues, dw, dv = forward[k]
                pickup_nodes = [deliveries[-1] if deliveries else problem.depot] + picked[k] + [problem.depot]
                pickup_edges = list(zip(pickup_nodes, pickup_nodes[1:]))
                travel_edges = [problem.travel(a, c) for a, c in pickup_edges]
                completed = (start + sum(travel_edges) + sum(problem.service(c) for c in picked[k])
                             + p.depot_return_unload_time)
                quantity = sum(q[c] for c in picked[k]) + q[s]
                capacity_issues = (
                    int(dw > f.e1_weight_cap + 1e-6 or quantity * f.container_weight > f.e1_weight_cap + 1e-6)
                    + int(dv > f.e1_volume_cap + 1e-6 or quantity * f.container_volume > f.e1_volume_cap + 1e-6)
                )
                nodes = [problem.depot] + deliveries + picked[k] + [problem.depot]
                distance_edges = [problem.distance(a, c) for a, c in zip(nodes, nodes[1:])]
                for position, (left, right) in enumerate(pickup_edges):
                    returned = (completed + problem.travel(left, s) + problem.service(s)
                                + problem.travel(s, right) - travel_edges[position])
                    issues = (fixed_issues + capacity_issues + int(returned > b.end)
                              + int(returned + p.e1_turnaround_time > p.cycle_end)
                              + int(returned + p.cleaning_duration > p.cycle_end))
                    edge = len(deliveries) + position
                    # Reuse unchanged arc values while preserving the original
                    # floating-point summation order and insertion tie breaks.
                    distance = sum(distance_edges[:edge]
                                   + [problem.distance(left, s), problem.distance(s, right)]
                                   + distance_edges[edge + 1:])
                    options.append((issues, int(hint is not None and hint != k),
                                    distance, k, position))
            _, _, _, k, position = min(options, key=lambda x: x[:4])
            picked[k].insert(position, s)
        for k in range(f.e1_count):
            trip, issues = _e1_trip(
                problem, b, k, state["deliveries"][b.id][k], picked[k], q, ready[k]
            )
            violations.extend(issues)
            if trip:
                trips.append(trip)
                ready[k] = trip["return_complete"] + p.e1_turnaround_time
                n = sum(q[s] for s in picked[k])
                if n:
                    releases.append((trip["return_complete"] + p.cleaning_duration, n))
    return trips, violations, releases


def evaluate(problem, state, *, _memo=None, _prepared=None):
    """Return (penalized score, result, violations), never silently relax a rule."""
    p, f = problem.operating_policy, problem.fleet
    task = problem.task if _prepared is None else _prepared.task
    summary_of = getattr(_prepared, "load_summary", None)
    result = empty_result(problem, "alns")
    violations, goods_ready = [], {}
    if problem._availability_codes:
        return 1e12, result, list(problem._availability_codes)
    # E1 deliveries do not depend on reverse flow; delivery times are explicit.
    for b in p.batches:
        seen = []
        for k, sites in enumerate(state["deliveries"][b.id]):
            trip, issues = _e1_trip(problem, b, k, sites, [], {}, p.cycle_start)
            # A delivery-only return may be late whereas the eventual mixed
            # route fits. Validate final return only after inserting pickups.
            violations.extend(
                x for x in issues if x not in ("E1_RETURN_DEADLINE", "E1_TERMINAL_VEHICLE")
            )
            if trip:
                for e in trip["events"]:
                    goods_ready[e["site_id"], b.id] = e["start"] + problem.service(e["site_id"])
            seen.extend(sites)
        expected = [s for s in problem.satellites if problem.demands[s, b.id]]
        if sorted(seen) != sorted(expected):
            return 1e12, result, ["E1_DELIVERY_COVERAGE"]

    # Difference constraints propagate cross-route customer precedence and
    # physical-vehicle reuse; deliberate departure/return waits are preserved.
    bounds, edges, upper, tasks = {}, [], {}, {}
    plans = [r for r in state["e2"] if r["tasks"]]
    previous_vehicle, slots = {}, defaultdict(int)
    parsed = []
    for ri, r in enumerate(plans):
        s, v = r["satellite"], r["vehicle"]
        if s not in problem.satellites or not 0 <= v < f.e2_per_sat_count:
            return 1e12, result, ["INVALID_E2_VEHICLE"]
        key = (s, v)
        slot = slots[key]
        slots[key] += 1
        if slot >= p.e2_max_trips_per_vehicle:
            violations.append("E2_TRIP_LIMIT")
        begin, finish = ("start", ri), ("end", ri)
        bounds[begin] = max(p.cycle_start, r.get("depart_after", p.cycle_start))
        bounds[finish] = max(p.cycle_start, r.get("return_after", p.cycle_start))
        upper[finish] = p.cycle_end - p.e2_turnaround_time
        loads = summary_of(tuple(r["tasks"])) if summary_of is not None else None
        dbatches = (
            {problem.order_map[o].batch for o, kind in r["tasks"] if kind == "d"}
            if loads is None
            else loads[0]
        )
        if len(dbatches) > 1:
            return 1e12, result, ["MIXED_DELIVERY_BATCHES"]
        if key in previous_vehicle:
            edges.append(
                (
                    previous_vehicle[key],
                    begin,
                    p.e2_turnaround_time + (p.e2_dispatch_prep_time if dbatches else 0),
                )
            )
        previous_vehicle[key] = finish
        if dbatches:
            b = next(iter(dbatches))
            bounds[begin] = max(bounds[begin], goods_ready[s, b] + p.e2_dispatch_prep_time)
        if loads is None:
            load_w = sum(task(o, kind)[1] for o, kind in r["tasks"] if kind == "d")
            load_v = sum(task(o, kind)[2] for o, kind in r["tasks"] if kind == "d")
        else:
            _, load_w, load_v = loads
        if load_w > f.e2_weight_cap + 1e-6 or load_v > f.e2_volume_cap + 1e-6:
            violations.append("E2_CAPACITY")
        previous, site, service = begin, s, 0
        for j, (oid, kind) in enumerate(r["tasks"]):
            o, w, vol = task(oid, kind)
            if o.satellite != s or (oid, kind) in tasks:
                return 1e12, result, ["E2_TASK_COVERAGE"]
            node = (ri, j)
            tasks[oid, kind] = node
            tw = o.delivery_window if kind == "d" else o.pickup_window
            bounds[node], upper[node] = tw
            if len(p.batches) == 1 and kind == "d":
                upper[node] = min(upper[node], p.batches[0].cutoff - o.service_time)
            edges.append((previous, node, service + problem.travel(site, o.customer)))
            previous, site, service = node, o.customer, o.service_time
            load_w += w if kind == "p" else -w
            load_v += vol if kind == "p" else -vol
            if (
                min(load_w, load_v) < -1e-6
                or load_w > f.e2_weight_cap + 1e-6
                or load_v > f.e2_volume_cap + 1e-6
            ):
                violations.append("E2_CAPACITY")
        edges.append(
            (previous, finish, service + problem.travel(site, s) + p.e2_return_unload_time)
        )
        parsed.append((r, ri, slot))
    expected = (
        {(o.id, k) for o in problem.orders for k in problem.task_kinds}
        if _prepared is None
        else _prepared.expected_tasks
    )
    if set(tasks) != expected:
        return 1e12, result, ["E2_TASK_COVERAGE"]
    for o in problem.orders if problem.reusable else ():
        edges.append((tasks[o.id, "d"], tasks[o.id, "p"], o.service_time + o.min_use_time))
    if _prepared is not None and problem.reusable:
        acyclic = _prepared.propagate(bounds, edges)
    else:
        indegree = {n: 0 for n in bounds}
        children = defaultdict(list)
        for a, b, delta in edges:
            indegree[b] += 1
            children[a].append((b, delta))
        queue = [n for n in bounds if not indegree[n]]
        visited = 0
        while queue:
            node = queue.pop()
            visited += 1
            for child, delta in children[node]:
                bounds[child] = max(bounds[child], bounds[node] + delta)
                indegree[child] -= 1
                if not indegree[child]:
                    queue.append(child)
        acyclic = visited == len(bounds)
    if not acyclic:
        return 1e12, result, ["TIMING_CYCLE"]
    if any(t > upper.get(node, p.cycle_end) for node, t in bounds.items()):
        violations.append("E2_TIME_WINDOW")
    quantities = {b.id: {s: 0 for s in problem.satellites} for b in p.batches}
    for r, ri, slot in parsed:
        u = bounds["end", ri]
        returned = sum(problem.order_map[o].count for o, k in r["tasks"] if k == "p")
        batch = next((b for b in p.batches if u <= b.cutoff), None)
        if returned:
            if batch:
                quantities[batch.id][r["satellite"]] += returned
            else:
                violations.append("TERMINAL_OUTSTANDING")
        events = [
            dict(order_id=o, kind=k, start=bounds[ri, j], quantity=problem.order_map[o].count)
            for j, (o, k) in enumerate(r["tasks"])
        ]
        result["e2_trips"].append(
            dict(
                trip_id=f'e2_{r["satellite"]}_{r["vehicle"]}_{slot}',
                satellite_id=r["satellite"],
                vehicle_id=r["vehicle"],
                trip_index=slot,
                departure=bounds["start", ri],
                return_complete=u,
                events=events,
            )
        )
    result["e1_trips"], e1_issues, releases = (
        _memo.returns(state, quantities)
        if _memo is not None
        else _e1_returns(problem, state, quantities)
    )
    violations.extend(e1_issues)
    issued, shortage = 0, 0
    pool_mode = getattr(problem, 'optimize_pool', False)
    required_pool = problem.pool_min if pool_mode else p.container_pool_size
    for b in p.batches if problem.reusable else ():
        issued += problem.batch_demands[b.id]
        required_pool = max(required_pool, issued - sum(n for t, n in releases if t <= b.start))
        gap = issued - p.container_pool_size - sum(n for t, n in releases if t <= b.start)
        if gap > 0:
            violations.append("CLEAN_STOCK_SHORTAGE")
            shortage += gap
    distance_of = route_distance if _prepared is None else _prepared.route_distance
    distance = sum(
        distance_of(problem, r, layer) for layer in ("e1", "e2") for r in result[layer + "_trips"]
    )
    result["info"]["objective"] = distance
    if pool_mode:
        if violations:
            # Infeasible search proposals may lie outside the time horizon;
            # the physical auditor only accepts complete, bounded schedules.
            return distance + len(violations) * 1e6 + shortage * 1e5, result, violations
        # N is the exact conditional minimum for each candidate schedule. The
        # search compares full costs, so changing routes/times can change N.
        from models.common.cycle.fixed_pool import attach_pool_cost
        pool = min(problem.pool_max, required_pool)
        try:
            physical = attach_pool_cost(problem, result, pool)
        except ValueError:
            return 1e12, result, ['INVALID_SCHEDULE']
        if not physical['valid']:
            violations.extend(e['code'] for e in physical['errors'])
        return result['info']['objective'] + len(violations) * 1e6 + shortage * 1e5, result, violations
    return distance + len(violations) * 1e6 + shortage * 1e5, result, violations


class EvaluationCache:
    def __init__(self, problem, evaluator, capacity=256):
        self.problem, self.evaluator, self.capacity = problem, evaluator, capacity
        self.entries = OrderedDict()
        self.hits = self.misses = 0

    def __call__(self, state):
        key = state_key(state)
        if key in self.entries:
            self.hits += 1
            self.entries.move_to_end(key)
            return self.entries[key]
        self.misses += 1
        value = self.evaluator(self.problem, state)
        self.entries[key] = value
        if len(self.entries) > self.capacity:
            self.entries.popitem(last=False)
        return value


def initial_state(problem, rng, randomized=False):
    """Compatibility entry; construction.py owns initialization."""
    from models.routing.model_5.alns.construction import initial_state as construct

    return construct(problem, rng, randomized=randomized)
