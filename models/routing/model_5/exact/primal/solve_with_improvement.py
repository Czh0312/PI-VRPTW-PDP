"""Existing M5 solve with optional, budgeted primal improvement stages."""
from models.common.cycle.scope import validate_problem


def solve_exact(problem, time_limit=60, seed=42, verbose=False, *, distance_cuts=None,
                initial_result=None, e1_polish_time=0, route_pool_time=0):
    validate_problem(problem, 5)
    import math
    import time
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('time_limit must be finite and positive')
    if not math.isfinite(e1_polish_time) or e1_polish_time < 0:
        raise ValueError('e1_polish_time must be finite and nonnegative')
    if not math.isfinite(route_pool_time) or route_pool_time < 0:
        raise ValueError('route_pool_time must be finite and nonnegative')
    begun, polish_info, pool_info = time.perf_counter(), None, None
    if e1_polish_time > 0 or route_pool_time > 0:
        if initial_result is None:
            from models.routing.model_5.exact.initialization import construct_incumbent
            initial_result = construct_incumbent(problem, begun+min(1, time_limit*.05))
        if initial_result is not None:
            from models.routing.model_5.exact.primal import reoptimize_e1
            remaining = max(0, time_limit-(time.perf_counter()-begun))
            if remaining > 0 and e1_polish_time > 0:
                initial_result = reoptimize_e1(problem, initial_result,
                    time_limit=min(e1_polish_time, remaining/3), seed=seed, verbose=verbose, retime_e2=True)
                polish_info = dict(initial_result['info'])
            remaining = max(0, time_limit-(time.perf_counter()-begun))
            if remaining > 0 and route_pool_time > 0:
                from models.routing.model_5.exact.primal import reoptimize_route_pool
                initial_result = reoptimize_route_pool(problem, initial_result,
                    time_limit=min(route_pool_time, remaining/3), seed=seed, verbose=verbose)
                pool_info = dict(initial_result['info'])
    from models.routing.model_5.exact.enhanced_solver import solve_exact as solve_local
    options = dict(distance_cuts=distance_cuts) if distance_cuts is not None else {}
    if initial_result is not None:
        options['initial_result'] = initial_result
    remaining = max(1e-12, time_limit-(time.perf_counter()-begun)) if e1_polish_time > 0 or route_pool_time > 0 else time_limit
    result = solve_local(problem, time_limit=remaining, seed=seed, verbose=verbose, **options)
    if e1_polish_time > 0:
        result['info'].update(e1_polish=polish_info, runtime=time.perf_counter()-begun)
    if route_pool_time > 0:
        result['info'].update(route_pool=pool_info, runtime=time.perf_counter()-begun)
    return result
