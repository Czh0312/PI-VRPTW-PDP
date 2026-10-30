"""Exact cost-cut refinement of a shared all-K lower-bound master.

A pattern contains K, fixed N, every delivery/recovery batch and E2 trip task
mask. Its follower retains ALL original vehicle, route-order and integer-clock
choices. Only a proven infeasible follower permits a no-good. Finite native
follower lower bounds permit conditional cost cuts, even on a time limit.
"""
import math
import time
from copy import copy
from dataclasses import replace

from shared_medium import build_relaxation
from models.common.cycle.audit import audit_solution
from models.common.cycle.calendar import attach_calendar_decisions
from models.routing.model_8.exact.formulation import build_model
from models.routing.model_8.exact.solution import extract_solution
from models.common.solver_config import read_milp_info,accept_milp_audit
from route_bounds_medium import add_shared_route_bounds
from master_sync import augment as add_trip_sync


def finite(value):
    return float(value) if math.isfinite(value) and abs(value)<1e90 else None


def solve(p,witness,retained_K,pool,lower,library,phase_module,seconds,seed=42,on_event=None):
    import gurobipy as gp

    begun = time.perf_counter()
    audit = audit_solution(p,witness)
    if not audit['valid'] or not audit['periodic_replay']['valid']:
        raise ValueError('An original-domain audited feasible witness is required')
    upper,best = audit['objective'],witness
    initial_upper = upper
    master,ctx = build_relaxation(p,library)
    cache = {}
    events = []
    try:
        phase_module.augment(p,master,ctx)
        add_trip_sync(p,master,ctx,library)
        optimistic = master.getObjective()
        eta = master.addVar(lb=lower,ub=upper+1e-7,name='complete_cost_proxy')
        master.addConstr(eta >= optimistic,name='shared_cost_lower_bound')
        master.setObjective(eta)
        ctx['pool'].LB = ctx['pool'].UB = pool
        for k,selector in ctx['selectors'].items():
            if k not in retained_K:
                selector.UB = 0
        master.Params.Seed = seed
        master.Params.Threads = 1
        master.Params.OutputFlag = 0
        master.Params.MIPGap = 0
        master.Params.MIPGapAbs = 0
        original_orders = list(p.orders)
        cuts = 0
        follower_seconds = 0.
        master_seconds = 0.
        work = 0.
        best_bound = lower
        stop = 'TIME_LIMIT'
        while time.perf_counter()-begun < seconds:
            remaining = seconds-(time.perf_counter()-begun)
            master.Params.TimeLimit = min(5.,remaining)
            master.optimize()
            master_seconds += master.Runtime
            work += master.Work
            bound = finite(master.ObjBound)
            if bound is not None:
                best_bound = max(best_bound,min(upper,bound))
            if upper-best_bound <= 1e-6:
                stop = 'GLOBAL_BOUND_CERTIFICATE'
                break
            if master.Status == gp.GRB.INFEASIBLE:
                raise RuntimeError('The master must still contain the audited witness projection')
            if not master.SolCount:
                continue
            k = next(k for k,v in ctx['selectors'].items() if v.X>.5)
            assignments = {o.id:next(b for b in range(k) if ctx['assignments'][i,b].X>.5)
                           for i,o in enumerate(original_orders)}
            recoveries = {o.id:next(b for b in range(k) if ctx['recovery'][i,b].X>.5)
                          for i,o in enumerate(original_orders)}
            selected = [c for c,v in ctx['route'].items() if v.X>.5]
            masks = tuple(sorted(library[c]['mask'] for c in selected))
            signature = (k,tuple(assignments[o.id] for o in original_orders),
                         tuple(recoveries[o.id] for o in original_orders),masks)
            if signature not in cache:
                child = copy(p.for_count(k))
                if child.operating_policy.time_tick_seconds != p.operating_policy.time_tick_seconds:
                    raise ValueError('Research decomposition currently requires unchanged retained-K clocks')
                # A fixed task partition has exactly this many nonempty trips
                # at each satellite. No vehicle there can use more than their
                # total. Remove only impossible unused suffix slots, keeping
                # the original parent policy and physical resources intact.
                trip_counts = {}
                for c in selected:
                    site = library[c]['satellite']
                    trip_counts[site] = trip_counts.get(site,0)+1
                original_cap = child.operating_policy.e2_max_trips_per_vehicle
                derived_cap = min(original_cap,max(trip_counts.values()))
                child.operating_policy = replace(child.operating_policy,e2_max_trips_per_vehicle=derived_cap)
                model,sub = build_model(child,compact_e2=True,strengthening='physical_time')
                model.Params.OutputFlag = 0
                model.Params.Threads = 1
                model.Params.Seed = seed
                model.Params.MIPGap = 0
                model.Params.MIPGapAbs = 0
                sub['pool'].LB = sub['pool'].UB = pool
                batches = [b.id for b in child.operating_policy.batches]
                for (oid,bid),variable in sub['assignments'].items():
                    variable.LB = variable.UB = int(bid==batches[assignments[oid]])
                for oid,b in assignments.items():
                    if (oid,batches[b]) not in sub['assignments']:
                        model.addConstr(gp.LinExpr()==1)
                model.update()
                for oid,recovery in recoveries.items():
                    for b,bid in enumerate(batches):
                        h = model.getVarByName(f'eligible_{oid}_{bid}')
                        if h is None:
                            raise RuntimeError('Missing complete-model recovery eligibility variable')
                        h.LB = h.UB = int(b>=recovery)
                add_shared_route_bounds(child,model,sub,library)
                for c,row in enumerate(library):
                    value = int(row['mask'] in masks)
                    variable = model.getVarByName(f'bound_shared_mask[{c}]')
                    variable.LB = variable.UB = value
                model.addConstr(model.getObjective() <= initial_upper+1e-7,name='initial_audited_cost_cap')
                model.addConstr(model.getObjective() >= lower-1e-7,name='prior_global_cost_lower_bound')
                model.update()
                cache[signature] = dict(model=model,context=sub,child=child,repeats=0,
                                         conditional_bound=lower,original_slot_cap=original_cap,
                                         derived_slot_cap=derived_cap)
            item = cache[signature]
            model,sub,child = item['model'],item['context'],item['child']
            remaining = seconds-(time.perf_counter()-begun)
            if remaining <= 0:
                break
            item['repeats'] += 1
            model.Params.TimeLimit = min(5.*item['repeats'],20.,remaining)
            model.optimize()
            follower_seconds += model.Runtime
            work += model.Work
            if model.SolCount:
                info = read_milp_info(model,proof_scope='complete_original_pattern_follower',build_time=0)
                result = extract_solution(child,model,sub,info)
                boundaries = [b['start'] for b in result['batches']]+[child.operating_policy.cycle_end]
                attach_calendar_decisions(p,result,k,boundaries)
                result['audit'] = audit_solution(p,result)
                accept_milp_audit(result,model.ObjVal)
                if not result['audit']['valid'] or not result['audit']['periodic_replay']['valid']:
                    raise RuntimeError('Follower solution failed original full-domain audit')
                if result['audit']['objective'] < upper:
                    upper,best = result['audit']['objective'],result
                    eta.UB = upper+1e-7
            chosen_variables = [ctx['selectors'][k]]
            chosen_variables += [ctx['assignments'][i,assignments[o.id]] for i,o in enumerate(original_orders)]
            chosen_variables += [ctx['recovery'][i,recoveries[o.id]] for i,o in enumerate(original_orders)]
            chosen_variables += [ctx['route'][c] for c in selected]
            mismatch = gp.quicksum(1-variable for variable in chosen_variables)
            native_bound = finite(model.ObjBound)
            event = dict(iteration=len(events)+1,K=k,delivery_batches=assignments,recovery_batches=recoveries,
                         masks=list(masks),native_status=model.Status,native_bound=native_bound,
                         native_objective=model.ObjVal if model.SolCount else None,
                         native_runtime=model.Runtime,pattern_repeats=item['repeats'],
                         original_slot_cap=item['original_slot_cap'],derived_slot_cap=item['derived_slot_cap'])
            if model.Status == gp.GRB.INFEASIBLE:
                master.addConstr(mismatch >= 1,name=f'proven_cost_cap_infeasible_pattern_{cuts}')
                event['cut_kind'] = 'PROVEN_NO_SOLUTION_UNDER_INITIAL_COST_CAP'
                cuts += 1
            elif native_bound is not None:
                gamma = max(item['conditional_bound'],min(initial_upper,native_bound))
                item['conditional_bound'] = gamma
                bigM = initial_upper-lower+1e-5
                master.addConstr(eta >= gamma-1e-7-bigM*mismatch,name=f'conditional_cost_bound_{cuts}')
                event.update(cut_kind='VALID_CONDITIONAL_LOWER_BOUND',gamma=gamma,bigM=bigM)
                cuts += 1
            elif model.Status not in (gp.GRB.TIME_LIMIT,gp.GRB.INTERRUPTED):
                raise RuntimeError(f'Unsupported follower status {model.Status}')
            event.update(global_lower_bound=best_bound,audited_upper_bound=upper,
                         elapsed=time.perf_counter()-begun)
            events.append(event)
            if on_event is not None:
                on_event(event)
        final_audit = audit_solution(p,best)
        assert final_audit['valid'] and final_audit['periodic_replay']['valid']
        assert best_bound<=upper+1e-6
        return best,dict(status=stop,global_optimality_certified=upper-best_bound<=1e-6,
             audited_upper_bound=upper,global_lower_bound=best_bound,gap_over_upper=max(0.,upper-best_bound)/abs(upper),
             elapsed_seconds=time.perf_counter()-begun,master_seconds=master_seconds,follower_seconds=follower_seconds,
             mip_work=work,patterns=len(cache),iterations=len(events),cuts=cuts,events=events,
             original_K=list(p.batch_count_candidates),retained_K=retained_K,retained_N=[pool,pool],
             seed=seed,deadline_seconds=seconds,proof='All original competitive solutions project to the '
             'master. Followers optimize every original order, vehicle and integer-clock choice within '
             'a discrete pattern. Infeasibility no-goods and native lower-bound cost cuts are valid. '
             'No time limit is treated as infeasibility.')
    finally:
        master.dispose()
        for item in cache.values():
            item['model'].dispose()
