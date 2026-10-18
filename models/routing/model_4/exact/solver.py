"""Exact solver entry for M4: 自由选仓、单批次容器循环."""
from models.common.cycle.scope import validate_problem
from models.common.cycle.scope import finish_result


def solve_exact(problem, time_limit=60, seed=42, verbose=False):
    validate_problem(problem, 4)
    from models.routing.model_4.exact.search.assignment import solve_exact as solve_local
    result = solve_local(problem, time_limit=time_limit, seed=seed, verbose=verbose)
    return finish_result(problem, result, solver_module=solve_local.__module__)
