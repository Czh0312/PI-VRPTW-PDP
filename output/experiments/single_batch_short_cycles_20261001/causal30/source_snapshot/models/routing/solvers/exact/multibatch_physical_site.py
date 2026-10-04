"""Conditional E2 cost floors with the original physical vehicle calendars.

Keep ALL orders of one satellite and its exact E2 formulation. Remove E1
route variables and every row/indicator using them, and relax the local
container pool to the entire local demand. Remaining E1 auxiliary variables
can take zero, so their residual stock rows impose no additional restriction.
The original network is retained, including directed nonmetric travel.

A full M5 schedule projects to this model whenever its supply completions
and early-return quantities satisfy the declared conditions. ObjBound is
therefore a conditional E2 lower bound, never a full M5 upper or proof.
"""
from dataclasses import replace
import math
from pathlib import Path
import time

from models.routing.common.cycle import MultiBatchInstance
from models.routing.common.cycle.audit import audit_solution


def build_physical_site(problem, site, *, supply_thresholds=None, minimum_returns=None,
                        deadline=None, verbose=False, compress_routes=None):
    import gurobipy as gp
    from .formulation import build_model
    if problem.model_number != 5 or problem.free_assignment or not problem.reusable:
        raise ValueError('Physical site floors require fixed reusable multibatch M5')
    orders = tuple(o for o in problem.orders if o.satellite == site)
    if site not in problem.satellites or not orders:
        raise ValueError('An active satellite is required')
    thresholds, required = dict(supply_thresholds or {}), dict(minimum_returns or {})
    total = sum(o.count for o in orders)
    present = {o.batch for o in orders}
    for values, maximum, batches in ((thresholds, problem.operating_policy.cycle_end, present),
                                     (required, total, set(problem.batch_map))):
        for bid, value in values.items():
            if bid not in batches or not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= maximum:
                raise ValueError('Conditions require known batches and bounded nonnegative integers')
    policy = replace(problem.operating_policy,
                     container_pool_size=max(problem.operating_policy.container_pool_size, total))
    local = MultiBatchInstance(problem.network, orders, policy, model=5)
    model, context = build_model(local, deadline=deadline, verbose=verbose,
        e2_capacity_cuts=False, compress_multibatch_routes=compress_routes)
    try:
        def check():
            if deadline is not None and time.perf_counter() >= deadline:
                raise TimeoutError('Physical site build reached its total budget')
        removed = {}
        def collect(value):
            if isinstance(value, gp.Var):
                removed[value.index] = value
            elif isinstance(value, gp.LinExpr):
                for i in range(value.size()): collect(value.getVar(i))
            elif isinstance(value, dict):
                for item in value.values(): collect(item)
            elif isinstance(value, (tuple, list)):
                for item in value: collect(item)
        for route in context['e1']: collect(route)
        rank_prefixes = tuple(r['label']+'_rank[' for r in context['e1'])
        for var in model.getVars():
            if var.VarName.startswith(rank_prefixes): collect(var)
        def touches(expression):
            return any(expression.getVar(i).index in removed for i in range(expression.size()))
        linear, indicators = [], []
        for i, row in enumerate(model.getConstrs()):
            if i % 256 == 0: check()
            if touches(model.getRow(row)): linear.append(row)
        for i, row in enumerate(model.getGenConstrs()):
            if i % 256 == 0: check()
            if row.GenConstrType != gp.GRB.GENCONSTR_INDICATOR:
                raise ValueError('Unexpected general constraint in physical-site projection')
            flag, _, expression, _, _ = model.getGenConstrIndicator(row)
            if flag.index in removed or touches(expression): indicators.append(row)
        objective = model.getObjective()
        e2_objective = gp.LinExpr()
        for i in range(objective.size()):
            var = objective.getVar(i)
            if var.index not in removed: e2_objective.addTerms(objective.getCoeff(i), var)
        proof = dict(removed_variables=[v.VarName for v in removed.values()],
            removed_linear_rows=[r.ConstrName for r in linear],
            removed_indicators=[r.GenConstrName for r in indicators],
            original_pool=problem.operating_policy.container_pool_size, local_relaxed_pool=policy.container_pool_size,
            local_container_demand=total, original_network_retained=True)
        model.remove(linear)
        model.remove(indicators)
        model.remove(list(removed.values()))
        model.setObjective(e2_objective)
        model.update()
        context['e1'] = []
        supplies = {bid: model.getVarByName(f'delivery_{site}_{bid}') for bid in present}
        for bid, value in thresholds.items(): supplies[bid].LB = max(supplies[bid].LB, value)
        flags = {}
        for bid, count in required.items():
            check()
            cutoff, expression = problem.batch_map[bid].cutoff, gp.LinExpr()
            for order in orders:
                returned = context['pickup_returns'][order.id]
                if returned.UB <= cutoff: flag = 1
                elif returned.LB > cutoff: flag = 0
                else:
                    flag = model.addVar(vtype=gp.GRB.BINARY, name=f'physical_early_{order.id}_{bid}')
                    model.addConstr((flag == 1) >> (returned <= cutoff))
                flags[order.id, bid] = flag
                expression += order.count*flag
            model.addConstr(expression >= count, name=f'physical_minimum_returns_{bid}')
        model.update()
        check()
        return model, {**context, 'local_problem': local, 'original_problem': problem,
            'satellite': site, 'supplies': supplies, 'supply_thresholds': thresholds,
            'minimum_returns': required, 'return_flags': flags, 'projection_proof': proof}
    except Exception:
        model.dispose()
        raise


