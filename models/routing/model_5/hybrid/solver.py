"""Fresh ALNS, audited route-pool improvements, and complete-domain certificates.

All stages share one deadline. The explicit entry requires Gurobi; the default
ALNS entry remains unchanged. Failure to reach a target is reported as such.
"""
from copy import deepcopy
import math
from pathlib import Path
import time

from models.common.cycle.audit import audit_solution


def solve_hybrid(problem, **options):
    """Own live continuation models only for this bounded solver call."""
    sessions = {}
    try:
        return _solve_hybrid(problem, _route_sessions=sessions, **options)
    finally:
        for session in sessions.values():
            session.dispose()


def _solve_hybrid(problem, *, time_limit=660, alns_time_limit=15, primal_time_limit=180,
        cover_time_limit=360, conditional_time_limit=105, target_gap=.05,
        seed=42, max_subset_cuts_per_batch=6, max_conditions=3,
        adaptive_finish=True, verbose=False, diagnostics_dir=None, extra_slots_per_vehicle=0,
        route_pool_max_columns=256, max_supply_conjunctions=0, subset_time_fraction=.25,
        adaptive_pricing_mode='column_generation', count_dual_repair=False,
        capacity_dual_repair=False, route_cover_batch_pricing=True,
        primal_revisit_time_limit=0, primal_revisit_gap_limit=.10,
        retain_all_pricing_columns=False, primal_mip_focus=0, _route_sessions=None):
    from models.routing.service import solve_routing
    from models.routing.model_5.exact.primal.route_pool import reoptimize_route_pool
    from models.routing.model_5.exact.bounds.route_cover_cg.route_cg import route_cover_certificate, site_route_cover
    from models.routing.model_5.exact.bounds.route_cover_cg.capacity_cuts import select_capacity_subsets
    from models.routing.model_5.exact.bounds.inventory.conditional_inventory import refine_conditional_bound
    from models.routing.model_5.exact.bounds.route_cg.pickup_pricing import route_context
    if (problem.model_number != 5 or problem.free_assignment or not problem.reusable
            or len(problem.operating_policy.batches) <= 1):
        raise ValueError('Hybrid requires fixed reusable multibatch M5')
    for name, value in [('time_limit', time_limit), ('alns_time_limit', alns_time_limit)]:
        if not math.isfinite(value) or value <= 0:
            raise ValueError(name+' must be finite and positive')
    for name, value in [('primal_time_limit', primal_time_limit), ('cover_time_limit', cover_time_limit),
                        ('conditional_time_limit', conditional_time_limit),
                        ('primal_revisit_time_limit', primal_revisit_time_limit)]:
        if not math.isfinite(value) or value < 0:
            raise ValueError(name+' must be finite and nonnegative')
    if not math.isfinite(target_gap) or not 0 <= target_gap < 1:
        raise ValueError('target_gap must lie in [0, 1)')
    for name, value in [('max_subset_cuts_per_batch', max_subset_cuts_per_batch), ('max_conditions', max_conditions),
                        ('extra_slots_per_vehicle', extra_slots_per_vehicle),
                        ('max_supply_conjunctions', max_supply_conjunctions)]:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(name+' must be a nonnegative integer')
    if not isinstance(route_pool_max_columns, int) or isinstance(route_pool_max_columns, bool) or route_pool_max_columns < 1:
        raise ValueError('route_pool_max_columns must be a positive integer')
    if (not isinstance(subset_time_fraction, (int, float)) or isinstance(subset_time_fraction, bool)
            or not math.isfinite(subset_time_fraction) or not 0 <= subset_time_fraction < 1):
        raise ValueError('subset_time_fraction must lie in [0, 1)')
    if adaptive_pricing_mode not in ('column_generation', 'fixed_duals', 'resume'):
        raise ValueError('Unknown adaptive_pricing_mode')
    if not isinstance(count_dual_repair, bool):
        raise ValueError('count_dual_repair must be boolean')
    if not isinstance(capacity_dual_repair, bool):
        raise ValueError('capacity_dual_repair must be boolean')
    if not isinstance(route_cover_batch_pricing, bool):
        raise ValueError('route_cover_batch_pricing must be boolean')
    if not isinstance(retain_all_pricing_columns, bool):
        raise ValueError('retain_all_pricing_columns must be boolean')
    if not math.isfinite(primal_revisit_gap_limit) or not 0 <= primal_revisit_gap_limit < 1:
        raise ValueError('primal_revisit_gap_limit must lie in [0, 1)')
    if not isinstance(primal_mip_focus, int) or isinstance(primal_mip_focus, bool) or not 0 <= primal_mip_focus <= 3:
        raise ValueError('primal_mip_focus must be an integer from 0 to 3')
    begun = time.perf_counter()
    # Charge cleanup, exports and the final physical audit to the public cap.
    deadline = begun+time_limit-min(1.0, time_limit*.01)
    path = Path(diagnostics_dir) if diagnostics_dir is not None else None
    stages, lower, reference, conditional, adaptive = [], 0.0, None, None, None
    result = solve_routing(model=5, method='alns', problem=problem, seed=seed,
        alns_time=min(alns_time_limit, max(1e-12, deadline-time.perf_counter())), alns_iter=100000)
    audited = audit_solution(problem, result)
    if not audited['valid']:
        # No certified UB exists; keep explicit failure rather than manufacturing a gap.
        return dict(result=result, upper_bound=None, bound=0.0, gap=None, target_reached=False,
            status='NO_AUDITED_ALNS_INCUMBENT', audit=audited, stages=[], runtime=time.perf_counter()-begun,
            proven_optimal=False, proven_infeasible=False)
    initial = deepcopy(result)
    stages.append(dict(stage='alns', upper_bound=audited['objective'], runtime=time.perf_counter()-begun))
    backend = 'patterns' if len({o.satellite for o in problem.orders}) <= 5 and (
        problem.operating_policy.e1_turnaround_time == 0) else 'auto'
    primal_deadline = min(deadline, time.perf_counter()+primal_time_limit)
    # Two rebuilding passes receive separate shares of the same stage budget.
    for number in range(2):
        budget = max(0, primal_deadline-time.perf_counter())/(2-number)
        if budget <= 1e-6:
            break
        result = reoptimize_route_pool(problem, result, time_limit=budget, seed=seed,
            transfers=True, exchanges=True, recombinations=True, max_passes=1,
            e1_backend=backend, max_columns=route_pool_max_columns, verbose=verbose,
            extra_slots_per_vehicle=extra_slots_per_vehicle,
            mip_focus=primal_mip_focus,
            diagnostics_dir=path/f'primal_{number}' if path else None)
        audited = audit_solution(problem, result)
        if not audited['valid']:
            raise RuntimeError('Hybrid primal failed independent whole-cycle audit')
        stages.append(dict(stage='route_pool', pass_index=number, upper_bound=audited['objective'],
            runtime=result['info']['runtime'], pool_complete=False))
    cover_deadline = min(deadline, time.perf_counter()+cover_time_limit)
    remaining = max(0, cover_deadline-time.perf_counter())
    if remaining > 1e-6:
        reference = route_cover_certificate(problem, time_limit=remaining*(1-subset_time_fraction), initial_result=result,
            seed=seed, e1_time_limit=30, e1_backend=backend,
            batch_pricing=route_cover_batch_pricing, dual_repair=True,
            delivery_count_cuts=True, pricing_time_limit=8, verbose=verbose,
            reuse_pricing_budget=True,
            count_dual_repair=count_dual_repair,
            capacity_dual_repair=capacity_dual_repair,
            route_sessions=_route_sessions if adaptive_pricing_mode == 'resume' else None,
            diagnostics_dir=path/'reference' if path else None)
        reference.setdefault('components', [])
        reference.setdefault('e1_bound', 0.0)
        lower = reference['bound']
        subset_runs = []
        # Restricted LPs choose rows only. Each resulting bound is repriced over
        # the complete route domain; restricted master bounds never enter LB.
        candidates = []
        for row in reference.get('sites', []):
            if subset_time_fraction == 0 or max_subset_cuts_per_batch == 0 or time.perf_counter() >= cover_deadline:
                break
            context = route_context(problem, row['satellite'])
            cuts = select_capacity_subsets(problem, context, row['columns'],
                deadline=min(cover_deadline, time.perf_counter()+3), max_per_batch=max_subset_cuts_per_batch)
            if cuts:
                candidates.append((row, context, cuts))
        for number, (old, context, cuts) in enumerate(candidates):
            now = time.perf_counter()
            if now >= cover_deadline:
                break
            bag = dict(e2_trips=[dict(satellite_id=context['satellite'],
                events=[dict(order_id=o, kind=k) for o, k in c['tasks']]) for c in old['columns']])
            refined = site_route_cover(problem, context,
                deadline=min(cover_deadline, now+(cover_deadline-now)/(len(candidates)-number)),
                initial_result=bag, seed=seed, batch_pricing=route_cover_batch_pricing,
                dual_repair=True, delivery_count_cuts=True,
                subset_cuts=cuts, pricing_time_limit=8, verbose=verbose,
                reuse_pricing_budget=True,
                count_dual_repair=count_dual_repair,
                capacity_dual_repair=capacity_dual_repair,
                pricing_export_path=path/f'subsets_{number}_pricing.mps' if path else None)
            subset_runs.append(dict(satellite=context['satellite'], cuts=cuts, certificate=refined,
                                    restricted_objective_used_as_bound=False))
            if refined['bound'] > old['bound']:
                reference['sites'][reference['sites'].index(old)] = refined
                component = next(c for c in reference['components'] if c['satellite'] == context['satellite'])
                component.update(route_cover_bound=refined['bound'], bound=max(component['bound'], refined['bound']))
        reference['subset_runs'] = subset_runs
        raw = reference['e1_bound']+math.fsum(c['bound'] for c in reference['components'])
        lower = max(lower, max(0, raw-1e-7*max(1, abs(raw))))
        reference.update(bound=lower, raw_bound=raw)
        stages.append(dict(stage='route_cover', bound=lower, upper_bound=audited['objective'],
                           subset_runs=len(subset_runs), runtime=cover_time_limit-max(0, cover_deadline-time.perf_counter())))
    if reference is not None and primal_revisit_time_limit > 0:
        from models.routing.model_5.exact.primal.hybrid_primal import collect_pricing_columns, near_target_primal_budget
        budget = near_target_primal_budget(audited['objective'], lower, target_gap=target_gap,
            gap_limit=primal_revisit_gap_limit, time_limit=primal_revisit_time_limit,
            remaining=deadline-time.perf_counter())
        if budget > .05:
            revisit_begun = time.perf_counter()
            before = audited['objective']
            result = reoptimize_route_pool(problem, result, time_limit=budget, seed=seed,
                transfers=True, exchanges=True, recombinations=True, max_passes=1,
                additional_columns=collect_pricing_columns(problem, reference),
                e1_backend=backend, max_columns=route_pool_max_columns, verbose=verbose,
                extra_slots_per_vehicle=extra_slots_per_vehicle,
                mip_focus=primal_mip_focus,
                diagnostics_dir=path/'primal_revisit' if path else None)
            audited = audit_solution(problem, result)
            if not audited['valid'] or audited['objective'] > before+1e-6:
                raise RuntimeError('Near-target primal failed independent improvement audit')
            stages.append(dict(stage='route_pool_revisit', upper_bound=audited['objective'],
                previous_upper_bound=before, bound=lower, budget=budget,
                runtime=time.perf_counter()-revisit_begun, pool_complete=False))
    if reference is not None and {c['satellite'] for c in reference['components']} == set(problem.satellites) and (
            conditional_time_limit > 0 and time.perf_counter() < deadline and (
                primal_revisit_time_limit == 0 or
                max(0, audited['objective']-lower)/max(1e-9, abs(audited['objective'])) > target_gap)):
        conditional = refine_conditional_bound(problem, reference, result,
            deadline=min(deadline, time.perf_counter()+conditional_time_limit), seed=seed,
            max_conditions=max_conditions, target_gap=target_gap, verbose=verbose,
            max_supply_conjunctions=max_supply_conjunctions,
            reuse_pricing_budget=True,
            diagnostics_dir=path/'conditional' if path else None)
        lower = max(lower, conditional['bound'])
        stages.append(dict(stage='conditional_inventory', bound=lower, runtime=conditional['runtime']))
    if adaptive_finish and reference is not None and time.perf_counter() < deadline and (
            max(0, audited['objective']-lower)/max(1e-9, abs(audited['objective'])) > target_gap):
        adaptive = finish_hybrid(problem, reference, conditional, result, deadline=deadline,
            fallback_bound=lower, seed=seed, target_gap=target_gap, verbose=verbose,
            extra_slots_per_vehicle=extra_slots_per_vehicle,
            route_pool_max_columns=route_pool_max_columns,
            adaptive_pricing_mode=adaptive_pricing_mode,
            count_dual_repair=count_dual_repair,
            capacity_dual_repair=capacity_dual_repair,
            route_cover_batch_pricing=route_cover_batch_pricing,
            retain_all_pricing_columns=retain_all_pricing_columns,
            primal_mip_focus=primal_mip_focus,
            route_sessions=_route_sessions,
            diagnostics_dir=path/'adaptive' if path else None)
        result, lower = adaptive['result'], adaptive['bound']
        audited = audit_solution(problem, result)
        stages.append(dict(stage='adaptive_finish', bound=lower, upper_bound=audited['objective'], runtime=adaptive['runtime']))
    upper = audited['objective']
    if lower > upper+1e-5:
        raise RuntimeError('Certified lower bound exceeds independently audited upper bound')
    gap = max(0, upper-lower)/max(1e-9, abs(upper))
    result['info'].update(objective=upper, best_bound=lower, mip_gap=gap,
        proven_optimal=False, proven_infeasible=False, proof_scope='M5_hybrid_full_domain_certificates',
        runtime=time.perf_counter()-begun, target_reached=gap <= target_gap)
    return dict(result=result, initial_result=initial, upper_bound=upper, bound=lower, gap=gap,
        target_reached=gap <= target_gap, status='TARGET_REACHED' if gap <= target_gap else 'FEASIBLE_TARGET_NOT_REACHED',
        audit=audited, stages=stages, reference_certificate=reference, conditional_certificate=conditional,
        adaptive_certificate=adaptive,
        runtime=time.perf_counter()-begun, proven_optimal=False, proven_infeasible=False)


