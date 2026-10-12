"""Exact insertion-option reuse within a fixed-assignment repair call."""
from __future__ import annotations


class FixedRouteInsertionCache:
    """Cache only unchanged routes; preserve enumeration and floating-point order.

    Task demands, physical locations and E1 routes must stay fixed during this
    cache's lifetime. Call ``invalidate`` after changing a route (including its
    transport loads). This is not a cache for free-assignment repair operators.
    ``enabled=False`` provides the exhaustive reference path for A/B tests.
    """

    def __init__(self, evaluator, *, enabled=True):
        self.evaluator = evaluator
        self.enabled = enabled
        self._routes = {}

    def invalidate(self, route):
        self._routes.pop(route, None)

    def options(self, route, node_id, satellite, start_time):
        key = (node_id, satellite, start_time)
        cached = self._routes.setdefault(route, {}) if self.enabled else {}
        if key not in cached:
            evaluator = self.evaluator
            old_cost = evaluator.route_distance(route)
            options = []
            original_nodes = route.nodes
            try:
                for position in range(len(original_nodes) + 1):
                    route.nodes = list(original_nodes)
                    route.nodes.insert(position, node_id)
                    if evaluator.check_e2_route_feasible(route, satellite, start_time):
                        cost = evaluator.route_distance(route) - old_cost
                        options.append((cost, route, position))
            finally:
                route.nodes = original_nodes
            cached[key] = options
        return cached[key]
