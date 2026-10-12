"""M7 exact public entry."""


def solve_exact(problem, **kwargs):
    from models.routing.model_7.exact.solver import solve_exact as solve
    return solve(problem, **kwargs)


MODEL_NUMBER = 7
BATCH_MODE = 'uniform_independent_cutoffs'
