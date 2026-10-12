"""Exact backend for the complete M6 finite-calendar model."""


def solve_exact(problem, **kwargs):
    from models.routing.model_6.exact.solver import solve_exact as solve
    return solve(problem, **kwargs)

MODEL_NUMBER = 6
BATCH_MODE = 'joint_candidate_batches'
