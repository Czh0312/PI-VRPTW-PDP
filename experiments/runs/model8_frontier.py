"""Certified discrete K/N stock bounds and cost-preserving vehicle symmetry."""
from copy import deepcopy
import math
import time

from experiments.runs.model8_stock_relaxation import build_stock_bound,phase_patterns


def stock_frontier(problem,certificate,*,deadline,per_pair_seconds=3.,on_progress=None):
    """Solve only optimistic stock subproblems; time limits yield bounds, not exclusions.

    A pair is removed only by a native infeasibility proof or a finite cost
    lower bound above audited U. Every undecided pair retains the independent
    transport/pool bound. The table therefore covers all competitive plans.
    """
    import gurobipy as gp
    begun = time.perf_counter()
    m,ctx = build_stock_bound(problem,certificate)
    original_pool_lb,original_pool_ub = ctx['pool'].LB,ctx['pool'].UB
    rates = problem.cost_profile.rates
    pairs = []
    try:
        for k in certificate['retained_K']:
            nmax = certificate['per_K_pool_upper'].get(k,certificate['per_K_pool_upper'].get(str(k)))
            nmin = max(certificate['pool_lower'],(problem.total+k-1)//k)
            for count in range(nmin,nmax+1):
                base = (rates['container_pool_cost_per_period']*count+certificate['constant_cost']
                        +certificate['e2_tsp_cost_lower_bound']+k*certificate['e1_minimum_cost_per_batch'])
                row = dict(K=k,N=count,lower_bound=base,native_status=None,proven_infeasible=False,
                           cost_dominated=False,seconds=0.)
                if time.perf_counter() < deadline:
                    for candidate,variable in ctx['selectors'].items():
                        variable.LB = variable.UB = int(candidate == k)
                    ctx['pool'].LB = ctx['pool'].UB = count
                    m.Params.TimeLimit = max(.01,min(per_pair_seconds,deadline-time.perf_counter()))
                    m.optimize()
                    row.update(native_status=int(m.Status),seconds=m.Runtime,
                               proven_infeasible=m.Status == gp.GRB.INFEASIBLE)
                    bound = m.ObjBound
                    if math.isfinite(bound) and abs(bound)<1e90:
                        row['lower_bound'] = max(base,bound)
                    row['cost_dominated'] = row['lower_bound'] > certificate['audited_upper']+1e-6
                pairs.append(row)
                if on_progress:
                    on_progress(dict(stage='stock_frontier',K=k,N=count,seconds=time.perf_counter()-begun,
                        lower_bound=row['lower_bound'],excluded=row['proven_infeasible'] or row['cost_dominated']))
    finally:
        m.dispose()
    retained = [row for row in pairs if not row['proven_infeasible'] and not row['cost_dominated']]
    if not retained:
        raise RuntimeError('Stock frontier contradicts the audited original feasible witness')
    return dict(pairs=pairs,retained_pairs=retained,seconds=time.perf_counter()-begun,
        original_K=certificate['original_K'],initial_K=certificate['retained_K'],
        retained_K=sorted({row['K'] for row in retained}),
        global_lower_bound=min(row['lower_bound'] for row in retained),
        seed=42,proof='Every original plan with cost <= audited U maps to its K/N stock subproblem. Only proven infeasibility or a valid lower bound above U removes a pair.')


def normalize_vehicle_ids(problem,result):
    """Rename complete homogeneous schedules; do not change task order or time."""
    from models.common.cycle.audit import audit_solution
    normalized = deepcopy(result)
    for site in problem.satellites:
        local = [o.id for o in problem.orders if o.satellite == site]
        groups = {}
        for trip in normalized['e2_trips']:
            if trip['satellite_id'] == site:
                groups.setdefault(trip['vehicle_id'],[]).append(trip)
        def first_key(pair):
            event = min(pair[1],key=lambda row:row['trip_index'])['events'][0]
            return 2*local.index(event['order_id'])+(0 if event['kind']=='d' else 1)+1
        for vehicle,(_,trips) in enumerate(sorted(groups.items(),key=first_key)):
            for trip in trips:
                trip['vehicle_id'] = vehicle
    rates = problem.cost_profile.rates
    if problem.operating_policy.e1_turnaround_time == 0 and rates['e1_vehicle_cost_per_cycle'] == 0:
        sites = list(problem.satellites)
        groups = {}
        for trip in normalized['e1_trips']:
            groups.setdefault(trip['batch_id'],[]).append(trip)
        for trips in groups.values():
            trips.sort(key=lambda row:2*sites.index(row['events'][0]['site_id'])+(0 if row['events'][0]['kind']=='d' else 1)+1)
            for vehicle,trip in enumerate(trips):
                trip['vehicle_id'] = vehicle
    audit = audit_solution(problem,normalized)
    if not audit['valid'] or not audit['periodic_replay']['valid']:
        raise RuntimeError('Vehicle relabeling failed original-domain audit')
    return normalized


def add_frontier_cuts(problem,model,ctx,frontier,certificate):
    """Install the proven K/N frontier and convex E1 visit-set distance bounds."""
    import gurobipy as gp
    pairs = frontier['retained_pairs']
    choose = model.addVars(len(pairs),vtype=gp.GRB.BINARY,name='certified_KN_pair')
    model.addConstr(choose.sum() == 1)
    selectors = ctx['prefix_selectors']
    for k,variable in selectors.items():
        model.addConstr(variable == gp.quicksum(choose[i] for i,row in enumerate(pairs) if row['K'] == k))
    model.addConstr(ctx['pool'] == gp.quicksum(row['N']*choose[i] for i,row in enumerate(pairs)))
    model.addConstr(model.getObjective() >= gp.quicksum((row['lower_bound']-1e-7)*choose[i] for i,row in enumerate(pairs)))
    patterns = phase_patterns(problem)
    active = ctx['variables'].calendar['prefix_active']
    phase_variables = 0
    e1_symmetry_rows = 0
    for batch in ctx['prefix_child'].operating_policy.batches:
        routes = [route for route in ctx['e1'] if route['batch'].id == batch.id]
        alpha = model.addVars(len(patterns),lb=0,ub=1,name=f'phase_bound_{batch.id}')
        phase_variables += len(patterns)
        model.addConstr(alpha.sum() == active[batch.id])
        for j,site in enumerate(problem.satellites):
            for kind,key in [('d','delivery'),('p','pickup')]:
                actual = gp.quicksum(route['visit'][i] for route in routes
                                    for i,node in enumerate(route['nodes']) if node['kind']==kind and node['site']==site)
                model.addConstr(actual == gp.quicksum(alpha[h] for h,row in enumerate(patterns) if row[key] & (1 << j)))
        model.addConstr(gp.quicksum(route['distance'] for route in routes)
                        >= gp.quicksum(row['distance']*alpha[h] for h,row in enumerate(patterns)))
        if problem.operating_policy.e1_turnaround_time == 0 and problem.cost_profile.rates['e1_vehicle_cost_per_cycle'] == 0:
            for left,right in zip(routes,routes[1:]):
                model.addConstr(right['used'] <= left['used'])
                left_key = gp.quicksum((i+1)*left['x'][-1,i] for i in range(len(left['nodes'])))
                right_key = gp.quicksum((i+1)*right['x'][-1,i] for i in range(len(right['nodes'])))
                model.addConstr(left_key+right['used'] <= right_key+len(right['nodes'])*(1-right['used']))
                e1_symmetry_rows += 2
    model.update()
    return dict(pair_variables=len(pairs),phase_variables=phase_variables,e1_symmetry_rows=e1_symmetry_rows,
        global_lower_bound=frontier['global_lower_bound'],original_K=certificate['original_K'],
        retained_K=frontier['retained_K'])
