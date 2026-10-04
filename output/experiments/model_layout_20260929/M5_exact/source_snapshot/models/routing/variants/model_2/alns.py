"""ALNS entry for M2: 自由选仓、纯两级配送（一次性包装对照）."""
from .model import validate_problem
from models.routing.common.cycle.scope import finish_result


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42):
    validate_problem(problem)
    from models.routing.common.cycle.assignment import solve_alns as solve_shared
    result = solve_shared(problem, max_time=max_time, max_iterations=max_iterations, seed=seed)
    return finish_result(problem, result)
