"""M5 alns public entry."""


def solve_alns(problem, **kwargs):
    from models.routing.model_5.alns.solver import solve_alns as solve
    return solve(problem, **kwargs)


MODEL_NUMBER = 5
BATCH_MODE = 'flexible_cycle'
