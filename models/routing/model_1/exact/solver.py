"""M1: direct Gurobi solve of the complete mathematical model."""
import time
from models.common.cycle.scope import validate_problem, finish_result
from models.common.solver_config import record_strategy


def solve_exact(problem, time_limit=60, seed=42, verbose=False, *, strategy='milp', milp_options=None):
    begun = time.perf_counter()
    validate_problem(problem, 1)
    if strategy not in ('milp', 'auto'):
        raise ValueError('Only direct Gurobi is supported; custom exact strategies were retired')
    from models.routing.model_1.exact.milp_solver import solve_milp
    result = solve_milp(problem, time_limit=time_limit, seed=seed, verbose=verbose, milp_options=milp_options)
    result = finish_result(problem, result, solver_module=solve_milp.__module__)
    result['info']['runtime'] = time.perf_counter()-begun
    record_strategy(result, 'milp', time_limit=time_limit, seed=seed)
    result['info']['exact_strategy_requested'] = strategy
    return result
