"""ALNS entry for M5: 固定分仓、多批次有限容器循环."""
from ..model import validate_problem
from models.routing.common.cycle.scope import finish_result


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42, *, cache_descent_distances=False):
    validate_problem(problem)
    from models.routing.solvers.alns.solver import solve_alns as solve_shared
    options = dict(max_time=max_time, max_iterations=max_iterations, seed=seed)
    if cache_descent_distances:
        options['cache_descent_distances'] = True
    result = solve_shared(problem, **options)
    return finish_result(problem, result, solver_module=solve_shared.__module__)
