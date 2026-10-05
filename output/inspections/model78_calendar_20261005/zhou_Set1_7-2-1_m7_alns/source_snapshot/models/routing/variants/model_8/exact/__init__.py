"""M8 exact public entry."""


def solve_exact(problem, **kwargs):
    from .solver import solve_exact as solve
    return solve(problem, **kwargs)

