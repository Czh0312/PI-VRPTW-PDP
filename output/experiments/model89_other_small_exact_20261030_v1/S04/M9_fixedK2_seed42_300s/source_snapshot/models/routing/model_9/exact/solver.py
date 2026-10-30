"""Direct Gurobi optimization of M9, with one optimize over the full K domain."""
import time
from functools import partial

from models.common.cycle.scope import validate_problem
from models.common.solver_config import record_strategy

UNIFIED_BATCH_COUNT = True


def solve_exact(problem, time_limit=60, seed=42, verbose=False, *, strategy='milp',
                milp_options=None, strengthening='physical_time'):
    begun = time.perf_counter()
    validate_problem(problem, 9)
    if strategy not in ('milp', 'auto'):
        raise ValueError('M9 supports direct Gurobi: strategy=milp or auto')
    from models.common.cycle.unified_milp import solve_unified_milp
    from models.routing.model_9.exact.formulation import build_model
    from models.routing.model_9.exact.constraints import validate_strengthening
    from models.routing.model_9.exact.solution import extract_solution
    validate_strengthening(strengthening)
    result = solve_unified_milp(
        problem, build_fixed=partial(build_model, strengthening=strengthening),
        extract_fixed=extract_solution, time_limit=time_limit, seed=seed,
        verbose=verbose, milp_options=milp_options)
    result['solver_module'] = __name__
    result['info']['runtime'] = time.perf_counter()-begun
    record_strategy(result, 'milp', time_limit=time_limit, seed=seed)
    result['info']['exact_strategy_requested'] = strategy
    result['info']['solver_config']['formulation_options']['strengthening'] = strengthening
    result['info']['solver_config']['formulation_options']['free_customer_assignment'] = True
    return result
