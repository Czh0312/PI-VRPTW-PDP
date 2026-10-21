"""Necessary simultaneous issue bound; contains no optimization algorithm."""


def necessary_pool_lower_bound(problem, *, batch_count=None, relaxed_loading=False):
    """Stock occupied in unavoidable time intervals cannot be reused.

    Every batch withdraws its whole demand at one loading event, so N is at
    least ceil(Q/K). For each order, loading is no later than the latest legal
    batch start (or its delivery deadline), while its boxes cannot be clean
    before pickup, shortest return travel, unloading and cleaning. Overlapping
    mandatory intervals require distinct boxes. Release-at-loading equality
    permits reuse, hence releases sort before issues at an equal timestamp.
    Shortest-path times keep the argument valid for directed/nonmetric inputs.
    """
    batches = problem.operating_policy.batches
    if not batches:
        return problem.pool_min
    k = len(batches) if batch_count is None else batch_count
    bound = max(problem.pool_min, (problem.total + k-1)//k)
    p = problem.operating_policy
    events = []
    starts = getattr(problem, 'start_bounds', {})
    for order in problem.orders:
        bound = max(bound, order.count)
        choices = problem.legal_batches[order.id]
        if not choices:
            continue
        latest = order.delivery_window[1]
        if not relaxed_loading and not getattr(problem, 'optimize_loading', False):
            latest = min(latest, max(starts.get(bid, (problem.batch_map[bid].start,
                problem.batch_map[bid].start))[1] for bid in choices))
        pickup = max(order.pickup_window[0],
                     order.delivery_window[0] + order.service_time + order.min_use_time)
        earliest_clean = (pickup + order.service_time
            + problem.travel_bounds['inward'][order.satellite][order.customer]
            + p.e2_return_unload_time
            + problem.travel_bounds['outward'][order.satellite][problem.depot]
            + p.depot_return_unload_time + p.cleaning_duration)
        if latest < earliest_clean:
            events.extend(((latest, order.count), (earliest_clean, -order.count)))
    occupied = 0
    for _, change in sorted(events):
        occupied += change
        bound = max(bound, occupied)
    return bound


def necessary_cost_lower_bound(problem, *, batch_count=None, relaxed_loading=False):
    """Relaxed degree/stock bound, independent of either solving algorithm.

    Closed routes must enter and leave every required physical location. The
    sum of cheapest incoming (or outgoing) non-self arcs is a lower bound,
    including for directed nonmetric matrices. Repeated visits only add arcs.
    Each productive batch also requires a depot trip and a delivery trip.
    Work costs are omitted; fixed vehicle/trip charges use only necessary
    counts. No bound presumes that a particular K or route will be optimal.
    """
    rates = problem.cost_profile.rates
    k = len(problem.operating_policy.batches) if batch_count is None else batch_count
    active = sorted({o.satellite for o in problem.orders})

    def degree(nodes):
        if len(nodes) < 2:
            return 0.
        return max(sum(min(problem.distance(a, b) for b in nodes if b != a) for a in nodes),
                   sum(min(problem.distance(a, b) for a in nodes if a != b) for b in nodes))

    e1_nodes = [problem.depot] + active
    e1 = degree(e1_nodes)
    if active:
        e1 = max(e1, k*(min(problem.distance(problem.depot, s) for s in active)
                        + min(problem.distance(s, problem.depot) for s in active)))
    e2 = 0.
    cycles = []
    for site in active:
        customers = sorted({o.customer for o in problem.orders if o.satellite == site})
        e2 += degree([site] + customers)
        cycles.append(min(problem.distance(site, c) for c in customers)
                      + min(problem.distance(c, site) for c in customers))
    if cycles:
        e2 = max(e2, k*min(cycles))
    value = (necessary_pool_lower_bound(problem, batch_count=k, relaxed_loading=relaxed_loading)
             * rates['container_pool_cost_per_period']
             + rates['distance_scale_to_km']*(e1*rates['e1_distance_cost_per_km']
                                             + e2*rates['e2_distance_cost_per_km'])
             + (rates['e1_vehicle_cost_per_cycle'] if active else 0.)
             + len(active)*rates['e2_vehicle_cost_per_cycle']
             + k*(rates['e1_trip_cost']+rates['e2_trip_cost'])
             + sum(problem.cost_profile.constant_cost(problem.total).values()))
    return max(0., value-1e-9*max(1., abs(value)))


def forced_batch_pool_violation(problem):
    """Only singleton necessary batch domains force a simultaneous issue.

    A batch's whole demand is withdrawn at one physical loading event. Prior
    clean returns can replenish inventory but cannot make more than N boxes
    simultaneously available. This bound concerns this fixed candidate only.
    """
    forced = {batch.id: 0 for batch in problem.operating_policy.batches}
    orders = {batch.id: [] for batch in problem.operating_policy.batches}
    for order in problem.orders:
        choices = problem.legal_batches[order.id]
        if len(choices) == 1:
            batch = choices[0]
            forced[batch] += order.count
            orders[batch].append(order.id)
    violating = [batch for batch, demand in forced.items() if demand > problem.pool_max]
    if not violating:
        return None
    return dict(pool_max=problem.pool_max, forced_batch_demands=forced,
                violating_batches=violating, forced_orders=orders)
