"""M5 public entry: explicit algorithm selection and stable result identity."""
import time

from models.common.cycle.scope import validate_problem, finish_result
from models.common.solver_config import record_strategy


def solve_exact(problem, time_limit=60, seed=42, verbose=False, *, strategy='auto', milp_options=None, distance_cuts=None, initial_result=None,
                e1_polish_time=0, route_pool_time=0):
    begun = time.perf_counter()
    validate_problem(problem, 5)
    if strategy not in ('auto', 'milp'):
        raise ValueError('Unsupported M5 exact strategy: '+str(strategy))
    if strategy != 'milp' and milp_options is not None:
        raise ValueError('milp_options requires strategy="milp"')
    if strategy == 'milp':
        if initial_result is not None or e1_polish_time != 0 or route_pool_time != 0 or distance_cuts not in (None, 'none'):
            raise ValueError('Direct MILP baseline does not accept starts, primal improvement or distance certificates')
        from models.routing.model_5.exact.milp_solver import solve_milp as solve_local
        result = solve_local(problem, time_limit=time_limit, seed=seed, verbose=verbose, milp_options=milp_options)
    else:
        from models.routing.model_5.exact.primal.solve_with_improvement import solve_exact as solve_local
        result = solve_local(problem, time_limit=time_limit, seed=seed, verbose=verbose, distance_cuts=distance_cuts,
                             initial_result=initial_result, e1_polish_time=e1_polish_time, route_pool_time=route_pool_time)
    result = finish_result(problem, result, solver_module=solve_local.__module__)
    result['info']['runtime'] = time.perf_counter()-begun
    return record_strategy(result, strategy, time_limit=time_limit, seed=seed)
