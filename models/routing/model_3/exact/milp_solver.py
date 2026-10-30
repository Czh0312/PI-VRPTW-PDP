"""Complete M3 MILP with bounded input-derived acceleration and audited output."""
import time

from models.common.solver_config import (
    MilpOptions, validate_budget, read_milp_info, accept_milp_audit,
)
from models.common.cycle.scope import validate_problem
from models.routing.model_3.exact.solution import extract_solution

from models.common.cycle.audit import audit_solution
from models.common.cycle.state import empty_result
from models.common.cycle.precheck import check_necessary_conditions, rejected_result, travel_lower_bounds
from models.routing.model_3.exact.formulation import build_model


def solve_milp(problem, time_limit=60, seed=42, verbose=False, *, milp_options=None,
               route_strengthening=True, resource_flows=True):
    """Solve the complete model, including free assignment where applicable."""
    validate_problem(problem, 3)
    validate_budget(time_limit, seed)
    options = MilpOptions.resolve(milp_options)
    if not isinstance(route_strengthening, bool):
        raise ValueError('route_strengthening must be boolean')
    if not isinstance(resource_flows, bool):
        raise ValueError('resource_flows must be boolean')
    if route_strengthening and (milp_options is None or
            isinstance(milp_options, dict) and 'formulation' not in milp_options):
        # Shared internal arcs are an equivalent formulation at every size.
        # Explicit expanded/compact requests remain reproducible.
        options = MilpOptions(threads=options.threads, formulation='compact')
    begun = time.perf_counter()
    config = options.metadata(time_limit, seed)

    def finish(result):
        result['info'].update(solver_config=config, runtime=time.perf_counter()-begun)
        return result

    travel = travel_lower_bounds(problem)
    domains = None
    if problem.free_assignment:
        from models.routing.model_3.exact.preprocessing.domains import assignment_domains, public_domain_check
        domains = assignment_domains(problem, travel)
        precheck = public_domain_check(domains)
    else:
        precheck = check_necessary_conditions(problem, travel)
    if not precheck['passed']:
        return finish(rejected_result(problem, 'exact', precheck, time.perf_counter()-begun))
    result = empty_result(problem, 'exact')
    result['precheck'] = precheck
    formulation = dict(compress_routes=options.formulation == 'compact', capacity_links=route_strengthening,
                       resource_flows=route_strengthening and resource_flows,
                       precedence_links=route_strengthening)
    config['formulation_options'] = formulation
    try:
        model, ctx = build_model(problem, verbose=verbose, deadline=begun+time_limit,
                                 travel_bounds=travel, domain_check=domains, **formulation)
    except TimeoutError:
        result['info'].update(status='TIME_LIMIT_BUILD')
        return finish(result)
    build_time = time.perf_counter()-begun
    try:
        strengthening_info = dict(applied=False, reason='disabled')
        if route_strengthening:
            from models.routing.model_3.exact.route_partition import strengthen
            remaining = max(0., begun+time_limit-time.perf_counter())
            strengthening_info = strengthen(problem, model, ctx,
                deadline=min(begun+time_limit, time.perf_counter()+min(5., remaining*.25)),
                threads=options.threads, seed=seed)
        config['route_strengthening'] = strengthening_info
        start_info = dict(supplied=False, reason='not_requested')
        if strengthening_info.get('applied') and getattr(problem, 'cost_objective', False):
            from models.routing.model_3.exact.route_partition import prepare_start
            remaining = max(0., begun+time_limit-time.perf_counter())
            start_info = prepare_start(problem, model, ctx,
                strengthening_info.get('selected_trip_masks'),
                deadline=min(begun+time_limit, time.perf_counter()+min(5., remaining*.25)),
                threads=options.threads, seed=seed,
                sequences=strengthening_info.get('selected_trip_sequences'))
        config['internal_start'] = start_info
        config['mip_start'] = start_info['supplied']
        if strengthening_info.get('applied'):
            config['model_preprocessing'] = 'safe_bounds_route_flows_precedence_and_capacity_cuts'
        model.Params.Threads = options.threads
        model.Params.Seed = seed
        preparation_time = time.perf_counter()-begun
        model.Params.TimeLimit = max(0., time_limit-preparation_time)
        # The optional start comes from a fresh restricted copy, never history.
        model.optimize()
        result['info'] = read_milp_info(model, build_time=build_time,
            proof_scope='full_model_3_integer_time_MILP')
        result['info']['route_strengthening'] = strengthening_info
        result['info'].update(internal_start=start_info, preparation_time=preparation_time,
                              mip_start_supplied=start_info['supplied'])
        config['compressed_routes'] = bool(ctx.get('compressed_e2_routes'))
        config['resource_flows'] = ctx['resource_flows']
        config['precedence_links'] = ctx['precedence_links']
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
