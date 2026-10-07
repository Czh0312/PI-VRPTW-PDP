"""Public M8 entry; exact and ALNS dependencies are loaded separately."""
from .model import MODEL_NUMBER

BATCH_MODE = 'flexible_cycle'


def solve_exact(problem, **kwargs):
    from .exact.solver import solve_exact as solve
    return solve(problem, **kwargs)


def solve_alns(problem, **kwargs):
    from .alns.solver import solve_alns as solve
    return solve(problem, **kwargs)

