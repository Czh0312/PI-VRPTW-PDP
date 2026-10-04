"""ALNS entry for M1: 固定分仓、纯两级配送（一次性包装对照）."""
from .model import validate_problem
from models.routing.common.cycle.scope import finish_result


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42):
    validate_problem(problem)
    from models.routing.solvers.alns.solver import solve_alns as solve_shared
    result = solve_shared(problem, max_time=max_time, max_iterations=max_iterations, seed=seed)
    return finish_result(problem, result, solver_module=solve_shared.__module__)
