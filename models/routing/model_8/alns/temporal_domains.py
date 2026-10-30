"""Pure necessary time bounds for ALNS proposals; no optimization backend.

These input-only implications mirror the mathematical model. Domain narrowing
uses certificates, never an incumbent or an objective reference.
"""

def necessary_time_bounds(problem):
    """Mandatory container occupancy and first-batch assignment implications.

    Shortest-path travel relaxations remain safe for nonmetric matrices.
    Order i holds its boxes throughout [latest_loading_i, earliest_clean_i).
    If i is loaded in a later batch, some other order must finish the first
    batch's productive depot recovery before i is loaded. When even the
    earliest such return exceeds latest_loading_i, i must be in batch 1.
    These implications cover continuous physical time, hence all refined
    integer clocks, and do not depend on an incumbent or a chosen calendar.
    """
    policy = problem.operating_policy
    outward, inward = problem.travel_bounds['outward'], problem.travel_bounds['inward']
    intervals, events = [], {}
    for order in problem.orders:
        site = order.satellite
        supply = (policy.e1_load_time + outward[problem.depot][site] + problem.service(site)
                  + policy.e2_dispatch_prep_time + outward[site][order.customer])
        latest_loading = order.delivery_window[1] - supply
        delivery = max(order.delivery_window[0], order.release_time+supply, policy.cycle_start+supply)
        pickup = max(order.pickup_window[0], delivery+order.service_time+order.min_use_time)
        clean = (pickup + order.service_time + inward[site][order.customer] + policy.e2_return_unload_time
                 + problem.service(site) + outward[site][problem.depot] + policy.depot_return_unload_time
                 + policy.cleaning_duration)
        intervals.append(dict(order=order.id, count=order.count,
                              latest_loading=latest_loading, earliest_clean=clean))
        if latest_loading < clean:
            events[latest_loading] = events.get(latest_loading, 0) + order.count
            events[clean] = events.get(clean, 0) - order.count
    active, lower, witness = 0, 0, None
    for tick, delta in sorted(events.items()):
        active += delta
        if active > lower:
            lower, witness = active, tick
    forced = []
    if problem.require_productive_batches:
        for row in intervals:
            other_returns = [r['earliest_clean']-policy.cleaning_duration
                             for r in intervals if r['order'] != row['order']]
            if other_returns and min(other_returns) > row['latest_loading']:
                forced.append(dict(order=row['order'], latest_loading=row['latest_loading'],
                                   earliest_other_order_depot_return=min(other_returns)))
    return dict(pool_lower_bound=lower, witness_tick=witness, intervals=intervals,
                forced_first_batch=forced)


def productive_matching_certificate(domains, batches):
    """Find a covering matching, or return an explicit deficient Hall set."""
    owners = {}
    neighbors = {b: [o for o, choices in domains.items() if b in choices] for b in batches}

    def augment(batch, seen_orders, seen_batches):
        seen_batches.add(batch)
        for order in neighbors[batch]:
            if order in seen_orders:
                continue
            seen_orders.add(order)
            if order not in owners or augment(owners[order], seen_orders, seen_batches):
                owners[order] = batch
                return True
        return False

    for batch in sorted(batches, key=lambda b: len(neighbors[b])):
        seen_orders, seen_batches = set(), set()
        if not augment(batch, seen_orders, seen_batches):
            witness_batches = [b for b in batches if b in seen_batches]
            witness_orders = [o for o, choices in domains.items()
                              if any(b in choices for b in witness_batches)]
            assert len(witness_orders) < len(witness_batches)
            return dict(reason='productive_batch_hall_deficiency',
                        batches=witness_batches, neighboring_orders=witness_orders,
                        batch_count=len(witness_batches), order_count=len(witness_orders))
    return None


def temporal_assignment_bounds(problem):
    batches = [b.id for b in problem.operating_policy.batches]
    position = {b: i for i, b in enumerate(batches, 1)}
    domains = {o.id: list(problem.legal_batches[o.id]) for o in problem.orders}
    if any(b not in position for choices in domains.values() for b in choices):
        raise ValueError('Legal assignment domains contain an unknown batch')
    bounds = necessary_time_bounds(problem)
    intervals = {row['order']: row for row in bounds['intervals']}
    quantities = {o.id: o.count for o in problem.orders}
    eligible = {o: [j for j, row in intervals.items() if j != o and
                   row['earliest_clean']-problem.operating_policy.cleaning_duration
                   <= intervals[o]['latest_loading']] for o in domains}
    removed = []
    if problem.require_productive_batches:
        changed = True
        while changed:
            changed = False
            for order in domains:
                for batch in list(domains[order]):
                    index = position[batch]
                    available = sum(quantities[j] for j in eligible[order]
                                    if any(position[h] < index for h in domains[j]))
                    if available < index-1:
                        domains[order].remove(batch)
                        removed.append(dict(order=order, batch=batch, required_returns=index-1,
                                            optimistic_available_returns=available))
                        changed = True
    certificate = None
    empty = [o for o in domains if not domains[o]]
    if empty:
        certificate = dict(reason='temporal_assignment_domain_empty', orders=empty)
    elif problem.require_productive_batches:
        certificate = productive_matching_certificate(domains, batches)
    total = sum(quantities.values())
    lower = max(problem.pool_min, bounds['pool_lower_bound'], max(quantities.values(), default=0),
                (total+len(batches)-1)//len(batches))
    prefix_bounds = []
    for index in range(1, len(batches)+1):
        prefix = set(batches[:index])
        demand = sum(quantities[o] for o, choices in domains.items() if choices and set(choices) <= prefix)
        bound = (demand+index-1)//index
        lower = max(lower, bound)
        prefix_bounds.append(dict(batches=batches[:index], mandatory_demand=demand, pool_lower_bound=bound))
    if certificate is None and lower > problem.pool_max:
        certificate = dict(reason='necessary_pool_exceeds_maximum', lower_bound=lower,
                           maximum=problem.pool_max)
    if certificate is not None:
        certificate.update(source='model_8_necessary_temporal_assignment',
                           domains=domains, removed=removed,
                           time_tick_seconds=problem.operating_policy.time_tick_seconds)
    return dict(domains=domains, eligible_predecessors=eligible, removed=removed,
                pool_lower_bound=lower, prefix_bounds=prefix_bounds, certificate=certificate,
                productive=problem.require_productive_batches)


class DomainView:
    """Narrow heuristic proposal domains without mutating the public instance."""
    def __init__(self, source, domains):
        self.source = source
        self.legal_batches = {oid: tuple(choices) for oid, choices in domains.items()}

    def __getattr__(self, name):
        return getattr(self.source, name)


def proposal_view(problem):
    bounds = temporal_assignment_bounds(problem)
    return DomainView(problem, bounds['domains']), bounds
