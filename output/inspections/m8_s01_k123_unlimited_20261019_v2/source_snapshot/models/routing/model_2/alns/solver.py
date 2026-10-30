"""ALNS entry for M2: 自由选仓、纯两级配送（一次性包装对照）."""
from models.common.cycle.scope import validate_problem, finish_result


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42):
    validate_problem(problem, 2)
    from models.routing.model_2.alns.assignment.continuous_search import solve_alns as solve_local

    result = solve_local(problem, max_time=max_time, max_iterations=max_iterations, seed=seed)
    return finish_result(problem, result, solver_module=solve_local.__module__)
