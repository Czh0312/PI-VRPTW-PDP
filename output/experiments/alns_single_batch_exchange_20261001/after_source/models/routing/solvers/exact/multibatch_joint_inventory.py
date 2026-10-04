"""Joint COMPLETE E2 route coverage and E1 cutoff/clean-stock relaxation.

Only route task sets and necessary return-time bounds enter the E1 links.
The cheapest route's actual clock is NOT imposed: cost dominance need not
preserve actual supply, timing, or cross-route vehicle feasibility.
"""
from __future__ import annotations

import math
from pathlib import Path
import time

from models.routing.common.cycle.precheck import travel_lower_bounds


def cutoff_domains(problem, context, universe, travel):
    """Every actual trip returns after every task's necessary service/return."""
    if not universe['complete'] or not all(g['complete'] for g in universe['groups']):
        raise ValueError('Joint inventory requires complete E2 route universes')
    p, site = problem.operating_policy, context['satellite']
    after = {n['key']: n['window'][0]+n['order'].service_time
             +travel['inward'][site][n['order'].customer]+p.e2_return_unload_time
             for n in context['nodes']}
    rows = []
    for group in universe['groups']:
        for column in group['columns']:
            keys = tuple(tuple(k) for k in column['tasks'])
            pickups = tuple(oid for oid, kind in keys if kind == 'p')
            earliest = max(after[k] for k in keys)
            allowed = tuple(b.id for b in p.batches if b.cutoff >= earliest) if pickups else (None,)
            if not allowed:
                raise ValueError('A complete feasible trip has no necessary return cutoff')
            rows.append(dict(column=column, keys=keys, pickups=pickups,
                             minimum_return_bound=int(earliest), allowed_cutoffs=allowed))
    return rows


def build_joint_inventory(problem, domains, *, deadline=None, verbose=False,
                          travel_bounds=None, distance_bounds=None, e1_bound=0.0, site_bounds=None):
    """Retain every cutoff-compatible column, and couple whole-trip pickups."""
    from gurobipy import GRB
    import gurobipy as gp
    p = problem.operating_policy
    travel = travel_bounds or travel_lower_bounds(problem)
    active = {o.satellite for o in problem.orders}
    if set(domains) != active:
        raise ValueError('Joint inventory requires complete domains for every active satellite')
    backend = 'patterns' if len(active) <= 4 else 'indexed'
    if backend == 'patterns':
        from .multibatch_e1_routes import build_e1_routes
        model, e1 = build_e1_routes(problem, deadline=deadline, verbose=verbose,
            travel_bounds=travel, distance_bounds=distance_bounds)
    else:
        from .multibatch_e1_inventory import build_e1_inventory
        model, e1 = build_e1_inventory(problem, deadline=deadline, verbose=verbose,
            travel_bounds=travel, distance_bounds=distance_bounds)
    def check():
        if deadline is not None and time.perf_counter() >= deadline:
            raise TimeoutError('Joint inventory build reached its original budget')
    try:
        e1_objective = model.getObjective()
        if e1_bound > 0:
            model.addConstr(e1_objective >= e1_bound, name='certified_E1_cost')
        link = {(o.id, b.id): model.addConstr(e1['allocation'][o.id, b.id] == 0,
                                            name=f'joint_cutoff_{o.id}_{b.id}')
                for o in problem.orders for b in p.batches}
        routes, all_choices, site_costs = {}, [], {}
        for site_index, site in enumerate(sorted(active)):
            check()
            context, universe = domains[site]
            rows = cutoff_domains(problem, context, universe, travel)
            cover = {n['key']: model.addConstr(gp.LinExpr() == 1, name=f'joint_cover_{site_index}_{i}')
                     for i, n in enumerate(context['nodes'])}
            fleet = model.addConstr(gp.LinExpr() <= context['maximum_trips'], name=f'joint_trip_limit_{site_index}')
            cost = model.addConstr(gp.LinExpr() >= (site_bounds or {}).get(site, 0.0), name=f'certified_E2_cost_{site_index}')
            choices, site_cost = {}, gp.LinExpr()
            for i, row in enumerate(rows):
                if i % 256 == 0:
                    check()
                for batch_index, bid in enumerate(row['allowed_cutoffs']):
                    coefficients = [1]*len(row['keys'])+[1, row['column']['cost']]
                    constraints = [cover[k] for k in row['keys']]+[fleet, cost]
                    if bid is not None:
                        coefficients += [-1]*len(row['pickups'])
                        constraints += [link[oid, bid] for oid in row['pickups']]
                    var = model.addVar(vtype=GRB.BINARY, obj=row['column']['cost'],
                        column=gp.Column(coefficients, constraints), name=f'joint_route_{site_index}_{i}_{batch_index}')
                    choices[i, bid] = var
                    all_choices.append(var)
                    site_cost += row['column']['cost']*var
            routes[site] = dict(context=context, rows=rows, choices=choices)
            site_costs[site] = site_cost
        model.update()
        check()
        return model, dict(e1=e1, backend=backend, routes=routes, e1_objective=e1_objective,
                           site_costs=site_costs, choices=all_choices, link=link)
    except Exception:
        model.dispose()
        raise


