"""Dependency-free joint batch and route adaptive search."""


def solve_alns(problem, **kwargs):
    from models.routing.model_6.alns.solver import solve_alns as solve
    return solve(problem, **kwargs)

MODEL_NUMBER = 6
BATCH_MODE = 'joint_candidate_batches'
