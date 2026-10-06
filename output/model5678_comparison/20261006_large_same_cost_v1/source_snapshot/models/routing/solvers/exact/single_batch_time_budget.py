"""Valid additive physical-fleet time cuts for the B1 delivery relaxation.

For [a,b], charge the guaranteed minimum overlap of each canonical operation.
Pure delivery permits advancing travel/returns while keeping customer services
and route distance unchanged. Operations start after actual E1 supply, so
their charged overlap sums to at most V * max(0, b - max(a,supply)).
Unused waiting and uncharged portions are relaxed.
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


def time_windows(problem, context, limit=8, mode='interval'):
    p = problem.operating_policy
    ends = time_deadlines(problem, context, limit)
    windows = {(p.cycle_start, b) for b in ends if b > p.cycle_start}
    if mode == 'interval':
        starts = sorted({max(p.cycle_start, n['order'].delivery_window[0]) for n in context['nodes']})
        cap = min(4, limit)
        if len(starts) > cap:
            starts = [starts[i*(len(starts)-1)//(cap-1)] for i in range(cap)]
        windows.update((a, b) for a in starts for b in ends if b > a)
    elif mode != 'prefix':
        raise ValueError('Time-cut mode must be prefix or interval')
    return sorted(windows)


def window_bounds(problem, window):
    return tuple(window) if isinstance(window, (tuple, list)) else (problem.operating_policy.cycle_start, window)


def minimum_overlap(lower, upper, duration, a, b):
    """Minimum overlap of [t,t+duration] with [a,b] for lower<=t<=upper."""
    if upper < lower:
        return 0  # This arc cannot occur in a feasible route.
    return max(0, min(duration, b-a, lower+duration-a, b-upper))


def service_bounds(problem, order):
    p = problem.operating_policy
    return max(p.cycle_start, order.delivery_window[0]), min(order.delivery_window[1], p.batches[0].cutoff-order.service_time)


def node_work(problem, order, deadline):
    a, b = window_bounds(problem, deadline)
    low, high = service_bounds(problem, order)
    return minimum_overlap(low, high, order.service_time, a, b)


def arc_work(problem, site, left, right, deadline):
    """Canonical arc occupation; None denotes the source/sink warehouse."""
    p = problem.operating_policy
    a, b = window_bounds(problem, deadline)
    if right is None:
        tail = problem.travel(left.customer, site)+p.e2_return_unload_time+p.e2_turnaround_time
        low, high = service_bounds(problem, left)
        return minimum_overlap(low+left.service_time, min(high+left.service_time, p.cycle_end-tail), tail, a, b)
    duration = problem.travel(site if left is None else left.customer, right.customer)
    _, right_high = service_bounds(problem, right)
    if left is None:
        duration += p.e2_dispatch_prep_time
        low, high = p.cycle_start, right_high-duration
    else:
        left_low, left_high = service_bounds(problem, left)
        low, high = left_low+left.service_time, min(left_high+left.service_time, right_high-duration)
    return minimum_overlap(low, high, duration, a, b)


def route_work(problem, site, tasks, deadline):
    """Guaranteed interval occupation, without double-counting operations."""
    previous, work = None, 0
    for key in tasks:
        order = problem.order_map[key]
        work += node_work(problem, order, deadline)+arc_work(problem, site, previous, order, deadline)
        previous = order
    return work+arc_work(problem, site, previous, None, deadline) if previous is not None else 0


def validate_work_prices(work_prices):
    pairs = tuple((tuple(b) if isinstance(b, (tuple, list)) else b, v) for b, v in work_prices)
    def valid(window):
        if isinstance(window, (tuple, list)):
            return len(window) == 2 and all(math.isfinite(t) for t in window) and window[1] >= window[0]
        return math.isfinite(window)
    if any(not valid(b) or not math.isfinite(v) or v < 0 for b, v in pairs):
        raise ValueError('Time dual penalties must be finite and nonnegative')
    return pairs


def work_penalty(problem, site, tasks, work_prices=()):
    return math.fsum(price*route_work(problem, site, tasks, b) for b, price in work_prices)


def reduced_route_cost(problem, site, column, prices, fleet_price=0, *, phase_one=False, work_prices=()):
    return ((0 if phase_one else column['cost'])+work_penalty(problem, site, column['tasks'], work_prices)
        -math.fsum(prices.get(k, 0) for k in column['tasks'])-min(0.0, fleet_price))
