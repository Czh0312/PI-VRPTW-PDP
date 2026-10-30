"""M8: direct Gurobi solve of the complete mathematical model."""
import time
from models.common.cycle.scope import validate_problem, finish_result
from models.common.solver_config import record_strategy

UNIFIED_BATCH_COUNT = True


def solve_exact(problem, time_limit=60, seed=42, verbose=False, *, strategy='milp', milp_options=None):
    begun = time.perf_counter()
    validate_problem(problem, 8)
    if strategy not in ('milp', 'auto'):
        raise ValueError('Only direct Gurobi is supported; custom exact strategies were retired')
    from models.common.cycle.unified_milp import solve_unified_milp
    from models.routing.model_8.exact.formulation import build_model
    from models.routing.model_8.exact.solution import extract_solution
    result = solve_unified_milp(problem, build_fixed=build_model, extract_fixed=extract_solution,
                                time_limit=time_limit, seed=seed, verbose=verbose, milp_options=milp_options)
    result['solver_module'] = __name__
    result['info']['runtime'] = time.perf_counter()-begun
    record_strategy(result, 'milp', time_limit=time_limit, seed=seed)
    result['info']['exact_strategy_requested'] = strategy
    return result
