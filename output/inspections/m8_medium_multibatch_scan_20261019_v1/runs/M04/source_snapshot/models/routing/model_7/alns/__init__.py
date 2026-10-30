"""M7 alns public entry."""


def solve_alns(problem, **kwargs):
    from models.routing.model_7.alns.solver import solve_alns as solve
    return solve(problem, **kwargs)


MODEL_NUMBER = 7
BATCH_MODE = 'uniform_independent_cutoffs'
