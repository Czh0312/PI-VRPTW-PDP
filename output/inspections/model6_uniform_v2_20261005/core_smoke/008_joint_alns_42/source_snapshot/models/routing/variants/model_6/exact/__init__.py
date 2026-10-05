"""Exact backend for the complete M6 finite-calendar model."""


def solve_exact(problem, **kwargs):
    from .solver import solve_exact as solve
    return solve(problem, **kwargs)
