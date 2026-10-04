"""Bounded caches for evaluators whose physical task locations never change."""
from functools import lru_cache

from .types import Route


def cache_static_evaluator(evaluator, *, catalogue=None):
    """Cache geometry and E2 schedules, never assignment-dependent E1 state.

    Do not apply to relay evaluators: their virtual task locations and loads
    can change. Exact tuples (including departure time) invalidate route data
    automatically after any edit. E2 task demand/window data must be immutable.
    """
    aliases = dict(evaluator.task_to_phys)
    if catalogue is not None:
        aliases.update({t.id: t.cust for pair in catalogue.values() for t in pair.values()})
        aliases.update({f'{s.id}{kind}': s.id for s in evaluator.problem.satellites for kind in ('d', 'p')})
    problem = evaluator.problem
    evaluator.dist = lru_cache(maxsize=65536)(
        lambda a, b: problem.distance(aliases.get(a, a), aliases.get(b, b)))
    evaluator.travel = lru_cache(maxsize=65536)(
        lambda a, b: problem.travel_time(aliases.get(a, a), aliases.get(b, b)))
    evaluator.service_time = lru_cache(maxsize=4096)(
        lambda node: getattr(problem.nodes.get(aliases.get(node, node)), 'service_time', 0.0))
    original_distance = evaluator.route_distance

    @lru_cache(maxsize=8192)
    def route_distance(base, nodes):
        return original_distance(Route(base, list(nodes)))

    evaluator.route_distance = lambda route: route_distance(route.base, tuple(route.nodes))
    simulate = getattr(evaluator, 'simulate_e2_route', None)
    if simulate is not None:
        @lru_cache(maxsize=8192)
        def schedule(base, nodes, satellite, start, pickup_ready):
            if pickup_ready:
                return simulate(Route(base, list(nodes)), satellite, start,
                                pickup_ready=dict(pickup_ready))
            return simulate(Route(base, list(nodes)), satellite, start)

        evaluator.simulate_e2_route = lambda route, satellite, start_time=0.0, pickup_ready=None: schedule(
            route.base, tuple(route.nodes), satellite, start_time,
            tuple(sorted((pickup_ready or {}).items())))
