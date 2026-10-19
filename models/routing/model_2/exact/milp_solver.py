"""Direct Gurobi baseline for M2, with explicit settings and audited output."""
import time

from models.common.solver_config import (
    MilpOptions, validate_budget, read_milp_info, accept_milp_audit,
)
from models.common.cycle.scope import validate_problem
from models.routing.model_2.exact.solution import extract_solution

from models.common.cycle.audit import audit_solution
from models.common.cycle.state import empty_result
from models.common.cycle.precheck import check_necessary_conditions, rejected_result, travel_lower_bounds
from models.routing.model_2.exact.formulation import build_model


def solve_milp(problem, time_limit=60, seed=42, verbose=False, *, milp_options=None):
    """Solve the complete model, including free assignment where applicable."""
    validate_problem(problem, 2)
    validate_budget(time_limit, seed)
    options = MilpOptions.resolve(milp_options)
    begun = time.perf_counter()
    config = options.metadata(time_limit, seed)

    def finish(result):
        result['info'].update(solver_config=config, runtime=time.perf_counter()-begun)
        return result

    travel = travel_lower_bounds(problem)
    domains = None
    if problem.free_assignment:
        from models.routing.model_2.exact.preprocessing.domains import assignment_domains, public_domain_check
        domains = assignment_domains(problem, travel)
        precheck = public_domain_check(domains)
    else:
        precheck = check_necessary_conditions(problem, travel)
    if not precheck['passed']:
        return finish(rejected_result(problem, 'exact', precheck, time.perf_counter()-begun))
    result = empty_result(problem, 'exact')
    result['precheck'] = precheck
    formulation = dict(compress_routes=options.formulation == 'compact', capacity_cuts=False,
                       short_cycle_cuts=False, causal_step_bounds=False, site_flow=False,
                       tight_route_bounds=False)
    config['formulation_options'] = formulation
    try:
        model, ctx = build_model(problem, verbose=verbose, deadline=begun+time_limit,
                                 travel_bounds=travel, domain_check=domains, **formulation)
    except TimeoutError:
        result['info'].update(status='TIME_LIMIT_BUILD')
        return finish(result)
    try:
        model.Params.Threads = options.threads
        model.Params.Seed = seed
        build_time = time.perf_counter()-begun
        model.Params.TimeLimit = max(0., time_limit-build_time)
        # No custom callback, MIP start, route-pool or external bound in this path.
        model.optimize()
        result['info'] = read_milp_info(model, build_time=build_time,
            proof_scope='full_model_2_integer_time_MILP')
        config['compressed_routes'] = bool(ctx.get('compressed_e2_routes'))
        extraction_begun = time.perf_counter()
        if model.SolCount:
            extract_solution(problem, model, ctx, result)
            audit = audit_solution(problem, result)
            result.update(audit=audit, cutoff_snapshots=audit.get('cutoff_snapshots', []),
                          depot_releases=audit.get('depot_releases', []))
            accept_milp_audit(result, float(model.ObjVal))
        result['info']['extraction_audit_time'] = time.perf_counter()-extraction_begun
        return finish(result)
    finally:
        model.dispose()
