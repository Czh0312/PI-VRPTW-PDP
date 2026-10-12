"""M8 exact public entry."""


def solve_exact(problem, **kwargs):
    from models.routing.model_8.exact.solver import solve_exact as solve
    return solve(problem, **kwargs)


MODEL_NUMBER = 8
BATCH_MODE = 'flexible_cycle'
