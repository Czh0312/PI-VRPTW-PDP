"""Exact pattern refinement with the container pool included in each pattern.

Every competitive original solution projects into the full-K master, including
its integer pool N. A follower fixes (K, N, assignments, recovery batches, trip
masks), while retaining original route orders, vehicles and clocks. Its bound
or infeasibility cut is gated by ALL these choices, including N. No time limit
is interpreted as infeasibility, and no relaxed representative is a real route.
"""
import math
import time
from copy import copy
from dataclasses import replace

from experiments.runs.research_model5_shared_relaxation import build_relaxation
from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import attach_calendar_decisions
from models.common.solver_config import accept_milp_audit, read_milp_info
from models.routing.model_5.exact.formulation import build_model
from models.routing.model_5.exact.solution import extract_solution

TOL = 1e-6


def finite(value):
    return float(value) if math.isfinite(value) and abs(value) < 1e90 else None


def pool_interval(bounds):
    try:
        values = (bounds, bounds) if isinstance(bounds, int) and not isinstance(bounds, bool) else tuple(bounds)
    except TypeError as error:
        raise ValueError('Pool bounds must be positive inclusive integer endpoints') from error
    if (len(values) != 2 or any(isinstance(v, bool) or not isinstance(v, int) for v in values)
            or values[0] < 1 or values[1] < values[0]):
        raise ValueError('Pool bounds must be positive inclusive integer endpoints')
    return values


def add_pool_choices(model, pool, bounds):
    """An exact encoding of every integer pool in the certified interval."""
    import gurobipy as gp
    lo, hi = pool_interval(bounds)
    model.update()
    pool.LB, pool.UB = max(pool.LB, lo), min(pool.UB, hi)
    if pool.LB > pool.UB:
        raise ValueError('Certified pool interval is empty in the original domain')
    choices = model.addVars(range(lo, hi + 1), vtype=gp.GRB.BINARY, name='pattern_pool_value')
    model.addConstr(choices.sum() == 1, name='one_pattern_pool_value')
    model.addConstr(pool == gp.quicksum(n * var for n, var in choices.items()), name='pattern_pool_link')
    return choices


def pattern_signature(k, pool, assignments, recoveries, masks, order_ids):
    return (k, pool, tuple(assignments[oid] for oid in order_ids),
            tuple(recoveries[oid] for oid in order_ids), tuple(sorted(masks)))


def pattern_mismatch(selected, *, pool_choice):
    """A pool-specific pattern must never exclude the same routes at another N."""
    import gurobipy as gp
    return 1 - pool_choice + gp.quicksum(1 - var for var in selected)


