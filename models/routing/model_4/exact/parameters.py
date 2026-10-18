"""Known inputs and safe derived bounds for M4."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from models.routing.model_4.exact.accelerators.strengthening import single_batch_bounds


@dataclass(frozen=True)
class ModelParameters:
    """Reuse the public instance data and keep safe derived bounds separately."""

    problem: Any
    policy: Any
    fleet: Any
    free_assignment: bool
    share_e2: bool
    linear_link_mode: Any
    task_windows: dict
    site_windows: dict
    assignment_domains: dict | None
    travel_bounds: dict
    domain_check: dict | None
    bounds: dict
    horizon: int
    total_containers: int


def prepare_parameters(
    causal_step_bounds,
    domain_check,
    linear_e2_links,
    problem,
    share_e2,
    short_cycle_cuts,
    travel_bounds,
):
    domains = None
    p, f = problem.operating_policy, problem.fleet
    if len(p.batches) != 1:
        raise ValueError("Single-batch formulation requires B=1")
    if not isinstance(short_cycle_cuts, bool):
        raise ValueError("Short-cycle cuts must be boolean")
    if short_cycle_cuts and (problem.model_number not in (1, 2, 3, 4) or not share_e2):
        raise ValueError("Short-cycle cuts require shared single-batch M1-M4 task arcs")
    if not isinstance(causal_step_bounds, bool):
        raise ValueError("Causal step bounds must be boolean")
    if causal_step_bounds and problem.model_number not in (1, 2, 3, 4):
        raise ValueError("Causal step bounds require single-batch M1-M4")
    free = problem.free_assignment
    compact = share_e2
    linear_mode = "complete" if linear_e2_links is True else linear_e2_links or False
    if linear_mode not in (False, "identity", "internal", "all", "complete"):
        raise ValueError("Unknown single-batch linear link mode")
    windows, site_windows = {}, {}
    if free:
        from models.routing.model_4.exact.accelerators.domains import assignment_domains

        domain_check = domain_check or assignment_domains(problem, travel_bounds)
        if not domain_check["passed"]:
            raise ValueError("Joint model requires nonempty necessary assignment domains")
        domains = domain_check["domains"]
        site_windows = domain_check["windows"]
        for o in problem.orders:
            for kind in problem.task_kinds:
                choices = [site_windows[s, o.id, kind] for s in domains[o.customer]]
                windows[o.id, kind] = (min(w[0] for w in choices), max(w[1] for w in choices))
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
                    min(o.delivery_window[1], batch.cutoff - o.service_time),
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
    from models.common.cycle.precheck import travel_lower_bounds

    travel_bounds = travel_bounds or travel_lower_bounds(problem)
    tightened = single_batch_bounds(problem, travel_bounds, domains if free else None)
    H, total = p.cycle_end, max(1, problem.total)
    return ModelParameters(
        problem,
        p,
        f,
        free,
        compact,
        linear_mode,
        windows,
        site_windows,
        domains,
        travel_bounds,
        domain_check,
        tightened,
        H,
        total,
    )
