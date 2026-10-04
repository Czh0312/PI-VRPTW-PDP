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


class CapacityCutSeparator:
    """Add only violated valid cuts, after a node relaxation has completed."""

    def __init__(self, variables):
        self.variables = variables
        self.candidates = []
        self.added = set()
        self.error = None

    def __call__(self, model, where):
        import gurobipy as gp
        from gurobipy import GRB
        if where != GRB.Callback.MIPNODE or self.error:
            return
        try:
            if model.cbGet(GRB.Callback.MIPNODE_STATUS) != GRB.OPTIMAL:
                return
            values = model.cbGetNodeRel(self.variables)
            violated = []
            for index, (terms, rhs) in enumerate(self.candidates):
                if index in self.added:
                    continue
                excess = sum(values[i] for i in terms)-rhs
                if excess > 1e-6:
                    violated.append((excess, index))
            for _, index in sorted(violated, reverse=True)[:20]:
                terms, rhs = self.candidates[index]
                model.cbCut(gp.quicksum(self.variables[i] for i in terms) <= rhs)
                self.added.add(index)
        except Exception as exc:
            # Gurobi otherwise only prints callback exceptions and continues.
            # Stop and let the solver report an explicit, unproven failure.
            self.error = f'{type(exc).__name__}: {exc}'
            model.terminate()


def build_capacity_separator(problem, internal_arcs, check_budget):
    separator = CapacityCutSeparator(list(internal_arcs.values()))
    indexed = [(a[0], b[0], i) for i, (a, b) in enumerate(internal_arcs)]
    for site in problem.satellites:
        orders = sorted((o for o in problem.orders if o.satellite == site), key=lambda o: o.id)
        if not orders:
            continue
        keys = {o.id for o in orders}
        arcs = [(a, b, i) for a, b, i in indexed if a in keys]
        subsets = [orders]
        for batch in problem.operating_policy.batches:
            subsets.append([o for o in orders if o.batch == batch.id])
        if len(orders) <= 12:
            subsets.extend(combinations(orders, 2))
        else:
            # Bound growth for large satellites; selection affects strength,
            # not validity. Always include whole-site and whole-batch cuts.
            for order in orders:
                check_budget()
                nearby = sorted((o for o in orders if o.id != order.id), key=lambda o: (
                    problem.distance(order.customer, o.customer)+problem.distance(o.customer, order.customer), o.id))[:5]
                subsets.extend((order, o) for o in nearby)
        seen = set()
        for selected in subsets:
            check_budget()
            subset = frozenset(o.id for o in selected)
            if len(subset) < 2 or subset in seen:
                continue
            seen.add(subset)
            routes = minimum_trip_count(problem, selected)
            terms = [i for a, b, i in arcs if a in subset and b in subset]
            rhs = 2*len(subset)-routes
            if len(terms) > rhs:
                separator.candidates.append((terms, rhs))
    return separator