def project_physical_site(problem, context, initial, *, fix=False):
    """Fix or start all route decisions from an audited original FULL schedule."""
    import gurobipy as gp
    audit = audit_solution(problem, initial)
    if not audit['valid']:
        raise ValueError('Projection requires an audited full M5 schedule')
    site = context['satellite']
    supplies = {(e['site_id'], t['batch_id']): e['start']+problem.service(e['site_id'])
        for t in initial['e1_trips'] for e in t['events'] if e['kind'] == 'd'}
    trips = [t for t in initial['e2_trips'] if t['satellite_id'] == site]
    returned = {e['order_id']: t['return_complete'] for t in trips for e in t['events'] if e['kind'] == 'p'}
    if any(supplies[site, bid] < value for bid, value in context['supply_thresholds'].items()):
        raise ValueError('Full schedule does not meet the conditional supply threshold')
    for bid, required in context['minimum_returns'].items():
        count = sum(o.count for o in problem.orders if o.satellite == site
                    and returned[o.id] <= problem.batch_map[bid].cutoff)
        if count < required: raise ValueError('Full schedule does not meet minimum early returns')
    def put(var, value):
        if isinstance(var, int):
            if var != value: raise ValueError('Projection contradicts a constant return flag')
            return
        var.Start = value
        if fix: var.LB = var.UB = value
    for bid, var in context['supplies'].items(): put(var, supplies[site, bid])
    for oid, var in context['pickup_returns'].items(): put(var, returned[oid])
    for (oid, bid), var in context['return_flags'].items():
        put(var, int(returned[oid] <= problem.batch_map[bid].cutoff))
    vehicles = {}
    for trip in trips: vehicles.setdefault(trip['vehicle_id'], []).append(trip)
    chosen = {}
    for vehicle, old in enumerate(sorted(vehicles, key=lambda v: (-len(vehicles[v]), v))):
        for slot, trip in enumerate(sorted(vehicles[old], key=lambda t: t['trip_index'])):
            chosen[vehicle, slot] = trip
    globals_ = context.get('global_e2_arcs', {})
    for var in globals_.values(): put(var, 0)
    covered = set()
    cost = 0.0
    for route in context['e2']:
        trip = chosen.get((route['vehicle'], route['slot']))
        indices = []
        if trip:
            lookup = {(n['order'].id, n['kind']): i for i,n in enumerate(route['nodes'])}
            indices = [lookup[e['order_id'], e['kind']] for e in trip['events']]
            covered.add((route['vehicle'], route['slot']))
        arcs = set(zip([-1]+indices, indices+[-2])) if trip else set()
        if not arcs <= route['x'].keys(): raise ValueError('Full trip has an absent physical-site arc')
        put(route['used'], int(trip is not None))
        put(route['start'], trip['departure'] if trip else 0)
        put(route['end'], trip['return_complete'] if trip else 0)
        for i,var in route['visit'].items(): put(var, int(i in indices))
        for arc,var in route['x'].items():
            if route.get('global_internal_arcs') and min(arc) >= 0:
                if arc in arcs: put(var, 1)
            else: put(var, int(arc in arcs))
        if trip:
            for index,event in zip(indices, trip['events']): put(route['times'][index], event['start'])
            places = [site]+[problem.order_map[e['order_id']].customer for e in trip['events']]+[site]
            cost += math.fsum(problem.distance(a,b) for a,b in zip(places,places[1:]))
    if covered != set(chosen): raise ValueError('Full schedule exceeds physical-site route slots')
    return dict(satellite=site, mapped_trips=len(trips), original_site_cost=cost,
                supply_thresholds=context['supply_thresholds'], minimum_returns=context['minimum_returns'])


