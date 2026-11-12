"""Native MILP entry; solver backends are imported only when requested."""
MODEL_NUMBER = 10
BATCH_MODE = 'no_batch'


def solve_exact(problem, **kwargs):
    from .solver import solve_exact as solve
    return solve(problem, **kwargs)
