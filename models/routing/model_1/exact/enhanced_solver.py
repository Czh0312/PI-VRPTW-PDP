"""Full-model solve and explicit schedule extraction, with honest proof metadata."""
from __future__ import annotations

import math
import time
from copy import deepcopy

from models.common.cycle.state import empty_result
from models.routing.model_1.exact.model import _build_complete_model as build_model
from models.routing.model_1.exact.initialization import construct_incumbent, install_start as _set_start
from models.routing.model_1.exact.solution import extract_solution
from models.common.cycle.audit import audit_solution
from models.common.cycle.precheck import check_necessary_conditions, rejected_result, travel_lower_bounds



def solve_exact(problem, time_limit=60, seed=42, verbose=False, initial_result=None, _travel_bounds=None,
                _domain_check=None, use_distance_certificate=True, distance_cuts=None,
                _single_batch_route_certificate=None, _single_batch_cycle_cuts=False,
                _single_batch_causal_bounds=False, _single_batch_e1_certificate=None,
                _single_batch_physical_certificate=None):
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('time_limit must be finite and positive')
    if _single_batch_e1_certificate is not None and (problem.model_number!=3 or len(problem.operating_policy.batches)!=1):
        raise ValueError('E1 certificate requires B1 M3')
    if not isinstance(_single_batch_cycle_cuts,bool):
        raise ValueError('Short-cycle cuts must be boolean')
    if _single_batch_cycle_cuts and (len(problem.operating_policy.batches)!=1 or problem.model_number not in (1,2,3,4)):
        raise ValueError('Short-cycle cuts require single-batch M1-M4')
    if not isinstance(_single_batch_causal_bounds,bool):
        raise ValueError('Causal step bounds must be boolean')
    if _single_batch_causal_bounds and (len(problem.operating_policy.batches)!=1 or problem.model_number not in (1,2,3,4)):
        raise ValueError('Causal step bounds require single-batch M1-M4')
    if distance_cuts not in (None, 'none', 'total', 'components'):
        raise ValueError('distance_cuts must be none, total or components')
    if distance_cuts in ('total', 'components') and not use_distance_certificate:
        raise ValueError('Distance cuts require the distance certificate')
    begun = time.perf_counter()
    travel_bounds = _travel_bounds or travel_lower_bounds(problem)
    domain_check = None
    if problem.free_assignment:
        from models.routing.model_1.exact.preprocessing.domains import assignment_domains, public_domain_check
        domain_check = _domain_check or assignment_domains(problem, travel_bounds)
        precheck = public_domain_check(domain_check)
    else:
        precheck = check_necessary_conditions(problem, travel_bounds)
    if not precheck['passed']:
        return rejected_result(problem, 'exact', precheck, time.perf_counter()-begun)
    import gurobipy as gp
    from gurobipy import GRB
    result = empty_result(problem, 'exact')
    result['precheck'] = precheck
    supplied = initial_result if initial_result is not None and audit_solution(problem, initial_result)['valid'] else None
    primal_begun = time.perf_counter()
    primal_cap = 1.0 if len(problem.operating_policy.batches) > 1 else .05
    primal_deadline = min(begun+time_limit, primal_begun+min(primal_cap, time_limit*.05))
    if problem.free_assignment:
        from models.common.cycle.assignment import assigned_problem, input_assignment, lift_result
        domains = domain_check['domains']
        original = input_assignment(problem)
        nearest = {c: min(sites, key=lambda s: problem.distance(s, c)+problem.distance(c, s))
                   for c, sites in domains.items()}
        original = {c: original[c] if original[c] in sites else nearest[c] for c, sites in domains.items()}
        generated = None
        for assignment in (original, nearest):
            candidate = construct_incumbent(assigned_problem(problem, assignment), primal_deadline)
            if candidate is not None:
                candidate = lift_result(problem, candidate, assignment)
                if candidate['audit']['valid'] and (generated is None or
                        candidate['info']['objective'] < generated['info']['objective']):
                    generated = candidate
    else:
        generated = construct_incumbent(problem, primal_deadline)
    if generated is not None and (supplied is None or generated['info']['objective'] < supplied['info']['objective']):
        supplied = generated
    result['info']['primal_seed_found'] = generated is not None
    result['info']['primal_runtime'] = time.perf_counter()-primal_begun
    result['info']['primal_seed_objective'] = generated['info']['objective'] if generated is not None else None
    result['info']['primal_seed_strategy'] = generated['info'].get('primal_strategy') if generated is not None else None
    certificate = None

    def preserve_incumbent():
        """A timeout or failed start completion must never discard a valid input."""
        info = result['info']
        if supplied is None or (result.get('audit', {}).get('valid') and
                info['objective'] <= supplied['info']['objective']+1e-9):
            return result
        kept = deepcopy(supplied)
        objective = supplied['info']['objective']
        bound = info.get('best_bound')
        conflict = info.get('proven_infeasible') or info.get('proven_optimal')
        if conflict:
            bound = None
        kept.update(solver='exact', precheck=precheck)
        if certificate is not None:
            kept['distance_certificate'] = certificate
        kept['info'] = {**info, 'objective': objective, 'proven_optimal': False,
                        'proven_infeasible': False, 'incumbent_source': supplied['info'].get('incumbent_source', 'audited_start'),
                        'best_bound': bound, 'runtime': time.perf_counter()-begun,
                        'mip_gap': max(0, objective-bound)/max(1e-9, abs(objective)) if bound is not None else None}
        if conflict:
            kept['info']['status'] = 'MODEL_START_CONFLICT'
        return kept
    try:
        distance_options = (dict(distance_cuts=distance_cuts, _distance_certificate=certificate)
                            if distance_cuts in ('total', 'components') else {})
        if _single_batch_cycle_cuts:
            distance_options['single_batch_cycle_cuts'] = True
        if _single_batch_causal_bounds:
            distance_options['single_batch_causal_bounds'] = True
        model, ctx = build_model(problem, verbose=verbose, deadline=begun+time_limit,
                                 travel_bounds=travel_bounds, domain_check=domain_check, **distance_options)
    except TimeoutError:
        result['info'].update(status='TIME_LIMIT_BUILD', runtime=time.perf_counter()-begun)
        if certificate is not None and certificate['bound'] > 0:
            result['info'].update(best_bound=certificate['bound'], best_bound_source='distance_certificate',
                                  gurobi_best_bound=None)
        return preserve_incumbent()
    try:
        if _single_batch_physical_certificate is not None or _single_batch_e1_certificate is not None:
            raise ValueError('Physical and E1 certificates require M3')
        if _single_batch_route_certificate is not None:
            from models.routing.model_1.exact.bounds.route_cg.columns import apply_route_bound
            result['info']['single_batch_route_floor'] = apply_route_bound(
                model, problem, _single_batch_route_certificate)
            if _single_batch_route_certificate.get('dual_cuts'):
                from models.routing.model_1.exact.branch_cut.dual_cuts import apply_dual_cuts
                result['info']['single_batch_route_dual_cuts'] = apply_dual_cuts(
                    model, problem, ctx, _single_batch_route_certificate)
        result['info']['mip_start_supplied'] = _set_start(problem, model, ctx, supplied)
        build_time = time.perf_counter()-begun
        model.Params.TimeLimit = max(0, time_limit-build_time)
        model.Params.Seed = int(seed)
        separator = ctx.get('capacity_separator')
        if separator is not None and separator.candidates:
            model.optimize(separator)
        else:
            model.optimize()
        status = {GRB.OPTIMAL: 'OPTIMAL', GRB.INFEASIBLE: 'INFEASIBLE', GRB.TIME_LIMIT: 'TIME_LIMIT',
                  GRB.INTERRUPTED: 'INTERRUPTED', GRB.INF_OR_UNBD: 'INF_OR_UNBD'}.get(model.Status, str(model.Status))
        info = result['info']
        info.update(status=status, proven_optimal=model.Status == GRB.OPTIMAL,
                    proven_infeasible=model.Status == GRB.INFEASIBLE,
                    proof_scope='joint_customer_assignment_integer_time_cycle' if problem.free_assignment else
                                'full_integer_time_multibatch_indexed',
                    build_time=build_time, mip_runtime=model.Runtime,
                    mip_work=model.Work,
                    mip_iterations=model.IterCount,
                    mip_node_count=model.NodeCount,
                    runtime=time.perf_counter()-begun, sol_count=model.SolCount,
                    variables=model.NumVars, constraints=model.NumConstrs+model.NumGenConstrs,
                    linear_nonzeros=model.NumNZs,
                    general_constraints=model.NumGenConstrs, e2_route_slots=len(ctx['e2']),
                    single_batch_linear_link_mode=ctx.get('single_batch_linear_links', False),
                    e2_indexed_internal_arcs=ctx.get('indexed_e2_internal_arcs'),
                    e2_global_internal_arcs=len(ctx.get('global_e2_arcs', {})),
                    e2_departure_load_variables=sum(len(r.get('departure_loads', ())) for r in ctx['e2']),
                    e2_site_flow=ctx.get('e2_site_flow', False),
                    e2_tight_route_bounds=ctx.get('tight_e2_route_bounds', False),
                    e2_short_cycle_cuts=ctx.get('e2_short_cycle_cuts',False),
                    e2_short_cycle_rows=len(ctx.get('e2_short_cycle_rows',())),
                    e2_causal_step_bounds=ctx.get('e2_causal_step_bounds',False),
                    e2_causal_largest_m=ctx.get('e2_causal_largest_m'),
                    e2_route_identity_ranges_reduced=sum(v.LB > 1 or v.UB < len(ctx['e2'])
                                                        for v in ctx.get('e2_route_identities', {}).values()),
                    e2_site_flow_arcs=len(ctx.get('e2_site_arcs', {})),
                    e2_site_flow_variables=sum(v.VType == GRB.CONTINUOUS
                                               for v in ctx.get('e2_site_arcs', {}).values()),
                    distance_cut_mode=ctx.get('distance_cut_mode', 'none'),
                    distance_cuts_added=len(ctx.get('distance_certificate_rows', {})),
                    time_tick_seconds=problem.operating_policy.time_tick_seconds,
                    formulation_features=['sparse_time_arcs', 'vehicle_symmetry', 'causal_steps']+
                        (['B1_supply_and_load_bounds', 'trip_count_symmetry', 'triangular_trip_slots',
                          'tight_causal_ranks'] if ctx.get('single_batch_bounds') else [])+
                        (['multibatch_task_time_bounds'] if len(problem.operating_policy.batches) > 1 else [])+
                        (['B1_all_take_assignment_quantities' if problem.free_assignment else
                          'fixed_B1_pickup_quantities'] if len(problem.operating_policy.batches) == 1 else [])+
                        (['shared_E2_task_states_and_internal_arcs'] if ctx['shared_e2'] else [])+
                        (['global_E2_arcs_with_route_identity'] if ctx.get('compressed_e2_routes') else [])+
                        (['B1_linear_E2_connections'] if ctx.get('single_batch_linear_links') else [])+
                        (['factored_E2_departure_loads'] if ctx.get('factored_e2_loads') else [])+
                        (['B1_warehouse_conservation'] if ctx.get('e2_site_flow') else [])+
                        (['B1_task_route_identity_bounds'] if ctx.get('tight_e2_route_bounds') else [])+
                        (['B1_static_two_cycle_cuts'] if ctx.get('e2_short_cycle_rows') else [])+
                        (['B1_causal_step_endpoint_bounds'] if ctx.get('e2_causal_step_bounds') else [])+
                        (['M5_distance_certificate_cuts'] if ctx.get('distance_certificate_rows') else [])+
                        (['multibatch_capacity_activation', 'multibatch_load_bounds_and_arc_pruning']
                         if ctx.get('capacity_activation_cuts') else [])+
                        (['shared_order_cutoff_states'] if ctx.get('shared_cutoff') else [])+
                        (['multibatch_linear_E2_links'] if ctx.get('linear_e2_links') else [])+
                        (['multibatch_trip_count_symmetry_and_slots'] if ctx.get('trip_count_symmetry')
                         and len(problem.operating_policy.batches) > 1 else [])+
                        (['separated_order_subset_capacity_cuts'] if separator is not None and separator.candidates else [])+
                        (['joint_customer_assignment'] if problem.free_assignment else []),
                    shared_e2_arcs=ctx['shared_e2_arcs'],
                    capacity_cut_candidates=len(separator.candidates) if separator is not None else 0,
                    capacity_cuts_added=len(separator.added) if separator is not None else 0,
                    root_optimal_callback_observed=getattr(separator, 'root_optimal_callback_observed', None),
                    root_optimal_callback_seconds=getattr(separator, 'root_optimal_callback_seconds', None),
                    e2_max_trips_per_vehicle=problem.operating_policy.e2_max_trips_per_vehicle)
        if separator is not None and separator.error:
            info.update(status='CAPACITY_CUT_ERROR', proven_optimal=False, proven_infeasible=False,
                        capacity_cut_error=separator.error)
        try:
            info['best_bound'] = float(model.ObjBound) if math.isfinite(model.ObjBound) else None
        except (AttributeError, gp.GurobiError):
            pass
        if certificate is not None:
            info['gurobi_best_bound'] = info.get('best_bound')
            certificate_bound = certificate['bound']
            if certificate_bound > 0 and (info['best_bound'] is None or certificate_bound > info['best_bound']):
                info.update(best_bound=certificate_bound, best_bound_source='distance_certificate')
            else:
                info['best_bound_source'] = 'gurobi' if info['best_bound'] is not None else None
        if not model.SolCount:
            info['proven_optimal'] = False
            return preserve_incumbent()
        gap = model.MIPGap
        info.update(objective=model.ObjVal, mip_gap=gap if math.isfinite(gap) else None)
        if certificate is not None:
            info['gurobi_mip_gap'] = info['mip_gap']
        if certificate is not None and info['best_bound'] is not None:
            info['mip_gap'] = max(0, info['objective']-info['best_bound'])/max(1e-9, abs(info['objective']))
        extract_solution(problem, model, ctx, result)
        audit = audit_solution(problem, result)
        result.update(audit=audit, cutoff_snapshots=audit.get('cutoff_snapshots', []),
                      depot_releases=audit.get('depot_releases', []))
        if not audit['valid']:
            info.update(status='AUDIT_FAILED', proven_optimal=False)
        return preserve_incumbent()
    finally:
        model.dispose()
