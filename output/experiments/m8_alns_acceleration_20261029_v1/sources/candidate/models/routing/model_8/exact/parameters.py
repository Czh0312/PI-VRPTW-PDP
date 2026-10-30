"""Known inputs and safe derived bounds for M8."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelParameters:
    """Reuse the public instance data and keep safe derived bounds separately."""

    problem: Any
    policy: Any
    fleet: Any
    horizon: int
    total_containers: int
    compact_e2: bool


def prepare_parameters(problem, *, compact_e2=None):
    p, f = problem.operating_policy, problem.fleet
    H, Q = p.cycle_end, problem.total
    if compact_e2 is None:
        # Compact the formulation once replicated internal arc constraints
        # dominate construction; keep the established small-model search.
        copies = 0
        for site in problem.satellites:
            tasks = 2 * sum(o.satellite == site for o in problem.orders)
            copies += tasks * max(0, tasks-1) * min(p.e2_max_trips_per_vehicle, tasks) * f.e2_per_sat_count
        compact_e2 = copies > 4000
    return ModelParameters(problem, p, f, H, Q, compact_e2)


def necessary_time_bounds(problem):
    """Mandatory container occupancy and first-batch assignment implications.

    Shortest-path travel relaxations remain safe for nonmetric matrices.
    Order i holds its boxes throughout [latest_loading_i, earliest_clean_i).
    If i is loaded in a later batch, some other order must finish the first
    batch's productive depot recovery before i is loaded. When even the
    earliest such return exceeds latest_loading_i, i must be in batch 1.
    These implications cover continuous physical time, hence all refined
    integer clocks, and do not depend on an incumbent or a chosen calendar.
    """
    policy = problem.operating_policy
    outward, inward = problem.travel_bounds['outward'], problem.travel_bounds['inward']
    intervals, events = [], {}
    for order in problem.orders:
        site = order.satellite
        supply = (policy.e1_load_time + outward[problem.depot][site] + problem.service(site)
                  + policy.e2_dispatch_prep_time + outward[site][order.customer])
        latest_loading = order.delivery_window[1] - supply
        delivery = max(order.delivery_window[0], order.release_time+supply, policy.cycle_start+supply)
        pickup = max(order.pickup_window[0], delivery+order.service_time+order.min_use_time)
        clean = (pickup + order.service_time + inward[site][order.customer] + policy.e2_return_unload_time
                 + problem.service(site) + outward[site][problem.depot] + policy.depot_return_unload_time
                 + policy.cleaning_duration)
        intervals.append(dict(order=order.id, count=order.count,
                              latest_loading=latest_loading, earliest_clean=clean))
        if latest_loading < clean:
            events[latest_loading] = events.get(latest_loading, 0) + order.count
            events[clean] = events.get(clean, 0) - order.count
    active, lower, witness = 0, 0, None
    for tick, delta in sorted(events.items()):
        active += delta
        if active > lower:
            lower, witness = active, tick
    forced = []
    if problem.require_productive_batches:
        for row in intervals:
            other_returns = [r['earliest_clean']-policy.cleaning_duration
                             for r in intervals if r['order'] != row['order']]
            if other_returns and min(other_returns) > row['latest_loading']:
                forced.append(dict(order=row['order'], latest_loading=row['latest_loading'],
                                   earliest_other_order_depot_return=min(other_returns)))
    return dict(pool_lower_bound=lower, witness_tick=witness, intervals=intervals,
                forced_first_batch=forced)
