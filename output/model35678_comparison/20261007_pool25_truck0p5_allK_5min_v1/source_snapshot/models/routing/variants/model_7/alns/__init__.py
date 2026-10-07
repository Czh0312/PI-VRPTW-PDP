"""M7 alns public entry."""


def solve_alns(problem, **kwargs):
    from .solver import solve_alns as solve
    return solve(problem, **kwargs)