def solve(p, witness, retained_K, pool_bounds, lower, library, phase_module, seconds,
          seed=42, on_event=None, *, trip_sync_augment, route_bound_augment):
    import gurobipy as gp
    begun = time.perf_counter()
    deadline = begun + seconds
    pool_lo, pool_hi = pool_interval(pool_bounds)
    audit = audit_solution(p, witness)
    if not audit['valid'] or not audit['periodic_replay']['valid']:
        raise ValueError('An original-domain audited feasible plan is required')
    if not pool_lo <= witness['container_pool_size'] <= pool_hi:
        raise ValueError('Certified pool interval excludes its audited plan')
    upper, best = audit['objective'], witness
    if lower > upper + TOL:
        raise ValueError('The fresh global lower bound exceeds the audited cost')
    initial_upper = upper
    master, ctx = build_relaxation(p, library)
    cache, events = {}, []
    cuts = 0
    follower_seconds = master_seconds = work = 0.
    best_bound, stop = lower, 'TIME_LIMIT'
    order_ids = [o.id for o in p.orders]
    try:
        phase_module.augment(p, master, ctx)
        trip_sync_augment(p, master, ctx, library)
        optimistic = master.getObjective()
        eta = master.addVar(lb=lower, ub=upper + 1e-7, name='complete_cost_proxy')
        master.addConstr(eta >= optimistic, name='shared_cost_lower_bound')
        master.setObjective(eta)
        choices = add_pool_choices(master, ctx['pool'], (pool_lo, pool_hi))
        for k, selector in ctx['selectors'].items():
            if k not in retained_K:
                selector.UB = 0
        master.Params.Seed, master.Params.Threads = seed, 1
        master.Params.OutputFlag = 0
        master.Params.MIPGap = master.Params.MIPGapAbs = 0
        try:
            while time.perf_counter() < deadline:
                master.Params.TimeLimit = min(5., deadline - time.perf_counter())
                master.optimize()
                master_seconds += master.Runtime
                work += master.Work
                if master.Status == gp.GRB.INFEASIBLE:
                    raise RuntimeError('The master must retain the audited plan projection')
                bound = finite(master.ObjBound)
                if bound is not None:
                    if bound > upper + TOL:
                        raise RuntimeError('Refined master bound exceeds its audited plan')
                    best_bound = max(best_bound, min(upper, bound))
                if upper - best_bound <= TOL:
                    stop = 'GLOBAL_VARIABLE_POOL_BOUND_CERTIFICATE'
                    break
                if not master.SolCount:
                    continue
                k = next(k for k, var in ctx['selectors'].items() if var.X > .5)
                pool = next(n for n, var in choices.items() if var.X > .5)
                assignments = {oid: next(b for b in range(k) if ctx['assignments'][i, b].X > .5)
                               for i, oid in enumerate(order_ids)}
                recoveries = {oid: next(b for b in range(k) if ctx['recovery'][i, b].X > .5)
                              for i, oid in enumerate(order_ids)}
                selected = [c for c, var in ctx['route'].items() if var.X > .5]
                masks = tuple(sorted(library[c]['mask'] for c in selected))
                signature = pattern_signature(k, pool, assignments, recoveries, masks, order_ids)
                if signature not in cache:
                    child = copy(p.for_count(k))
                    if (child.operating_policy.time_tick_seconds, child.operating_policy.time_tick_subdivision) != (
                            p.operating_policy.time_tick_seconds, p.operating_policy.time_tick_subdivision):
                        raise ValueError('Pattern refinement requires unchanged retained-K clocks')
                    trip_counts = {}
                    for c in selected:
                        site = library[c]['satellite']
                        trip_counts[site] = trip_counts.get(site, 0) + 1
                    original_cap = child.operating_policy.e2_max_trips_per_vehicle
                    derived_cap = min(original_cap, max(trip_counts.values()))
                    child.operating_policy = replace(child.operating_policy, e2_max_trips_per_vehicle=derived_cap)
                    model, sub = build_model(child, compact_e2=True, strengthening='physical_time', deadline=deadline)
                    # Put the new model under disposal immediately, including on later exceptions.
                    item = dict(model=model, context=sub, child=child, repeats=0, conditional_bound=lower,
                                pool=pool, original_slot_cap=original_cap, derived_slot_cap=derived_cap)
                    cache[signature] = item
                    model.Params.OutputFlag = 0
                    model.Params.Threads, model.Params.Seed = 1, seed
                    model.Params.MIPGap = model.Params.MIPGapAbs = 0
                    sub['pool'].LB = sub['pool'].UB = pool
                    batches = [b.id for b in child.operating_policy.batches]
                    for (oid, bid), var in sub['assignments'].items():
                        var.LB = var.UB = int(bid == batches[assignments[oid]])
                    for oid, b in assignments.items():
                        if (oid, batches[b]) not in sub['assignments']:
                            model.addConstr(gp.LinExpr() == 1)
                    model.update()
                    for oid, recovery in recoveries.items():
                        for b, bid in enumerate(batches):
                            var = model.getVarByName(f'eligible_{oid}_{bid}')
                            if var is None:
                                raise RuntimeError('Missing complete-model recovery eligibility variable')
                            var.LB = var.UB = int(b >= recovery)
                    route_bound_augment(child, model, sub, library)
                    for c, row in enumerate(library):
                        var = model.getVarByName(f'bound_shared_mask[{c}]')
                        var.LB = var.UB = int(row['mask'] in masks)
                    model.addConstr(model.getObjective() <= initial_upper + 1e-7, name='initial_audited_cost_cap')
                    model.addConstr(model.getObjective() >= lower - 1e-7, name='fresh_global_cost_lower_bound')
                    model.update()
                item = cache[signature]
                model, sub, child = item['model'], item['context'], item['child']
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    break
                item['repeats'] += 1
                model.Params.TimeLimit = min(5. * item['repeats'], 20., remaining)
                model.optimize()
                follower_seconds += model.Runtime
                work += model.Work
                if model.SolCount:
                    info = read_milp_info(model, proof_scope='complete_original_pool_specific_pattern', build_time=0)
                    result = extract_solution(child, model, sub, info)
                    boundaries = [b['start'] for b in result['batches']] + [child.operating_policy.cycle_end]
                    attach_calendar_decisions(p, result, k, boundaries)
                    result['audit'] = audit_solution(p, result)
                    accept_milp_audit(result, model.ObjVal)
                    if not result['audit']['valid'] or not result['audit']['periodic_replay']['valid']:
                        raise RuntimeError('Pattern solution failed original-domain audit')
                    if result['audit']['objective'] < upper:
                        upper, best = result['audit']['objective'], result
                        eta.UB = upper + 1e-7
                chosen = [ctx['selectors'][k]]
                chosen += [ctx['assignments'][i, assignments[oid]] for i, oid in enumerate(order_ids)]
                chosen += [ctx['recovery'][i, recoveries[oid]] for i, oid in enumerate(order_ids)]
                chosen += [ctx['route'][c] for c in selected]
                mismatch = pattern_mismatch(chosen, pool_choice=choices[pool])
                native_bound = finite(model.ObjBound)
                event = dict(iteration=len(events) + 1, K=k, N=pool, delivery_batches=assignments,
                    recovery_batches=recoveries, masks=list(masks), native_status=model.Status,
                    native_bound=native_bound, native_objective=model.ObjVal if model.SolCount else None,
                    native_runtime=model.Runtime, pattern_repeats=item['repeats'],
                    original_slot_cap=item['original_slot_cap'], derived_slot_cap=item['derived_slot_cap'],
                    pool_value_in_pattern=True, cut_kind='NO_CUT_TIME_LIMIT')
                if model.Status == gp.GRB.INFEASIBLE:
                    master.addConstr(mismatch >= 1, name=f'pool_specific_no_good_{cuts}')
                    event['cut_kind'] = 'PROVEN_NO_SOLUTION_UNDER_INITIAL_COST_CAP'
                    cuts += 1
                elif native_bound is not None:
                    gamma = max(item['conditional_bound'], min(initial_upper, native_bound))
                    item['conditional_bound'] = gamma
                    big_m = initial_upper - lower + 1e-5
                    master.addConstr(eta >= gamma - 1e-7 - big_m * mismatch,
                                     name=f'pool_specific_cost_bound_{cuts}')
                    event.update(cut_kind='VALID_CONDITIONAL_LOWER_BOUND', gamma=gamma, bigM=big_m)
                    cuts += 1
                elif model.Status not in (gp.GRB.TIME_LIMIT, gp.GRB.INTERRUPTED):
                    raise RuntimeError(f'Unsupported follower status {model.Status}')
                event.update(global_lower_bound=best_bound, audited_upper_bound=upper,
                             elapsed=time.perf_counter() - begun)
                events.append(event)
                if on_event is not None:
                    on_event(event)
                if upper - best_bound <= TOL:
                    stop = 'GLOBAL_VARIABLE_POOL_BOUND_CERTIFICATE'
                    break
        except TimeoutError:
            stop = 'TIME_LIMIT_INCLUSIVE_BUDGET'
        final_audit = audit_solution(p, best)
        assert final_audit['valid'] and final_audit['periodic_replay']['valid']
        assert best_bound <= upper + TOL
        return best, dict(status=stop, global_optimality_certified=upper - best_bound <= TOL,
            audited_upper_bound=upper, global_lower_bound=best_bound,
            gap_over_upper=max(0., upper - best_bound) / abs(upper),
            elapsed_seconds=time.perf_counter() - begun, master_seconds=master_seconds,
            follower_seconds=follower_seconds, mip_work=work, patterns=len(cache), iterations=len(events),
            cuts=cuts, events=events, original_K=list(p.batch_count_candidates), retained_K=list(retained_K),
            retained_N=[pool_lo, pool_hi], pool_value_in_pattern=True, seed=seed, deadline_seconds=seconds,
            proof='Every original competitive solution projects to a master pattern including N. '
                  'Pool-specific followers retain original route, vehicle and integer-clock choices. '
                  'Only proven infeasibility or finite native bounds permit cuts, gated by N as well '
                  'as K, all assignments, recoveries and masks. Time limits never imply infeasibility.')
    finally:
        master.dispose()
        for item in cache.values():
            item['model'].dispose()
