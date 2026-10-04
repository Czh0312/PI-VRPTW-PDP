"""Audited optional E1 reoptimization with a fixed complete E2 schedule."""
from ..model import validate_problem


def reoptimize_e1(problem, initial_result, **options):
    validate_problem(problem)
    from models.routing.solvers.exact.multibatch_e1_polish import reoptimize_e1 as polish
    return polish(problem, initial_result, **options)


def reoptimize_route_pool(problem, initial_result, **options):
    validate_problem(problem)
    from models.routing.solvers.exact.multibatch_route_pool import reoptimize_route_pool as polish
    return polish(problem, initial_result, **options)
