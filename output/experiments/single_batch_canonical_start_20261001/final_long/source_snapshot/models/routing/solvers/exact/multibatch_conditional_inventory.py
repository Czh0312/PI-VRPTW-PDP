"""Certified E2 cost floors conditional on optimistic E1 supply patterns.

Each full M5 solution projects to a complete E1 pattern at no later supply
times. A pattern with supply >= t therefore safely activates an E2 lower
bound priced over all trips with that necessary release. Bounds for different
conditions on the same satellite are imposed by separate rows, never summed.
"""
from copy import deepcopy
import math
from pathlib import Path
import time

from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.precheck import travel_lower_bounds
from .multibatch_route_pricing import route_context, evaluate_column


def conditional_context(problem, site, batch, threshold, *, travel_bounds=None):
    if site not in problem.satellites or batch not in problem.batch_map:
        raise ValueError('Unknown conditional supply site or batch')
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 0:
        raise ValueError('Supply threshold must be a nonnegative integer')
    travel = travel_bounds or travel_lower_bounds(problem)
    context = route_context(problem, site, travel)
    release = dict(context['release'])
    release[batch] = max(release[batch], threshold+problem.operating_policy.e2_dispatch_prep_time)
    nodes = []
    for node in context['nodes']:
        order = node['order']
        delivery = max(order.delivery_window[0], release[order.batch]+travel['outward'][site][order.customer])
        earliest = delivery if node['key'][1] == 'd' else max(
            order.pickup_window[0], delivery+order.service_time+order.min_use_time)
        nodes.append({**node, 'window': (earliest, node['window'][1])})
    return {**context, 'release': release, 'nodes': nodes, 'lookup': {n['key']: n for n in nodes}}


def build_conditional_inventory(problem, site_bounds, conditions, *, deadline=None,
                                e1_bound=0.0, verbose=False):
    """Caller supplies independently certified floors; complete patterns only."""
    import gurobipy as gp
    from .multibatch_e1_routes import build_e1_routes
    if set(site_bounds) != set(problem.satellites) or any(
            not math.isfinite(v) or v < 0 for v in site_bounds.values()):
        raise ValueError('Finite certified floors are required for every satellite')
    for row in conditions:
        conditional_context(problem, row['site'], row['batch'], row['threshold'])
        if not math.isfinite(row['bound']) or row['bound'] < 0:
            raise ValueError('Conditional floors must be finite and nonnegative')
    model, context = build_e1_routes(problem, deadline=deadline, verbose=verbose)
    try:
        objective = model.getObjective()
        if e1_bound > 0:
            model.addConstr(objective >= e1_bound, name='certified_E1_cost')
        theta = model.addVars(problem.satellites, name='conditional_E2_cost')
        for site, value in site_bounds.items():
            model.addConstr(theta[site] >= value, name='unconditional_E2_'+site)
        rows = []
        for index, row in enumerate(conditions):
            if deadline is not None and time.perf_counter() >= deadline:
                raise TimeoutError('Conditional master build reached its budget')
            site, batch, threshold = row['site'], row['batch'], row['threshold']
            base = site_bounds[site]
            if row['bound'] <= base+1e-6:
                continue
            indices = [i for i, pattern in enumerate(context['patterns']) if pattern['batch'] == batch
                       and site in pattern['supply'] and pattern['supply'][site] >= threshold]
            late = gp.quicksum(context['chosen'][i] for i in indices)
            model.addConstr(theta[site] >= base+(row['bound']-base)*late,
                            name='conditional_floor_'+str(index))
            rows.append({**row, 'base_bound': base, 'late_indices': indices})
        model.setObjective(objective+theta.sum())
        model.update()
        return model, {**context, 'theta': theta, 'conditional_rows': rows}
    except Exception:
        model.dispose()
        raise


def set_conditional_projection(problem, context, initial, *, fix=False):
    from .multibatch_e1_routes import set_route_projection
    audited = audit_solution(problem, initial)
    if not audited['valid']:
        raise ValueError('Conditional projection requires an audited full M5 schedule')
    set_route_projection(problem, context, initial, fix=fix)
    costs = {s: 0.0 for s in problem.satellites}
    for trip in initial['e2_trips']:
        site = trip['satellite_id']
        path = [site]+[problem.order_map[e['order_id']].customer for e in trip['events']]+[site]
        costs[site] += math.fsum(problem.distance(a, b) for a, b in zip(path, path[1:]))
        for event in trip['events']:
            variable = context['order_times'][event['order_id'], event['kind']]
            variable.Start = event['start']
            if fix:
                variable.LB = variable.UB = event['start']
    for site, variable in context['theta'].items():
        variable.Start = costs[site]
        if fix:
            variable.LB = variable.UB = costs[site]


