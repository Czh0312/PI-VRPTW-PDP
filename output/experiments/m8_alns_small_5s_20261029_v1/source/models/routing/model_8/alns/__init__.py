"""M8 alns public entry."""


def solve_alns(problem, **kwargs):
    from models.routing.model_8.alns.solver import solve_alns as solve
    return solve(problem, **kwargs)


MODEL_NUMBER = 8
BATCH_MODE = 'flexible_cycle'
