"""Necessary assignment conditions, checked before creating a fixed-K MILP.

These certificates use the child's full legal domains, including movable
calendar bounds for M8. Passing this check does not establish feasibility.
"""


def infeasible_count_certificate(problem):
    """Return a proof of impossible assignment, or None when undecided.

Every order must choose one legal batch. When productive batches are
required, every batch must receive a distinct (indivisible) order. We only
use empty domains, empty batch neighborhoods and the pigeonhole condition;
no heuristic calendar, incumbent, cost bound or numerical tolerance is used.
    """
    batches = [b.id for b in problem.operating_policy.batches]
    orders = [o.id for o in problem.orders]
    domains = {oid: tuple(problem.legal_batches[oid]) for oid in orders}
    if any(bid not in batches for choices in domains.values() for bid in choices):
        raise ValueError('Legal assignment domains contain an unknown batch')
    empty_orders = [oid for oid in orders if not domains[oid]]
    if empty_orders:
        return dict(reason='orders_without_legal_batch', orders=empty_orders,
                    source='fixed_count_assignment_rows')
    if not problem.require_productive_batches:
        return None
    empty_batches = [bid for bid in batches if not any(bid in d for d in domains.values())]
    if empty_batches:
        return dict(reason='productive_batches_without_legal_order', batches=empty_batches,
                    source='fixed_count_assignment_rows')
    if len(batches) > len(orders):
        return dict(reason='more_productive_batches_than_orders',
                    batch_count=len(batches), order_count=len(orders),
                    source='fixed_count_assignment_rows')
    return None
