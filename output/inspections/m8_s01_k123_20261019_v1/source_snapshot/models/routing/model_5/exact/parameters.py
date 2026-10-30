"""Inputs and physical bounds for the complete fixed-assignment M5 MILP."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MultibatchParameters:
    """Reuse the public instance data and keep safe derived bounds separately."""

    problem: Any
    policy: Any
    fleet: Any
    single_batch: bool
    free_assignment: bool
    share_e2: bool
    share_cutoff: bool
    linear_e2_links: bool
    task_windows: dict
    site_windows: dict
    assignment_domains: dict | None
    travel_bounds: dict
    domain_check: dict | None
    depot_outward: dict
    horizon: int
    total_containers: int


def multibatch_prepare_parameters(
    domain_check,
    linear_e2_links,
    problem,
    share_cutoff,
    share_e2,
    travel_bounds,
):
    domains = None
    outward = {}
    p, f = problem.operating_policy, problem.fleet
    single = len(p.batches) == 1
    free = problem.free_assignment
    if free and not single:
        raise ValueError("Joint assignment is supported only for B=1")
    compact = share_e2
    compact_cutoff = not single and share_cutoff
    linear_links = compact and linear_e2_links
    windows, site_windows = {}, {}
    if not free:
        from models.common.cycle.precheck import travel_lower_bounds

        travel_bounds = travel_bounds or travel_lower_bounds(problem)
        outward = travel_bounds["outward"][problem.depot]
        for s in problem.satellites:
            travel_out = travel_bounds["outward"][s]
            travel_in = travel_bounds["inward"][s]
            for o in problem.orders:
                if o.satellite != s:
                    continue
                batch = problem.batch_map[o.batch]
                supplied = (
                    batch.start
                    + p.e1_load_time
                    + outward[s]
                    + problem.service(s)
                    + p.e2_dispatch_prep_time
                )
                delivered = max(o.delivery_window[0], supplied + travel_out[o.customer])
                windows[o.id, "d"] = (
                    delivered,
                    min(o.delivery_window[1], batch.cutoff - o.service_time)
                    if single
                    else o.delivery_window[1],
                )
                windows[o.id, "p"] = (
                    max(o.pickup_window[0], delivered + o.service_time + o.min_use_time),
                    min(
                        o.pickup_window[1],
                        p.batches[-1].cutoff
                        - o.service_time
                        - travel_in[o.customer]
                        - p.e2_return_unload_time,
                    ),
                )
    H, total = p.cycle_end, max(1, problem.total)
    return MultibatchParameters(
        problem,
        p,
        f,
        single,
        free,
        compact,
        compact_cutoff,
        linear_links,
        windows,
        site_windows,
        domains,
        travel_bounds,
        domain_check,
        outward,
        H,
        total,
    )
