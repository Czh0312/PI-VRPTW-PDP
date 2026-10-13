"""Necessary simultaneous issue bound; contains no optimization algorithm."""


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
