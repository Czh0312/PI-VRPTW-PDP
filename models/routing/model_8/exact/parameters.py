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


def prepare_parameters(problem):
    p, f = problem.operating_policy, problem.fleet
    H, Q = p.cycle_end, problem.total
    return ModelParameters(problem, p, f, H, Q)
