"""Valid additive physical-fleet time cuts for the B1 delivery relaxation.

For a deadline b, charge only occupied segments guaranteed to finish by b.
Every charged segment starts after actual E1 supply. Their sum is at most
V * max(0, b - supply); unused waiting and uncharged segments are relaxed.
Directed/nonmetric travel and zero-duration segments are supported.
"""
import math


def completion_due(problem, order):
    return min(order.delivery_window[1]+order.service_time, problem.operating_policy.batches[0].cutoff)


def time_deadlines(problem, context, limit=8):
    """Deterministic due-time quantiles plus delivery and terminal deadlines."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 2:
        raise ValueError('Time-cut limit must be an integer >= 2')
    p = problem.operating_policy
    values = sorted({completion_due(problem, n['order']) for n in context['nodes']})
    if len(values) > limit:
        values = [values[i*(len(values)-1)//(limit-1)] for i in range(limit)]
    return sorted(set(values+[p.batches[0].cutoff, p.cycle_end]))


def node_work(problem, order, deadline):
    return order.service_time if completion_due(problem, order) <= deadline else 0


def arc_work(problem, site, left, right, deadline):
    """None denotes the warehouse at the corresponding source/sink arc."""
    p = problem.operating_policy
    if right is None:
        tail = problem.travel(left.customer, site)+p.e2_return_unload_time+p.e2_turnaround_time
        return tail if deadline >= p.cycle_end or completion_due(problem, left)+tail <= deadline else 0
    if completion_due(problem, right) > deadline:
        return 0
    return problem.travel(site if left is None else left.customer, right.customer)+(p.e2_dispatch_prep_time if left is None else 0)


def route_work(problem, site, tasks, deadline):
    """Guaranteed occupation before b, without double-counting segments."""
    previous, work = None, 0
    for key in tasks:
        order = problem.order_map[key]
        work += node_work(problem, order, deadline)+arc_work(problem, site, previous, order, deadline)
        previous = order
    return work+arc_work(problem, site, previous, None, deadline) if previous is not None else 0


def validate_work_prices(work_prices):
    pairs = tuple(work_prices)
    if any(not math.isfinite(b) or not math.isfinite(v) or v < 0 for b, v in pairs):
        raise ValueError('Time dual penalties must be finite and nonnegative')
    return pairs


def work_penalty(problem, site, tasks, work_prices=()):
    return math.fsum(price*route_work(problem, site, tasks, b) for b, price in work_prices)


def reduced_route_cost(problem, site, column, prices, fleet_price=0, *, phase_one=False, work_prices=()):
    return ((0 if phase_one else column['cost'])+work_penalty(problem, site, column['tasks'], work_prices)
        -math.fsum(prices.get(k, 0) for k in column['tasks'])-min(0.0, fleet_price))
