# -*- coding: utf-8 -*-
"""Model 2: 2E-VRPTW pure-delivery with free customer-satellite assignment.

Model 2 = Model 1 (pure delivery, no pickup) + Model 4 (free assignment w[c,s]).
"""

MODEL_NUMBER = 2
SUPPORTS_EXACT = True


def solve_exact(problem, e1_tasks=None, e2_tasks_by_sat=None,
                vehicles_per_satellite=None, **kwargs):
    """Run the exact solver through the common variant contract.

    Model 2 builds assignment-dependent tasks internally, so the two task
    arguments are accepted for interface consistency but intentionally unused.
    """
    del e1_tasks, e2_tasks_by_sat
    from .exact_solver import solve_exact as _solve_exact

    if vehicles_per_satellite is None:
        vehicles_per_satellite = problem.fleet.e2_per_sat_count
    return _solve_exact(problem, vehicles_per_satellite, **kwargs)


__all__ = ["MODEL_NUMBER", "SUPPORTS_EXACT", "solve_exact"]
