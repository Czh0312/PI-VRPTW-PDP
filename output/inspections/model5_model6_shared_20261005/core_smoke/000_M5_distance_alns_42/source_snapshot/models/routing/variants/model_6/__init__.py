"""Lazy solver loading: ALNS does not import Gurobi or the exact backend."""
from .model import MODEL_NUMBER

BATCH_MODE = 'joint_candidate_batches'


def solve_exact(problem, **kwargs):
    from .exact.solver import solve_exact as solve
    return solve(problem, **kwargs)


def solve_alns(problem, **kwargs):
    from .alns.solver import solve_alns as solve
    return solve(problem, **kwargs)
