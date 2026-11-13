"""Bounded chronological partition seeds ranked by reuse and transport cost.

These are heuristic proposals, not constraints or optimality certificates.
Only the existing full decoder and original-domain audit accept a schedule.
No optimizer, reference result, case identifier or saved route participates.
"""
from itertools import combinations


def use_reuse_seeds(problem):
    """Target multisatellite, multiwave calendars; leave simpler starts intact."""
    if len(problem.orders) > 9 or len({o.satellite for o in problem.orders}) < 2:
        return False
    bands, end = 0, float('-inf')
    for order in sorted(problem.orders, key=lambda o: o.delivery_window):
        if order.delivery_window[0] > end:
            bands += 1
        end = max(end, order.delivery_window[1])
    return bands >= 4


def _nearest_tour(problem, site, orders):
    remaining = {order.customer for order in orders}
    at, length = site, 0.
    while remaining:
        nxt = min(remaining, key=lambda node: (problem.distance(at, node), node))
        length += problem.distance(at, nxt)
        at = nxt
        remaining.remove(nxt)
    return length + problem.distance(at, site)


def _timing(problem, order, start):
    p = problem.operating_policy
    outward, inward = problem.travel_bounds['outward'], problem.travel_bounds['inward']
    site = order.satellite
    supply = (p.e1_load_time + outward[problem.depot][site] + problem.service(site)
              + p.e2_dispatch_prep_time + outward[site][order.customer])
    delivered = max(order.delivery_window[0], start + supply)
    pickup = max(order.pickup_window[0], delivered + order.service_time + order.min_use_time)
    returned = pickup + order.service_time + inward[site][order.customer] + p.e2_return_unload_time
    tail = (problem.service(site) + outward[site][problem.depot]
            + p.depot_return_unload_time + p.cleaning_duration)
    return order.delivery_window[1] - supply, delivered, returned, tail


def _rank(problem, groups, boundaries, cutoffs):
    p = problem.operating_policy
    batches = p.batches
    delivered_sites = [{o.satellite for o in group} for group in groups]
    recovered_sites = [set() for _ in groups]
    releases, recovered = [], [0] * len(groups)
    violations = 0
    issued = required = 0
    for index, group in enumerate(groups):
        for order in group:
            _, delivery, returned, tail = _timing(problem, order, boundaries[index])
            violations += int(delivery > order.delivery_window[1])
            chosen = next((j for j in range(index, len(groups)) if returned <= cutoffs[batches[j].id]), None)
            if chosen is None:
                violations += 1
            else:
                recovered[chosen] += order.count
                recovered_sites[chosen].add(order.satellite)
                releases.append((cutoffs[batches[chosen].id] + tail, order.count))
    for index, group in enumerate(groups):
        issued += sum(o.count for o in group)
        required = max(required, issued - sum(n for ready, n in releases if ready <= boundaries[index]))
    violations += sum(n == 0 for n in recovered) if problem.require_productive_batches else 0
    required = max(problem.pool_min, required)
    rates = problem.cost_profile.rates
    e1 = sum(2 * sum(problem.distance(problem.depot, site) for site in ds | rs)
             for ds, rs in zip(delivered_sites, recovered_sites))
    e2 = sum(2 * _nearest_tour(problem, site, [o for o in group if o.satellite == site])
             for group in groups for site in {o.satellite for o in group})
    proxy = (rates['container_pool_cost_per_period'] * required + rates['distance_scale_to_km']
             * (rates['e1_distance_cost_per_km'] * e1 + rates['e2_distance_cost_per_km'] * e2))
    return violations, proxy, required


def reuse_calendar_seeds(variable, limit=8):
    """Keep a small diverse set of contiguous temporal partitions, including uneven ones."""
    n, k = len(variable.orders), variable.count
    if n > 9 or k < 2 or k > n or limit <= 0:
        return
    p, parent = variable.operating_policy, variable.parent
    minimum = 2 * variable.minimum_phase_ticks
    batches = p.batches
    latest = {o.id: _timing(variable, o, p.cycle_start)[0] for o in variable.orders}
    orderings = [
        sorted(variable.orders, key=lambda o: (o.delivery_window[0], o.satellite, o.delivery_window[1], o.id)),
        sorted(variable.orders, key=lambda o: (latest[o.id], o.satellite, o.id)),
        sorted(variable.orders, key=lambda o: (o.pickup_window[0], o.satellite, o.delivery_window[0], o.id)),
    ]
    ranked, seen = [], set()
    for ordering in orderings:
        for cuts in combinations(range(1, n), k - 1):
            ends = (0,) + cuts + (n,)
            groups = [ordering[a:b] for a, b in zip(ends, ends[1:])]
            assignment = {o.id: batch.id for batch, group in zip(batches, groups) for o in group}
            if any(assignment[o.id] not in variable.legal_batches[o.id] for o in variable.orders):
                continue
            deadlines = [min(latest[o.id] for o in group) for group in groups]
            release = [max(o.release_time for o in group) for group in groups]
            first_start = max(p.cycle_start, release[0]) if parent.no_work_window else p.cycle_start
            if not release[0] <= first_start <= deadlines[0]:
                continue
            # Try earliest optimistic clean reuse and a later loading frontier.
            for late in (False, True):
                boundaries = [first_start]
                for index in range(1, k):
                    low = max(release[index], boundaries[-1] + minimum)
                    high = min(deadlines[index], p.cycle_end - minimum * (k - index))
                    if low > high:
                        break
                    clean = max(sum(_timing(variable, o, boundaries[-1])[2:]) for o in groups[index-1])
                    target = high if late else clean
                    boundaries.append(min(high, max(low, target)))
                if len(boundaries) != k:
                    continue
                boundaries.append(p.cycle_end)
                try:
                    boundaries = parent.proposed_boundaries(k, boundaries)
                except ValueError:
                    continue
                cutoffs = {}
                for index, group in enumerate(groups):
                    tail = max(_timing(variable, o, boundaries[index])[3] for o in group)
                    target = boundaries[index+1] - tail
                    lo, hi = boundaries[index] + variable.minimum_phase_ticks, boundaries[index+1] - variable.minimum_phase_ticks
                    cutoffs[batches[index].id] = min(hi, max(lo, target))
                signature = (tuple(boundaries), tuple(sorted(assignment.items())), tuple(cutoffs.values()))
                if signature in seen:
                    continue
                seen.add(signature)
                rank = _rank(variable, groups, boundaries, cutoffs)
                ranked.append((rank, signature, tuple(boundaries), assignment, cutoffs))
    ranked.sort(key=lambda row: (row[0], row[1]))
    # First cover distinct assignments; then use spare slots for calendar variants.
    chosen, represented = [], set()
    for row in ranked:
        assignment_key = tuple(sorted(row[3].items()))
        if assignment_key not in represented:
            represented.add(assignment_key)
            chosen.append(row)
            if len(chosen) >= limit:
                break
    if len(chosen) < limit:
        chosen_keys = {row[1] for row in chosen}
        chosen.extend(row for row in ranked if row[1] not in chosen_keys)
    for _, _, boundaries, assignment, cutoffs in chosen[:limit]:
        yield boundaries, dict(assignment), dict(cutoffs)