def set_joint_projection(problem, context, initial_result, *, fix=False):
    """Project the original task sets/cutoffs, retaining original order times."""
    if context['backend'] == 'patterns':
        from .multibatch_e1_routes import set_route_projection
        set_route_projection(problem, context['e1'], initial_result, fix=fix)
    else:
        from .multibatch_e1_inventory import set_e1_projection
        set_e1_projection(problem, context['e1'], initial_result, fix=fix)
    def put(var, value):
        var.Start = value
        if fix:
            var.LB = var.UB = value
    for trip in initial_result['e2_trips']:
        for event in trip['events']:
            put(context['e1']['order_times'][event['order_id'], event['kind']], event['start'])
    selected, projected_cost = {}, 0.0
    for site, data in context['routes'].items():
        lookup = {frozenset(row['keys']): i for i, row in enumerate(data['rows'])}
        chosen = set()
        for trip in initial_result['e2_trips']:
            if trip['satellite_id'] != site:
                continue
            task_set = frozenset((e['order_id'], e['kind']) for e in trip['events'])
            i = lookup[task_set]
            bid = next(b.id for b in problem.operating_policy.batches if trip['return_complete'] <= b.cutoff) \
                if data['rows'][i]['pickups'] else None
            if (i, bid) not in data['choices']:
                raise ValueError('Full-M5 trip failed to project to a joint cutoff column')
            chosen.add((i, bid))
            projected_cost += data['rows'][i]['column']['cost']
        for key, var in data['choices'].items():
            put(var, int(key in chosen))
        selected[site] = len(chosen)
    return dict(mapped_trips=selected, projected_e2_cost=projected_cost)


def solve_joint_inventory(problem, domains, *, deadline, initial_result=None, seed=42,
                          verbose=False, travel_bounds=None, distance_bounds=None,
                          e1_bound=0.0, site_bounds=None, fallback_bound=0.0, diagnostics_dir=None):
    """Return a solver LOWER bound on joint cost; never a full-M5 upper bound."""
    from gurobipy import GRB
    import gurobipy as gp
    begun, model, context = time.perf_counter(), None, None
    result = dict(bound=fallback_bound, raw_bound=None, margin=0.0, status='TIME_LIMIT_BUILD',
        scope='M5_joint_E1_inventory_and_integer_E2_cutoff_cover_relaxation',
        proven_optimal=False, proven_infeasible=False, relaxation_optimal=False,
        relaxation_infeasible=False, relaxation_objective=None, initial_projection=None,
        complete_route_universes=True, runtime=0.0, selected_columns=[])
    try:
        model, context = build_joint_inventory(problem, domains, deadline=deadline, verbose=verbose,
            travel_bounds=travel_bounds, distance_bounds=distance_bounds, e1_bound=e1_bound, site_bounds=site_bounds)
        model.Params.Seed = int(seed)
        model.Params.IntFeasTol = model.Params.FeasibilityTol = 1e-9
        if initial_result is not None:
            result['initial_projection'] = set_joint_projection(problem, context, initial_result)
        model.Params.TimeLimit = max(0, deadline-time.perf_counter())
        model.optimize()
        try:
            raw = float(model.ObjBound)
        except (gp.GurobiError, AttributeError):
            raw = math.nan
        if math.isfinite(raw) and model.Status != GRB.INFEASIBLE:
            margin = 1e-7*max(1.0, abs(raw))
            result.update(raw_bound=raw, margin=margin, bound=max(fallback_bound, max(0, raw-margin)))
        result.update(status=int(model.Status), relaxation_optimal=model.Status == GRB.OPTIMAL,
            relaxation_infeasible=model.Status == GRB.INFEASIBLE,
            variables=model.NumVars, constraints=model.NumConstrs, general_constraints=model.NumGenConstrs,
            route_choices=len(context['choices']), backend=context['backend'], nodes=model.NodeCount)
        if model.SolCount:
            result['relaxation_objective'] = float(model.ObjVal)
            result['selected_columns'] = [dict(satellite=site, column_index=i, cutoff=bid,
                tasks=data['rows'][i]['column']['tasks'], cost=data['rows'][i]['column']['cost'])
                for site, data in context['routes'].items() for (i, bid), v in data['choices'].items() if v.X > .5]
        if diagnostics_dir is not None:
            path = Path(diagnostics_dir)
            path.mkdir(parents=True, exist_ok=True)
            model.write(str(path/'joint_inventory.mps.gz'))
        return result
    except TimeoutError:
        return result
    finally:
        result['runtime'] = time.perf_counter()-begun
        if model is not None:
            model.dispose()