def finish_hybrid(problem, reference, conditional, initial_result, *, deadline,
                  fallback_bound, seed=42, target_gap=.05, verbose=False, diagnostics_dir=None,
                  extra_slots_per_vehicle=0, route_pool_max_columns=256,
                  adaptive_pricing_mode='column_generation', route_sessions=None, count_dual_repair=False,
                  capacity_dual_repair=False, route_cover_batch_pricing=True,
                  retain_all_pricing_columns=False, primal_mip_focus=0):
    """Spend unused original budget on unfinished pricing, then audited primal.

    This internal helper consumes certificates produced in the same run. Copies
    preserve every earlier stage's provenance and exported model association.
    """
    from models.routing.model_5.exact.bounds.route_cover_cg.route_cg import site_route_cover
    from models.routing.model_5.exact.bounds.route_cg.pickup_pricing import route_context
    from models.routing.model_5.exact.bounds.inventory.conditional_inventory import solve_conditional_inventory
    from models.routing.model_5.exact.primal.route_pool import reoptimize_route_pool
    from models.routing.model_5.exact.bounds.route_cover_cg.pricing_polish import refine_pricing_witness, select_unfinished_witness
    if adaptive_pricing_mode not in ('column_generation', 'fixed_duals', 'resume'):
        raise ValueError('Unknown adaptive_pricing_mode')
    begun = time.perf_counter()
    ref, result = deepcopy(reference), deepcopy(initial_result)
    path = Path(diagnostics_dir) if diagnostics_dir is not None else None
    lower, leaves, masters = fallback_bound, [], []
    audited = audit_solution(problem, result)
    if not audited['valid']:
        raise ValueError('Adaptive finish requires an audited full M5 result')
    def reached():
        return max(0, audited['objective']-lower)/max(1e-9, abs(audited['objective'])) <= target_gap
    def deficit(row):
        # A restricted objective diagnoses unfinished certification only;
        # it never contributes to the global lower bound.
        objectives = [it['master_objective'] for it in row.get('iterations', [])
                      if it.get('phase') == 'distance' and it.get('master_objective') is not None]
        return max(0.0, objectives[-1]-row['bound']) if objectives else 0.0
    pending = sorted((r for r in ref.get('sites', []) if not r['lp_converged']),
                     key=lambda r: (-deficit(r), r['satellite']))
    improved_any = False
    for number, old in enumerate(pending):
        now = time.perf_counter()
        if reached() or deadline-now < .05:
            break
        site = old['satellite']
        cuts = next((r['cuts'] for r in ref.get('subset_runs', []) if r['satellite'] == site), [])
        # A weaker certificate may still contain useful new columns. Earlier
        # subset runs are retained even when their bound did not improve.
        proposals = list(old['columns'])
        proposals += [c for run in ref.get('subset_runs', []) if run['satellite'] == site
                      for c in run['certificate']['columns']]
        unique = {tuple(tuple(k) for k in c['tasks']): c for c in proposals}
        bag = dict(e2_trips=[dict(satellite_id=site, events=[dict(order_id=o, kind=k)
                        for o, k in tasks]) for tasks in unique])
        reserve = min(15, max(0, deadline-now)*.2)
        # Equal remaining shares prevent one difficult site from consuming
        # almost all the budget. Early convergence releases its unused share.
        budget = max(0, deadline-now-reserve)/(len(pending)-number)
        index = select_unfinished_witness(old) if adaptive_pricing_mode == 'fixed_duals' else None
        session = (route_sessions or {}).get(site)
        if adaptive_pricing_mode == 'resume' and session is not None and session.active:
            if session.problem is not problem:
                raise ValueError('Cannot resume a route cover session from another problem')
            leaf = session.run(deadline=min(deadline, now+budget),
                pricing_export_path=path/f'site_{number}_pricing.mps' if path else None)
            cuts = session.options.get('subset_cuts') or []
        elif (adaptive_pricing_mode == 'fixed_duals' and index is not None
                and old['iterations'][index].get('phase') == 'distance'):
            leaf = refine_pricing_witness(problem, route_context(problem, site), old,
                deadline=min(deadline, now+budget), seed=seed, verbose=verbose, witness_index=index,
                pricing_export_path=path/f'site_{number}_pricing.mps' if path else None)
            cuts = leaf['subset_cuts']
        else:
            leaf = site_route_cover(problem, route_context(problem, site), deadline=min(deadline, now+budget),
                initial_result=bag, seed=seed, pricing_time_limit=8,
                batch_pricing=route_cover_batch_pricing, dual_repair=True,
                delivery_count_cuts=True, subset_cuts=cuts, verbose=verbose,
                reuse_pricing_budget=True,
                count_dual_repair=count_dual_repair,
                capacity_dual_repair=capacity_dual_repair,
                pricing_export_path=path/f'site_{number}_pricing.mps' if path else None)
        leaves.append(dict(satellite=site, cuts=cuts, certificate=leaf))
        improved = leaf['bound'] > old['bound']+1e-6
        improved_any |= improved
        if improved:
            ref['sites'][ref['sites'].index(old)] = leaf
            component = next(c for c in ref['components'] if c['satellite'] == site)
            component.update(route_cover_bound=leaf['bound'], bound=max(component['bound'], leaf['bound']))
        raw = ref['e1_bound']+math.fsum(c['bound'] for c in ref['components'])
        ref.update(raw_bound=raw, bound=max(ref['bound'], max(0, raw-1e-7*max(1, abs(raw)))))
        lower = max(lower, ref['bound'])
    if (improved_any and len({o.satellite for o in problem.orders}) <= 5 and time.perf_counter() < deadline):
        floors = {c['satellite']: c['bound'] for c in ref['components']}
        rows = [{k: v for k, v in c.items() if k != 'certificate'}
                for c in (conditional or {}).get('conditions', [])]
        master = solve_conditional_inventory(problem, floors, rows,
            deadline=min(deadline, time.perf_counter()+30), initial_result=result,
            e1_bound=ref['e1_bound'], fallback_bound=lower, seed=seed, verbose=verbose,
            diagnostics_path=path/'master_0.mps.gz' if path else None)
        masters.append(dict(certificate=master, site_bounds=floors))
        lower = max(lower, master['bound'])
    if not reached() and deadline-time.perf_counter() > .05:
        backend = 'patterns' if len({o.satellite for o in problem.orders}) <= 5 and (
            problem.operating_policy.e1_turnaround_time == 0) else 'auto'
        if retain_all_pricing_columns:
            from models.routing.model_5.exact.primal.hybrid_primal import collect_pricing_columns
            extra = collect_pricing_columns(problem, ref, conditional, leaves)
        else:
            proposal_rows = ref['sites']+[r['certificate'] for r in leaves]
            extra = {site: [c for row in proposal_rows if row['satellite'] == site for c in row['columns']]
                     for site in problem.satellites}
        result = reoptimize_route_pool(problem, result, time_limit=max(1e-12, deadline-time.perf_counter()),
            seed=seed, exchanges=True, recombinations=True, max_columns=route_pool_max_columns, max_passes=2,
            additional_columns=extra,
            extra_slots_per_vehicle=extra_slots_per_vehicle,
            e1_backend=backend, mip_focus=primal_mip_focus, verbose=verbose,
            diagnostics_dir=path/'primal' if path else None)
        audited = audit_solution(problem, result)
        if not audited['valid']:
            raise RuntimeError('Adaptive primal failed independent whole-cycle audit')
    return dict(bound=lower, result=result, reference_certificate=ref, sites=leaves, masters=masters,
                runtime=time.perf_counter()-begun, target_reached=reached())
