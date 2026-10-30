"""Necessary temporal assignment domains and verifiable fixed-K certificates.

Before loading order i in batch b, each of the b-1 earlier productive batches
has returned at least one integer container. These returns belong to orders
loaded in earlier batches, other than i, whose optimistic depot return is no
later than i's latest loading. Count containers, not orders: a multi-container
order may supply returns to more than one batch. No incumbent is used.
"""
from models.routing.model_8.exact.parameters import necessary_time_bounds


def validate_preprocessing_options(preprocessing, vehicle_symmetry):
    if not isinstance(preprocessing, bool) or not isinstance(vehicle_symmetry, bool):
        raise ValueError('preprocessing and vehicle_symmetry must be boolean')


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


def infeasible_time_domain_certificate(problem):
    return temporal_assignment_bounds(problem)['certificate']


def add_temporal_assignment_constraints(problem, variables):
    """Apply necessary domains and predecessor-cover inequalities to a block."""
    import gurobipy as gp

    bounds = temporal_assignment_bounds(problem)
    variables.pool.LB = bounds['pool_lower_bound']
    batches = [b.id for b in problem.operating_policy.batches]
    quantities = {o.id: o.count for o in problem.orders}
    for (order, batch), variable in variables.assignments.items():
        if batch not in bounds['domains'][order]:
            variable.UB = 0
    rows = 0
    if problem.require_productive_batches:
        for order in quantities:
            for index, batch in enumerate(batches):
                target = variables.assignments.get((order, batch))
                if index == 0 or target is None or batch not in bounds['domains'][order]:
                    continue
                prior = gp.quicksum(quantities[j]*variables.assignments[j, h]
                                   for j in bounds['eligible_predecessors'][order] for h in batches[:index]
                                   if (j, h) in variables.assignments)
                variables.model.addConstr(index*target <= prior,
                                          name=f'temporal_prior_returns_{order}_{batch}')
                rows += 1
    if bounds['certificate'] is not None:
        # Fixed-K diagnostics must also retain the explicit contradiction.
        variables.model.addConstr(gp.LinExpr() == 1, name='temporal_domain_infeasible')
    bounds['predecessor_cover_rows'] = rows
    return bounds
