"""M6 adaptive large-neighborhood search; no mathematical solver imports."""
MODEL_NUMBER = 6
BATCH_MODE = 'no_batch'


def solve_alns(problem, **kwargs):
    from .solver import solve_alns as solve
    return solve(problem, **kwargs)
