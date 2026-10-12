"""ALNS entry for M3: 固定分仓、单批次容器循环."""
from models.common.cycle.scope import validate_problem
from models.common.cycle.scope import finish_result


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42):
    validate_problem(problem, 3)
    from models.routing.model_3.alns.single_batch import solve_alns as solve_local
    result = solve_local(problem, max_time=max_time, max_iterations=max_iterations, seed=seed)
    return finish_result(problem, result, solver_module=solve_local.__module__)
