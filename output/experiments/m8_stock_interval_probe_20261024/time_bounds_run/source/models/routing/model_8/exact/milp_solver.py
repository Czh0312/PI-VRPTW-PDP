"""Direct Gurobi baseline for M8, with explicit settings and audited output."""
import time

from models.common.solver_config import (
    MilpOptions, validate_budget, read_milp_info, accept_milp_audit,
)
from models.common.cycle.scope import validate_problem
from models.routing.model_8.exact.solution import extract_solution

from models.common.cycle.joint import joint_empty_result
from models.routing.model_8.exact.formulation import build_model


def solve_milp(problem, time_limit=60, seed=42, verbose=False, *, milp_options=None,
               strengthening='physical'):
    """Solve one fixed-K projection; the public entry builds a single MILP for the entire K domain."""
    validate_problem(problem, 6)  # Internal joint projection, including M7/M8 children.
    if getattr(problem, 'uniform_cycle', False) or getattr(problem, 'calendar_cycle', False):
        raise ValueError('Use solve_exact(strategy="milp") to optimize all K in one MILP')
    if getattr(problem, 'calendar_model_number', None) != 8:
        raise ValueError('M8 baseline requires its own fixed-K projection')
    validate_budget(time_limit, seed)
    options = MilpOptions.resolve(milp_options)
    from models.routing.model_8.exact.constraints import validate_strengthening
    validate_strengthening(strengthening)
    begun = time.perf_counter()
    config = options.metadata(time_limit, seed)
    config['formulation_options'] = dict(compact_e2=options.formulation == 'compact', strengthening=strengthening)
    config['retained_model_bounds'] = False
    result = joint_empty_result(problem, 'exact')

    def finish(result):
        result['info'].update(solver_config=config, runtime=time.perf_counter()-begun)
        return result

    try:
        model, ctx = build_model(problem, verbose=verbose, deadline=begun+time_limit,
                                 compact_e2=options.formulation == 'compact', strengthening=strengthening)
    except TimeoutError:
        result['info'].update(status='TIME_LIMIT_BUILD')
        return finish(result)
    try:
        model.Params.Threads = options.threads
        model.Params.Seed = seed
        build_time = time.perf_counter()-begun
        model.Params.TimeLimit = max(0., time_limit-build_time)
        model.optimize()
        info = read_milp_info(model, build_time=build_time,
                             proof_scope='model_8_fixed_K_joint_cost_integer_time_MILP')
        info.update(compact_e2=ctx['parameters'].compact_e2,
                    candidate_slots=len(problem.operating_policy.batches))
        result['info'] = info
        extraction_begun = time.perf_counter()
        if model.SolCount:
            result = extract_solution(problem, model, ctx, info)
            accept_milp_audit(result, float(model.ObjVal))
        result['solver_module'] = __name__
        result['info']['extraction_audit_time'] = time.perf_counter()-extraction_begun
        return finish(result)
    finally:
        model.dispose()
