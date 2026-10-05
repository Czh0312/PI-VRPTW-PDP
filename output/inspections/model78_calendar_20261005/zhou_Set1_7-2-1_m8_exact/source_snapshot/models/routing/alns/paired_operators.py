"""Shared pickup-delivery pair neighborhoods for fixed-assignment ALNS."""
from __future__ import annotations

import time
import random

from models.routing.alns.types import FEAS_TOL, Route, clean_empty_routes


def route_customer_pair_removal(sol, evaluator, num_remove, echelon="e2"):
    """Remove complete customer pairs concentrated on one selected route."""
    if echelon != "e2":
        # Generic E1 fallback: remove a random set of tasks.
        nodes = [(route, node_id) for route in sol.e1_routes
                 for node_id in route.nodes]
        random.shuffle(nodes)
        removed = []
        for route, node_id in nodes[:num_remove]:
            if node_id in route.nodes:
                route.nodes.remove(node_id)
                removed.append(node_id)
        clean_empty_routes(sol)
        return removed

    routes = [route for sat_routes in sol.e2_routes.values()
              for route in sat_routes if route.nodes]
    if not routes:
        return []
    seed_route = random.choice(routes)
    customers = []
    for node_id in seed_route.nodes:
        task = evaluator.e2_task_map.get(node_id)
        if task is not None and task.cust not in customers:
            customers.append(task.cust)
    random.shuffle(customers)
    pair_count = max(1, (num_remove + 1) // 2)
    chosen = set(customers[:pair_count])
    removed = []
    for sat_routes in sol.e2_routes.values():
        for route in sat_routes:
            kept = []
            for node_id in route.nodes:
                task = evaluator.e2_task_map.get(node_id)
                if task is not None and task.cust in chosen:
                    removed.append(node_id)
                else:
                    kept.append(node_id)
            route.nodes = kept
    clean_empty_routes(sol)
    return removed


def _adopt(target, source):
    target.e1_routes = [route.copy() for route in source.e1_routes]
    target.e2_routes = {
        sat: [route.copy() for route in routes]
        for sat, routes in source.e2_routes.items()
    }
    target.cost = source.cost
    target.task_to_phys = dict(source.task_to_phys)
    target.transshipments = dict(source.transshipments)


def _ranked_positions(route, node_id, evaluator, limit):
    old_cost = evaluator.route_distance(route)
    positions = []
    for pos in range(len(route.nodes) + 1):
        route.nodes.insert(pos, node_id)
        delta = evaluator.route_distance(route) - old_cost
        route.nodes.pop(pos)
        positions.append((delta, pos))
    positions.sort(key=lambda item: item[0])
    if limit is not None:
        positions = positions[:limit]
    return [pos for _delta, pos in positions]


def insert_complete_customer_pairs(sol, removed, evaluator):
    """Insert every complete d/p pair, allowing different E2 routes.

    Returns the node ids that were not handled as complete pairs. The caller
    can pass those residual nodes to its ordinary single-task repair.
    """
    by_customer = {}
    for node_id in removed:
        task = evaluator.e2_task_map.get(node_id)
        if task is not None:
            by_customer.setdefault(task.cust, {})[task.kind] = node_id

    paired_nodes = set()
    deadline = getattr(evaluator, "_alns_deadline", float("inf"))
    customer_count = max(1, len(evaluator.e2_task_map) // 2)
    position_limit = None if customer_count <= 20 else 8

    pairs = [pair for pair in by_customer.values()
             if "d" in pair and "p" in pair]
    # Harder pairs first: earlier pickup due dates receive priority.
    pairs.sort(key=lambda pair: evaluator.e2_task_map[pair["p"]].tw[1])

    for pair in pairs:
        if time.time() >= deadline:
            break
        d_id, p_id = pair["d"], pair["p"]
        sat_id = evaluator.e2_task_satellite(d_id)
        if sat_id is None:
            continue
        routes = sol.e2_routes.setdefault(sat_id, [])
        delivery_options = [
            (route_idx, pos)
            for route_idx, route in enumerate(routes)
            for pos in _ranked_positions(
                route, d_id, evaluator, position_limit)
        ]
        if len(routes) < evaluator.vehicles_per_satellite:
            delivery_options.append((None, 0))

        best = None
        best_cost = float("inf")
        for d_route_idx, d_pos in delivery_options:
            if time.time() >= deadline:
                break
            with_delivery = sol.copy()
            d_routes = with_delivery.e2_routes.setdefault(sat_id, [])
            if d_route_idx is None:
                d_routes.append(Route(sat_id, [d_id]))
                actual_d_idx = len(d_routes) - 1
            else:
                d_routes[d_route_idx].nodes.insert(d_pos, d_id)
                actual_d_idx = d_route_idx

            start_time = evaluator.e2_start_times(with_delivery).get(
                sat_id, 0.0)
            d_ok, d_arrivals, _ = evaluator.simulate_e2_route(
                d_routes[actual_d_idx], sat_id, start_time)
            if not d_ok or d_id not in d_arrivals:
                continue

            pickup_options = [
                (route_idx, pos)
                for route_idx, route in enumerate(d_routes)
                for pos in _ranked_positions(
                    route, p_id, evaluator, position_limit)
            ]
            if len(d_routes) < evaluator.vehicles_per_satellite:
                pickup_options.append((None, 0))

            for p_route_idx, p_pos in pickup_options:
                if time.time() >= deadline:
                    break
                candidate = with_delivery.copy()
                c_routes = candidate.e2_routes[sat_id]
                if p_route_idx is None:
                    c_routes.append(Route(sat_id, [p_id]))
                    actual_p_idx = len(c_routes) - 1
                else:
                    c_routes[p_route_idx].nodes.insert(p_pos, p_id)
                    actual_p_idx = p_route_idx

                affected = {actual_d_idx, actual_p_idx}
                arrivals = {}
                feasible = True
                for route_idx in affected:
                    ok, route_arrivals, _ = evaluator.simulate_e2_route(
                        c_routes[route_idx], sat_id, start_time)
                    if not ok:
                        feasible = False
                        break
                    arrivals.update(route_arrivals)
                if not feasible:
                    continue
                if (d_id not in arrivals or p_id not in arrivals
                        or arrivals[p_id] < arrivals[d_id]
                        + evaluator.service_time(d_id) - FEAS_TOL):
                    continue

                candidate.cost = evaluator.total_cost(candidate)
                if candidate.cost < best_cost - FEAS_TOL:
                    best = candidate
                    best_cost = candidate.cost

        if best is not None:
            _adopt(sol, best)
            paired_nodes.update((d_id, p_id))

    clean_empty_routes(sol)
    return [node_id for node_id in removed if node_id not in paired_nodes]
