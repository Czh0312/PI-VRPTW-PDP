"""One complete physical arc model with optional productive batch positions.

For a common retained-K clock, any original plan extends by zero-duration
empty suffix batches. Conversely the nonempty prefix projects to the
original fixed-K formulation. Original time/capacity/vehicle/stock constraints
are reused, not replaced by the optimistic trip library.
"""
from copy import copy
from functools import lru_cache
import math


def metric_closure(problem):
    nodes = list(problem.network.nodes)
    distance = {(a,b):problem.distance(a,b) for a in nodes for b in nodes}
    for k in nodes:
        for a in nodes:
            for b in nodes:
                distance[a,b] = min(distance[a,b], distance[a,k]+distance[k,b])
    return distance


def transport_certificate(problem, upper):
    """Cheap input-only distance lower bounds, then audited-U dominance.

    Concatenating E2 trips at their satellite and shortcutting in the shortest
    distance closure gives a Hamiltonian tour no longer than their total.
    Every productive E1 batch has a nonempty satellite tour. All omitted
    objective terms must be nonnegative. No K monotonicity is assumed.
    """
    from models.routing.model_8.exact.parameters import necessary_time_bounds
    rates = problem.cost_profile.rates
    if any(value < 0 for value in rates.values() if isinstance(value,(int,float))):
        raise ValueError('Distance relaxation requires nonnegative objective rates')
    d = metric_closure(problem)
    site_tours = {}
    for site in problem.satellites:
        customers = sorted({o.customer for o in problem.orders if o.satellite == site})
        n = len(customers)
        if not n:
            continue
        if n > 16:
            raise ValueError('Subset TSP certificate currently supports at most sixteen local locations')

        @lru_cache(None)
        def length(mask,last):
            bit = 1 << last
            if mask == bit:
                return d[site,customers[last]]
            prior = mask ^ bit
            return min(length(prior,j)+d[customers[j],customers[last]]
                       for j in range(n) if prior & (1 << j))
        site_tours[site] = min(length((1 << n)-1,j)+d[customers[j],site] for j in range(n))
    e1 = min(d[problem.depot,s]+d[s,problem.depot] for s in problem.satellites)
    scale = rates['distance_scale_to_km']
    e1_cost = e1*scale*rates['e1_distance_cost_per_km']
    e2_cost = sum(site_tours.values())*scale*rates['e2_distance_cost_per_km']
    physical = necessary_time_bounds(problem)
    pool_lower = max(problem.pool_min, physical['pool_lower_bound'], max(o.count for o in problem.orders))
    constant = sum(problem.cost_profile.constant_cost(problem.total).values())
    bounds, pool_upper = {}, {}
    for k in problem.batch_count_candidates:
        minimum = max(pool_lower, (problem.total+k-1)//k)
        bounds[k] = minimum*rates['container_pool_cost_per_period']+constant+e2_cost+k*e1_cost
        if rates['container_pool_cost_per_period'] > 0:
            pool_upper[k] = min(problem.pool_max, math.floor(
                (upper-constant-e2_cost-k*e1_cost+1e-7)/rates['container_pool_cost_per_period']))
        else:
            pool_upper[k] = problem.pool_max
    retained = [k for k,value in bounds.items() if value <= upper+1e-6]
    return dict(original_K=list(problem.batch_count_candidates), retained_K=retained,
        per_K_lower_bound=bounds, per_K_pool_upper=pool_upper, pool_lower=pool_lower,
        e1_minimum_cost_per_batch=e1_cost, e2_tsp_cost_lower_bound=e2_cost,
        site_TSP_lengths=site_tours, constant_cost=constant, audited_upper=upper,
        excluded={k:dict(reason='COST_DOMINANCE', lower_bound=value, upper_bound=upper)
                  for k,value in bounds.items() if k not in retained},
        proof='All original solutions cost at least the independent pool, E1 productive-tour and E2 metric-tour bounds. An original optimum costs at most audited U.')


def build_prefix(problem, retained_counts=None, *, certificate=None, deadline=None, verbose=False,
                 vehicle_symmetry=False):
    import gurobipy as gp
    from models.routing.model_8.exact.formulation import build_model
    counts = tuple(retained_counts or problem.batch_count_candidates)
    if not counts or not set(counts) <= set(problem.batch_count_candidates):
        raise ValueError('Expected a nonempty subset of the original K domain')
    # A retained count subset must be certified by the caller. This builder
    # also supports full-domain equivalence tests without any cost cap.
    if set(counts) != set(problem.batch_count_candidates) and certificate is None:
        raise ValueError('Restricting K requires an audited cost-dominance certificate')
    clocks = [problem.for_count(k).operating_policy for k in counts]
    parent_clock = problem.operating_policy
    if any((p.time_tick_seconds,p.time_tick_subdivision) !=
           (parent_clock.time_tick_seconds,parent_clock.time_tick_subdivision) for p in clocks):
        raise ValueError('Optional-prefix prototype requires a common original clock for retained K')
    child = copy(problem.for_count(max(counts)))
    child.optional_batch_prefix = True
    child.prefix_original_counts = tuple(problem.batch_count_candidates)
    child.prefix_retained_counts = counts
    policy = child.operating_policy
    batches = policy.batches
    child.start_bounds = {b.id: ((policy.cycle_start,policy.cycle_start) if i == 0
                                else (policy.cycle_start,policy.cycle_end)) for i,b in enumerate(batches)}
    child.end_bounds = {b.id: ((policy.cycle_end,policy.cycle_end) if i == len(batches)-1
                              else (policy.cycle_start,policy.cycle_end)) for i,b in enumerate(batches)}
    child.cutoff_bounds = {b.id:(policy.cycle_start,policy.cycle_end) for b in batches}
    child.legal_batches = {o.id:tuple(b.id for b in batches) for o in child.orders}
    m,ctx = build_model(child, compact_e2=True, strengthening='physical_time',
                        vehicle_symmetry=vehicle_symmetry, deadline=deadline, verbose=verbose)
    selectors = ctx['variables'].calendar['prefix_count_selectors']
    for k in counts:
        m.addConstr(ctx['pool'] >= ((problem.total+k-1)//k)*selectors[k])
    if certificate is not None:
        m.addConstr(m.getObjective() <= certificate['audited_upper']+1e-7, name='audited_cost_cap')
        m.addConstr(gp.quicksum(r['distance'] for r in ctx['e2'])*problem.cost_profile.rates['distance_scale_to_km']
            *problem.cost_profile.rates['e2_distance_cost_per_km'] >= certificate['e2_tsp_cost_lower_bound']-1e-7)
        for k in counts:
            upper = certificate['per_K_pool_upper'].get(k,certificate['per_K_pool_upper'].get(str(k)))
            m.addConstr((selectors[k] == 1) >> (ctx['pool'] <= upper))
            lower = certificate['per_K_lower_bound'].get(k,certificate['per_K_lower_bound'].get(str(k)))
            m.addConstr((selectors[k] == 1) >> (m.getObjective() >= lower-1e-7))
    m.update()
    ctx.update(prefix_child=child, prefix_parent=problem, prefix_selectors=selectors)
    return m,ctx


def extract_prefix(problem, model, ctx, info):
    from models.routing.model_8.exact.solution import extract_solution
    from models.common.cycle.calendar import attach_calendar_decisions
    from models.common.cycle.audit import audit_solution
    from models.common.solver_config import accept_milp_audit
    k = next(k for k,v in ctx['prefix_selectors'].items() if v.X > .5)
    child = problem.for_count(k)
    bids = {b.id for b in child.operating_policy.batches}
    sub = dict(ctx)
    sub['e1'] = [r for r in ctx['e1'] if r['batch'].id in bids]
    for key in ('assignments','cutoffs','batch_starts','batch_ends'):
        sub[key] = {key_:value for key_,value in ctx[key].items()
                    if (key_[1] if isinstance(key_,tuple) else key_) in bids}
    result = extract_solution(child, model, sub, dict(info))
    boundaries = [b['start'] for b in result['batches']]+[child.operating_policy.cycle_end]
    attach_calendar_decisions(problem,result,k,boundaries)
    result['audit'] = audit_solution(problem,result)
    accept_milp_audit(result,float(model.ObjVal))
    if not result['audit']['valid'] or not result['audit']['periodic_replay']['valid']:
        raise RuntimeError('Prefix incumbent failed original full-domain audit')
    return result


def supply_start(problem, model, ctx, result):
    """Partial MIP start from an audited original plan; never fix a decision."""
    from models.common.cycle.audit import audit_solution
    audit = audit_solution(problem,result)
    if not audit['valid'] or not audit['periodic_replay']['valid']:
        raise ValueError('An audited original-domain plan is required')
    k = result['cycle_batch_count']
    policy = problem.operating_policy
    for count,selector in ctx['prefix_selectors'].items():
        selector.Start = int(count == k)
    for (oid,bid),variable in ctx['assignments'].items():
        variable.Start = int(result['order_batch_assignment'][oid] == bid)
    ctx['pool'].Start = result['container_pool_size']
    active = ctx['variables'].calendar['prefix_active']
    for i,b in enumerate(ctx['prefix_child'].operating_policy.batches):
        active[b.id].Start = int(i < k)
        ctx['batch_starts'][b.id].Start = result['batch_boundaries'][i] if i < k else policy.cycle_end
        ctx['cutoffs'][b.id].Start = result['batch_cutoffs'][b.id] if i < k else policy.cycle_end
    supplied = 0
    for layer in ('e1','e2'):
        if layer == 'e1':
            saved = {(r['batch_id'],r['vehicle_id']):r for r in result['e1_trips']}
        else:
            saved = {(r['satellite_id'],r['vehicle_id'],r['trip_index']):r for r in result['e2_trips']}
        for route in ctx[layer]:
            key = (route['batch'].id,route['vehicle']) if layer == 'e1' else (route['satellite'],route['vehicle'],route['slot'])
            plan = saved.get(key)
            route['used'].Start = int(plan is not None)
            route['start'].Start = plan['departure'] if plan else 0
            route['end'].Start = plan['return_complete'] if plan else 0
            for v in route['visit'].values():
                v.Start = 0
            for v in route['x'].values():
                v.Start = 0
            if plan is None:
                continue
            sequence = []
            for event in plan['events']:
                identity = event['site_id'] if layer == 'e1' else event['order_id']
                i = next(i for i,node in enumerate(route['nodes']) if node['kind'] == event['kind']
                         and (node['site'] if layer == 'e1' else node['order'].id) == identity)
                route['visit'][i].Start = 1
                route['times'][i].Start = event['start']
                quantity = route['nodes'][i]['quantity']
                if layer == 'e1':
                    quantity.Start = event['quantity']
                sequence.append(i)
            for a,b in zip([-1]+sequence,sequence+[-2]):
                if (a,b) not in route['x']:
                    raise RuntimeError('Audited start uses an arc missing from the complete model')
                route['x'][a,b].Start = 1
            supplied += 1
    return dict(original_audit_valid=True, original_cost=audit['objective'], supplied_physical_trips=supplied,
                fixes_decisions=False, mip_start=True)
