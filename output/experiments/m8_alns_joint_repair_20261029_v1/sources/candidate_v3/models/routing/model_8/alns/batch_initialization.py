"""Balanced, time-aware assignment seeds; no exact solver or saved solutions."""

from itertools import combinations


def temporal_assignments(problem, limit=8):
    """Propose bounded chronological partitions, including unbalanced batches.

    An optimistic cleaning/next-loading comparison ranks these initial guesses;
    it never removes a K or replaces the complete decoder and business audit.
    Only ordered partitions are examined, not the full assignment domain.
    """
    batches = problem.operating_policy.batches
    n, k = len(problem.orders), len(batches)
    if n > 9 or k < 2 or k > n:
        return []
    p = problem.operating_policy
    orders = sorted(problem.orders, key=lambda o: (o.delivery_window[0], o.delivery_window[1], o.id))
    outward, inward = problem.travel_bounds['outward'], problem.travel_bounds['inward']
    supply, tails = {}, {}
    for order in orders:
        site = order.satellite
        supply[order.id] = (p.e1_load_time + outward[problem.depot][site]
            + problem.service(site) + p.e2_dispatch_prep_time + outward[site][order.customer])
        tails[order.id] = (order.service_time + inward[site][order.customer]
            + p.e2_return_unload_time + problem.service(site) + outward[site][problem.depot]
            + p.depot_return_unload_time + p.cleaning_duration)
    ranked = []
    for cuts in combinations(range(1, n), k-1):
        endpoints = (0,) + cuts + (n,)
        groups = [orders[a:b] for a, b in zip(endpoints, endpoints[1:])]
        assignment = {order.id: batch.id for batch, group in zip(batches, groups) for order in group}
        if any(assignment[o.id] not in problem.legal_batches[o.id] for o in orders):
            continue
        earliest = [max(p.cycle_start + 2*problem.minimum_phase_ticks*i,
                        max(o.release_time for o in group)) for i, group in enumerate(groups)]
        latest = [min(o.delivery_window[1]-supply[o.id] for o in group) for group in groups]
        if earliest[0] > p.cycle_start or any(lo > hi for lo, hi in zip(earliest, latest)):
            continue
        clean = [max(max(o.pickup_window[0],
                         max(o.delivery_window[0], start+supply[o.id])+o.service_time+o.min_use_time)
                     + tails[o.id] for o in group) for start, group in zip(earliest, groups)]
        loads = [sum(o.count for o in group) for group in groups]
        issued = peak = 0
        for index, load in enumerate(loads):
            issued += load
            reusable = sum(loads[j] for j in range(index) if clean[j] <= latest[index])
            peak = max(peak, issued-reusable)
        visits = len({(o.satellite, assignment[o.id]) for o in orders})
        rates = problem.cost_profile.rates
        transport = 0.
        for group in groups:
            remaining = {o.satellite for o in group}
            previous = problem.depot
            while remaining:
                site = min(remaining, key=lambda value: (problem.distance(previous, value), value))
                transport += problem.distance(previous, site)*rates['e1_distance_cost_per_km']
                previous = site
                remaining.remove(site)
            transport += problem.distance(previous, problem.depot)*rates['e1_distance_cost_per_km']
            for site in {o.satellite for o in group}:
                transport += max(problem.distance(site, o.customer)+problem.distance(o.customer, site)
                                 for o in group if o.satellite == site)*rates['e2_distance_cost_per_km']
        estimate = peak*rates['container_pool_cost_per_period']+transport*rates['distance_scale_to_km']
        ranked.append(((estimate, peak, visits, max(loads), sum(v*v for v in loads), cuts), assignment))
    return [assignment for _, assignment in sorted(ranked, key=lambda row: row[0])[:limit]]


def balanced_assignments(problem, limit=8, width=32):
    if len(problem.orders) > 16 or len(problem.operating_policy.batches) < 2:
        return []
    batches = problem.operating_policy.batches
    positions = {b.id: i for i, b in enumerate(batches)}
    orders = sorted(problem.orders, key=lambda o: (
        len(problem.legal_batches[o.id]), -o.count, o.delivery_window[1], o.id))
    beam = [(tuple(0 for _ in batches), (), 0.)]
    for index, order in enumerate(orders):
        proposed = []
        remaining = len(orders)-index-1
        for loads, assignment, temporal in beam:
            for bid in problem.legal_batches[order.id]:
                updated = list(loads)
                updated[positions[bid]] += order.count
                if updated.count(0) > remaining:
                    continue
                batch = problem.batch_map[bid]
                p = problem.operating_policy
                outward = (p.e1_load_time
                    + problem.travel_bounds['outward'][problem.depot][order.satellite]
                    + problem.service(order.satellite) + p.e2_dispatch_prep_time
                    + problem.travel_bounds['outward'][order.satellite][order.customer])
                arrival = batch.start+outward
                # Reference-calendar lateness is a ranking penalty only.
                # Variable boundaries may still make this assignment legal.
                distance = (max(0, arrival-order.delivery_window[1])*(p.cycle_end-p.cycle_start+1)
                            + max(0, order.delivery_window[0]-arrival))
                candidate = (tuple(updated), assignment+(bid,), temporal+distance)
                proposed.append(candidate)
        if not proposed:
            return []
        proposed.sort(key=lambda x: (max(x[0]), sum(v*v for v in x[0]), x[2], x[1]))
        beam = proposed[:width]
    return [{order.id: bid for order, bid in zip(orders, assignment)}
            for _, assignment, _ in beam[:limit]]
