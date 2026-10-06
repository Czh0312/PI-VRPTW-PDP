"""Dependency-free joint batch and route adaptive search."""


def solve_alns(problem, **kwargs):
    from .solver import solve_alns as solve
    return solve(problem, **kwargs)
