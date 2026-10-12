"""Atomic customer exchanges and same-satellite route-tail exchanges."""
from itertools import combinations
import time

from archive.routing_baselines.alns.common.engine import evaluate_solution
from archive.routing_baselines.alns.common.types import FEAS_TOL, clean_empty_routes


def improve_route_exchanges(solution, evaluator, deadline, max_rounds=8):
    """Return an improved copy; validate complete E1/E2 schedules for each move.

    Swapping customer identities moves both delivery and pickup atomically,
    including when they occupy different routes. Tail swaps keep the satellite
    fixed but exchange multiple tasks between two vehicles in a single move.
    """
    current = solution.copy()
    cost, feasible = evaluate_solution(evaluator, current)
    if not feasible:
        return current, 0
    improvements = 0
    try:
        for _ in range(max_rounds):
            if time.time() >= deadline:
                break
            moves = []
            for sat, routes in current.e2_routes.items():
                for a, b in combinations(range(len(routes)), 2):
                    left, right = routes[a], routes[b]
                    for i in range(len(left.nodes) + 1):
                        for j in range(len(right.nodes) + 1):
                            if time.time() >= deadline:
                                return current, improvements
                            before_a = left.nodes[i-1] if i else sat
                            after_a = left.nodes[i] if i < len(left.nodes) else sat
                            before_b = right.nodes[j-1] if j else sat
                            after_b = right.nodes[j] if j < len(right.nodes) else sat
                            gain = (evaluator.dist(before_a, after_a) + evaluator.dist(before_b, after_b)
                                    - evaluator.dist(before_a, after_b) - evaluator.dist(before_b, after_a))
                            if gain > FEAS_TOL:
                                moves.append((gain, 'tail', (sat, a, b, i, j)))
            assignment = evaluator.assignment(current)
            for a, b in combinations(sorted(assignment), 2):
                if time.time() >= deadline:
                    return current, improvements
                pair_a, pair_b = evaluator.catalog[a], evaluator.catalog[b]
                mapping = {pair_a[k].id: pair_b[k].id for k in ('d', 'p')}
                mapping.update({v: k for k, v in list(mapping.items())})
                gain = 0.0
                for routes in current.e2_routes.values():
                    for route in routes:
                        path = route.full_path()
                        for u, v in zip(path, path[1:]):
                            nu, nv = mapping.get(u, u), mapping.get(v, v)
                            if nu != u or nv != v:
                                gain += evaluator.dist(u, v) - evaluator.dist(nu, nv)
                # Across satellites, rebuilding E1 can change its cost as well.
                if gain > FEAS_TOL or assignment[a] != assignment[b]:
                    moves.append((gain, 'pair', mapping))
            moves.sort(key=lambda move: move[0], reverse=True)
            improved = False
            for _, kind, payload in moves:
                if time.time() >= deadline:
                    break
                candidate = current.copy()
                if kind == 'tail':
                    sat, a, b, i, j = payload
                    left, right = candidate.e2_routes[sat][a], candidate.e2_routes[sat][b]
                    left.nodes, right.nodes = (left.nodes[:i] + right.nodes[j:],
                                               right.nodes[:j] + left.nodes[i:])
                    clean_empty_routes(candidate)
                else:
                    for routes in candidate.e2_routes.values():
                        for route in routes:
                            route.nodes = [payload.get(node, node) for node in route.nodes]
                new_cost, valid = evaluate_solution(evaluator, candidate)
                if valid and new_cost < cost - FEAS_TOL:
                    current, cost = candidate, new_cost
                    improvements += 1
                    improved = True
                    break
            if not improved:
                break
    finally:
        evaluator.rebuild_from_solution(current)
    return current, improvements
