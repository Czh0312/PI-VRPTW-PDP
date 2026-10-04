"""Exact solver entry for M5: 固定分仓、多批次有限容器循环."""
from .model import validate_problem
from models.routing.common.cycle.scope import finish_result


def solve_exact(problem, time_limit=60, seed=42, verbose=False):
    validate_problem(problem)
    from models.routing.common.cycle.exact_solver import solve_exact as solve_shared
    result = solve_shared(problem, time_limit=time_limit, seed=seed, verbose=verbose)
    return finish_result(problem, result)
