"""Known inputs and safe derived bounds for M7."""
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
