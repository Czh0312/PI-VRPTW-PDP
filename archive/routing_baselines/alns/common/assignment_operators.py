"""Assignment-aware destroy operators shared by free-assignment models."""
from __future__ import annotations

import random

from archive.routing_baselines.alns.common.types import clean_empty_routes


def _remove_customers(sol, evaluator, customers):
    chosen = set(customers)
    for routes in sol.e2_routes.values():
        for route in routes:
            route.nodes = [
                node_id for node_id in route.nodes
                if not (
                    (task := evaluator._catalog_task(node_id)) is not None
                    and task.cust in chosen
                )
            ]
    clean_empty_routes(sol)
    return list(customers)


def assignment_boundary_removal(sol, evaluator, num_remove, echelon="e2"):
    """Remove customers that have a competitive alternative satellite."""
    assignment = evaluator.assignment(sol)
    scored = []
    for customer, current_sat in assignment.items():
        node_id = f"{customer}d"
        current = evaluator.dist(current_sat, node_id)
        alternatives = [
            evaluator.dist(sat_id, node_id)
            for sat_id in evaluator.sat_ids if sat_id != current_sat
        ]
        if alternatives:
            scored.append((min(alternatives) - current, random.random(), customer))
    scored.sort()
    count = min(len(scored), max(1, num_remove))
    return _remove_customers(
        sol, evaluator, [customer for _gap, _noise, customer in scored[:count]])


def two_satellite_removal(sol, evaluator, num_remove, echelon="e2"):
    """Destroy portions of two assignments so repair can exchange customers."""
    assignment = evaluator.assignment(sol)
    by_sat = {}
    for customer, sat_id in assignment.items():
        by_sat.setdefault(sat_id, []).append(customer)
    nonempty = list(by_sat)
    if not nonempty:
        return []
    selected = random.sample(nonempty, min(2, len(nonempty)))
    quota = max(1, (num_remove + len(selected) - 1) // len(selected))
    customers = []
    for sat_id in selected:
        pool = list(by_sat[sat_id])
        random.shuffle(pool)
        customers.extend(pool[:quota])
    return _remove_customers(sol, evaluator, customers[:max(1, num_remove)])
