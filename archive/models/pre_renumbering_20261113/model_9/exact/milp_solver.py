"""Fixed-K M9 diagnostic; the public solver optimizes the full K domain."""
import time

from models.common.cycle.joint import joint_empty_result
from models.common.solver_config import (
    MilpOptions, validate_budget, read_milp_info, accept_milp_audit,
)
from models.routing.model_9.exact.formulation import build_model
from models.routing.model_9.exact.solution import extract_solution


def solve_milp(problem, time_limit=60, seed=42, verbose=False, *,
               milp_options=None, strengthening='physical_time'):
    if (getattr(problem, 'calendar_cycle', False)
            or getattr(problem, 'calendar_model_number', None) != 9):
        raise ValueError('M9 solve_milp requires its own fixed-K projection')
    validate_budget(time_limit, seed)
    options = MilpOptions.resolve(milp_options)
    begun = time.perf_counter()
    config = options.metadata(time_limit, seed)
    config.update(retained_model_bounds=False, formulation_options=dict(
        compact_e2=options.formulation == 'compact', strengthening=strengthening,
        free_customer_assignment=True))

    def finish(result):
        result['solver_module'] = __name__
        result['info'].update(solver_config=config, runtime=time.perf_counter()-begun)
        return result

    try:
        model, ctx = build_model(problem, verbose=verbose, deadline=begun+time_limit,
                                compact_e2=options.formulation == 'compact',
                                strengthening=strengthening)
    except TimeoutError:
        result = joint_empty_result(problem, 'exact')
        result['info'].update(status='TIME_LIMIT_BUILD')
        return finish(result)
    try:
        model.Params.Threads = options.threads
        model.Params.Seed = seed
        build_time = time.perf_counter()-begun
        model.Params.TimeLimit = max(0., time_limit-build_time)
        model.optimize()
        info = read_milp_info(model, build_time=build_time,
                             proof_scope='model_9_fixed_K_free_joint_cost_integer_time_MILP')
        result = joint_empty_result(problem, 'exact')
        result['info'] = info
        if model.SolCount:
            result = extract_solution(problem, model, ctx, info)
            accept_milp_audit(result, float(model.ObjVal))
        return finish(result)
    finally:
        model.dispose()