def solve_conditional_inventory(problem, site_bounds, conditions, *, deadline,
        initial_result=None, e1_bound=0.0, fallback_bound=0.0, seed=42,
        verbose=False, diagnostics_path=None):
    import gurobipy as gp
    begun, model = time.perf_counter(), None
    result = dict(bound=fallback_bound, raw_bound=None, margin=0.0, status='TIME_LIMIT_BUILD',
        scope='M5_E1_inventory_plus_conditional_E2_floors', relaxation_optimal=False,
        proven_optimal=False, proven_infeasible=False, conditional_rows=[], supplies=[])
    try:
        if time.perf_counter() >= deadline:
            raise TimeoutError
        model, context = build_conditional_inventory(problem, site_bounds, conditions,
            deadline=deadline, e1_bound=e1_bound, verbose=verbose)
        model.Params.Seed = int(seed)
        model.Params.IntFeasTol = model.Params.FeasibilityTol = 1e-9
        if initial_result is not None:
            set_conditional_projection(problem, context, initial_result)
        model.Params.TimeLimit = max(0, deadline-time.perf_counter())
        model.optimize()
        try:
            raw = float(model.ObjBound)
        except (gp.GurobiError, AttributeError):
            raw = math.nan
        if math.isfinite(raw) and model.Status != gp.GRB.INFEASIBLE:
            margin = 1e-7*max(1.0, abs(raw))
            result.update(bound=max(fallback_bound, max(0, raw-margin)), raw_bound=raw, margin=margin)
        result.update(status=int(model.Status), relaxation_optimal=model.Status == gp.GRB.OPTIMAL,
            conditional_rows=context['conditional_rows'], patterns=len(context['patterns']),
            variables=model.NumVars, constraints=model.NumConstrs)
        if model.SolCount:
            result['supplies'] = [dict(site=s, batch=b, supply=int(round(v.X)))
                                  for (s, b), v in context['supplies'].items()]
        if diagnostics_path is not None:
            path = Path(diagnostics_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            model.write(str(path))
    except TimeoutError:
        pass
    finally:
        if model is not None:
            model.dispose()
        result['runtime'] = time.perf_counter()-begun
    return result


def refine_conditional_bound(problem, reference, initial_result, *, deadline,
        seed=42, max_conditions=3, target_gap=.05, verbose=False, diagnostics_dir=None):
    """Price new conditional floors, combining them with a supplied certificate.

    This is an internal refinement of a locally computed reference certificate.
    No conditional cost/solution is ever used as a full M5 upper bound.
    """
    from .multibatch_route_cg import site_route_cover
    begun = time.perf_counter()
    audited = audit_solution(problem, initial_result)
    if not audited['valid']:
        raise ValueError('Conditional refinement requires an audited full M5 incumbent')
    path = Path(diagnostics_dir) if diagnostics_dir is not None else None
    sites = {c['satellite']: c['bound'] for c in reference['components']}
    # Pattern growth is factorial. Large-site cases retain the reference bound.
    if len([s for s in sites if any(o.satellite == s for o in problem.orders)]) > 5:
        return dict(bound=reference['bound'], status='SITE_LIMIT_SEPARATE_BOUND', masters=[], conditions=[], runtime=0.0)
    supplies = {(e['site_id'], t['batch_id']): e['start']+problem.service(e['site_id'])
                for t in initial_result['e1_trips'] for e in t['events'] if e['kind'] == 'd'}
    master_deadline = min(deadline, time.perf_counter()+30)
    master = solve_conditional_inventory(problem, sites, [], deadline=master_deadline,
        initial_result=initial_result, e1_bound=reference['e1_bound'], fallback_bound=reference['bound'],
        seed=seed, verbose=verbose, diagnostics_path=path/'master_0.mps.gz' if path else None)
    masters, conditions = [master], []
    jobs = sorted(master['supplies'], key=lambda row: -max(0,
        row['supply']-supplies.get((row['site'], row['batch']), row['supply']))*sites[row['site']])
    travel = travel_lower_bounds(problem)
    for job in jobs:
        if len(conditions) >= max_conditions or time.perf_counter() >= deadline:
            break
        if max(0, audited['objective']-master['bound'])/max(1e-9, abs(audited['objective'])) <= target_gap:
            break
        site, batch, threshold = job['site'], job['batch'], job['supply']
        base_context = route_context(problem, site, travel)
        if threshold <= base_context['release'][batch]-problem.operating_policy.e2_dispatch_prep_time:
            continue
        context = conditional_context(problem, site, batch, threshold, travel_bounds=travel)
        original_columns = next(r['columns'] for r in reference['sites'] if r['satellite'] == site)
        valid = [c for c in original_columns if evaluate_column(problem, context, c['tasks']) is not None]
        initial = dict(e2_trips=[dict(satellite_id=site, events=[dict(order_id=o, kind=k)
                       for o, k in c['tasks']]) for c in valid])
        number = len(conditions)+1
        # Leave a real share for rebuilding and solving the joint master.
        leaf_deadline = min(deadline, time.perf_counter()+max(0, deadline-time.perf_counter()-15)/2)
        leaf = site_route_cover(problem, context, deadline=leaf_deadline, initial_result=initial,
            seed=seed, pricing_time_limit=8, batch_pricing=True, dual_repair=True,
            delivery_count_cuts=True, verbose=verbose,
            pricing_export_path=path/f'condition_{number}_pricing.mps' if path else None)
        conditions.append(dict(site=site, batch=batch, threshold=threshold, bound=leaf['bound'], certificate=leaf))
        row = {k: v for k, v in conditions[-1].items() if k != 'certificate'}
        rows = [{k: v for k, v in c.items() if k != 'certificate'} for c in conditions]
        master = solve_conditional_inventory(problem, sites, rows, deadline=min(deadline, time.perf_counter()+30),
            initial_result=initial_result, e1_bound=reference['e1_bound'], fallback_bound=master['bound'],
            seed=seed, verbose=verbose, diagnostics_path=path/f'master_{number}.mps.gz' if path else None)
        masters.append(master)
    return dict(bound=master['bound'], status='CONDITIONAL_CERTIFIED_BOUND', masters=masters,
                conditions=conditions, runtime=time.perf_counter()-begun)