def physical_site_certificate(problem, site, *, time_limit=60, supply_thresholds=None,
        minimum_returns=None, initial_result=None, seed=42, fallback_bound=0.0,
        verbose=False, diagnostics_path=None, compress_routes=None):
    """A conditional site-cost certificate; infeasibility remains subproblem-only."""
    import gurobipy as gp
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('time_limit must be finite and positive')
    if not math.isfinite(fallback_bound) or fallback_bound < 0:
        raise ValueError('fallback_bound must be finite and nonnegative')
    begun, model = time.perf_counter(), None
    result = dict(satellite=site, bound=fallback_bound, raw_bound=None, margin=0.0,
        status='TIME_LIMIT_BUILD', scope='M5_conditional_physical_E2_site_cost_relaxation',
        proven_optimal=False, proven_infeasible=False, subproblem_optimal=False,
        subproblem_infeasible=False, subproblem_objective=None,
        supply_thresholds=dict(supply_thresholds or {}), minimum_returns=dict(minimum_returns or {}))
    try:
        model, context = build_physical_site(problem, site, supply_thresholds=supply_thresholds,
            minimum_returns=minimum_returns, deadline=begun+time_limit, verbose=verbose,
            compress_routes=compress_routes)
        result['projection_proof'] = context['projection_proof']
        if initial_result is not None:
            result['initial_projection'] = project_physical_site(problem, context, initial_result)
        model.Params.Seed = int(seed)
        model.Params.IntFeasTol = model.Params.FeasibilityTol = 1e-9
        model.Params.DualReductions = 0
        model.Params.TimeLimit = max(0, begun+time_limit-time.perf_counter())
        model.optimize()
        raw = float(model.ObjBound)
        if math.isfinite(raw) and model.Status != gp.GRB.INFEASIBLE:
            margin = 1e-7*max(1, abs(raw))
            result.update(raw_bound=raw, margin=margin, bound=max(fallback_bound, max(0, raw-margin)))
        result.update(status=int(model.Status), subproblem_optimal=model.Status == gp.GRB.OPTIMAL,
            subproblem_infeasible=model.Status == gp.GRB.INFEASIBLE,
            subproblem_objective=float(model.ObjVal) if model.SolCount else None,
            variables=model.NumVars, constraints=model.NumConstrs,
            general_constraints=model.NumGenConstrs, nodes=model.NodeCount,
            e2_slots=len(context['e2']), build_seconds=time.perf_counter()-begun-model.Runtime)
        if diagnostics_path is not None:
            path = Path(diagnostics_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            model.write(str(path))
    except TimeoutError:
        pass
    finally:
        result['runtime'] = time.perf_counter()-begun
        if model is not None: model.dispose()
    return result
