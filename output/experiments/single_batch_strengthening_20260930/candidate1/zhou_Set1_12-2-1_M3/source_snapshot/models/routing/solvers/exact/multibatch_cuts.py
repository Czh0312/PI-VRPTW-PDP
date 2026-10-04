"""Rounded capacity cuts on subsets of complete order task pairs.

Every route entering a subset can supply at most one delivery batch and its
vehicle capacity. Thus the subset needs at least the sum of per-batch rounded
delivery bounds, or the rounded pickup bound, whichever is larger. With two
mandatory tasks per order, internal arcs are at most 2*orders-required_routes.
This counting argument does not assume a metric, positive travel, or one visit
per physical customer. It also permits a route to enter a subset repeatedly.
"""
from itertools import combinations
import math


def minimum_trip_count(problem, orders):
    if not orders:
        return 0
    f = problem.fleet

    def rounded(rows, kind):
        bound = 1
        for resource, cap in ((1, f.e2_weight_cap), (2, f.e2_volume_cap)):
            amount = math.fsum(problem.task(o.id, kind)[resource] for o in rows)
            if amount <= 0:
                continue
            # Relax the capacity slightly before rounding. This can weaken a
            # cut, never intentionally strengthen it at a floating boundary.
            relaxed = cap+max(1e-6, abs(cap)*1e-12)
            bound = max(bound, math.ceil(amount/relaxed))
        return bound

    batches = {o.batch for o in orders}
    deliveries = sum(rounded([o for o in orders if o.batch == b], 'd') for b in batches)
    return max(deliveries, rounded(orders, 'p'))


def add_subset_capacity_cuts(model, problem, internal_arcs, check_budget):
    import gurobipy as gp

    count = 0
    for site in problem.satellites:
        orders = sorted((o for o in problem.orders if o.satellite == site), key=lambda o: o.id)
        if not orders:
            continue
        keys = {o.id for o in orders}
        arcs = [(a[0], b[0], variable) for (a, b), variable in internal_arcs.items() if a[0] in keys]
        subsets = [orders]
        for batch in problem.operating_policy.batches:
            subsets.append([o for o in orders if o.batch == batch.id])
        if len(orders) <= 12:
            subsets.extend(combinations(orders, 2))
            subsets.extend(combinations(orders, 3))
        else:
            # Bound growth for large satellites; selection affects strength,
            # not validity. Always include whole-site and whole-batch cuts.
            for order in orders:
                check_budget()
                nearby = sorted((o for o in orders if o.id != order.id), key=lambda o: (
                    problem.distance(order.customer, o.customer)+problem.distance(o.customer, order.customer), o.id))[:5]
                subsets.extend((order, o) for o in nearby)
                subsets.extend((order, a, b) for a, b in combinations(nearby[:3], 2))
        seen = set()
        for selected in subsets:
            check_budget()
            subset = frozenset(o.id for o in selected)
            if len(subset) < 2 or subset in seen:
                continue
            seen.add(subset)
            routes = minimum_trip_count(problem, selected)
            model.addConstr(gp.quicksum(var for a, b, var in arcs if a in subset and b in subset)
                            <= 2*len(subset)-routes, name=f'order_capacity_{site}_{count}')
            count += 1
    return count
