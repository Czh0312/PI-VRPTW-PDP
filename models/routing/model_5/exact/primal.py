"""Audited optional E1 reoptimization with a fixed complete E2 schedule."""
from models.common.cycle.scope import validate_problem


def reoptimize_e1(problem, initial_result, **options):
    validate_problem(problem, 5)
    from models.routing.model_5.exact.multibatch_e1_polish import reoptimize_e1 as polish
    return polish(problem, initial_result, **options)


def reoptimize_route_pool(problem, initial_result, **options):
    validate_problem(problem, 5)
    from models.routing.model_5.exact.multibatch_route_pool import reoptimize_route_pool as polish
    return polish(problem, initial_result, **options)


def solve_hybrid(problem, **options):
    """Explicit optional ALNS + Gurobi workflow with one total deadline."""
    validate_problem(problem, 5)
    from models.routing.model_5.exact.multibatch_hybrid import solve_hybrid as solve
    return solve(problem, **options)
