"""M1 public entry: explicit algorithm selection and stable result identity."""
import time

from models.common.cycle.scope import validate_problem, finish_result
from models.common.solver_config import record_strategy


def solve_exact(problem, time_limit=60, seed=42, verbose=False, *, strategy='auto', milp_options=None):
    begun = time.perf_counter()
    validate_problem(problem, 1)
    if strategy not in ('auto', 'milp'):
        raise ValueError('Unsupported M1 exact strategy: '+str(strategy))
    if strategy != 'milp' and milp_options is not None:
        raise ValueError('milp_options requires strategy="milp"')
    if strategy == 'milp':
        from models.routing.model_1.exact.milp_solver import solve_milp as solve_local
        result = solve_local(problem, time_limit=time_limit, seed=seed, verbose=verbose, milp_options=milp_options)
    else:
        from models.routing.model_1.exact.enhanced_solver import solve_exact as solve_local
        result = solve_local(problem, time_limit=time_limit, seed=seed, verbose=verbose)
    result = finish_result(problem, result, solver_module=solve_local.__module__)
    result['info']['runtime'] = time.perf_counter()-begun
    return record_strategy(result, strategy, time_limit=time_limit, seed=seed)
