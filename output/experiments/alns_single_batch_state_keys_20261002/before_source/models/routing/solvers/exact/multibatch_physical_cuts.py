"""Conditional physical-site floors in a joint relaxation's cost epigraph.

The joint route domain may retain a cheaper path for an actual trip's task
set. A floor on executable E2 cost must therefore constrain theta_site, not
the compressed route-cost sum. Every full schedule projects with theta_site
equal to its actual site cost, which dominates the compressed cover cost.

Supply in the E1 projection can be earlier than actual supply. Thus a lower
threshold on projected supply also holds for actual supply. Cumulative E1
pickup quantities can never exceed containers returned by that cutoff.
These two monotone implications justify the conditional physical oracle.

This module accepts externally validated oracle certificates for the SAME
problem. It does not validate a caller-created certificate or establish
full M5 optimality. No public solver enables these experimental cuts yet.
"""
import math


def lift_physical_costs(model, context):
    """Replace compressed route objective terms by dominating site costs."""
    import gurobipy as gp
    if 'physical_costs' in context:
        return context['physical_costs']
    model.update()
    if model.ModelSense != gp.GRB.MINIMIZE:
        raise ValueError('Physical cost floors require minimization')
    old = model.getObjective()
    if not isinstance(old, gp.LinExpr):
        raise ValueError('Physical cost floors require a linear objective')
    costs, floors = {}, {}
    compressed = gp.LinExpr()
    for site, data in sorted(context['routes'].items()):
        expression = gp.LinExpr()
        for (i, _), var in data['choices'].items():
            cost = data['rows'][i]['column']['cost']
            if not math.isfinite(cost) or cost < 0 or abs(var.Obj-cost) > 1e-8:
                raise ValueError('Route objective must match nonnegative compressed column costs')
            expression.addTerms(cost, var)
        compressed += expression
        theta = model.addVar(lb=0, name=f'physical_cost_{site}')
        floors[site] = model.addConstr(theta >= expression, name=f'physical_cover_cost_{site}')
        costs[site] = theta
    model.setObjective(old-compressed+gp.quicksum(costs.values()))
    model.update()
    context['physical_costs'] = costs
    context['physical_cover_rows'] = floors
    return costs


def _integer_at_least(model, expression, threshold, lower, upper, name):
    """An exact iff flag; threshold-1 is valid only for integer expressions."""
    import gurobipy as gp
    if not isinstance(threshold, int) or isinstance(threshold, bool):
        raise ValueError('Thresholds must be integer')
    lower, upper = math.ceil(lower), math.floor(upper)
    if lower > upper:
        raise ValueError('Invalid expression bounds')
    if threshold <= lower:
        return 1
    if threshold > upper:
        return 0
    flag = model.addVar(vtype=gp.GRB.BINARY, name=name)
    model.addConstr(expression >= lower+(threshold-lower)*flag, name=name+'_lower')
    model.addConstr(expression <= threshold-1+(upper-threshold+1)*flag, name=name+'_upper')
    return flag


def add_physical_cut(problem, model, context, certificate, *, base_floor=0.0, name='physical_cut'):
    """Add an externally validated conditional floor or infeasibility no-good.

    base_floor must itself be an unconditional site-cost lower bound. The
    physical oracle bound must apply to all actual schedules satisfying its
    conditions, not to a restricted route pool or fixed physical assignment.
    """
    import gurobipy as gp
    if problem.model_number != 5 or problem.free_assignment or not problem.reusable:
        raise ValueError('Physical cuts require fixed reusable multibatch M5')
    if certificate.get('scope') != 'M5_conditional_physical_E2_site_cost_relaxation':
        raise ValueError('A physical-site certificate is required')
    site = certificate['satellite']
    if site not in context['routes']:
        raise ValueError('An active satellite is required')
    bound = certificate['bound']
    if not all(math.isfinite(v) and v >= 0 for v in (bound, base_floor)):
        raise ValueError('Cost floors must be finite and nonnegative')
    total = sum(o.count for o in problem.orders if o.satellite == site)
    thresholds = certificate.get('supply_thresholds', {})
    required = certificate.get('minimum_returns', {})
    present = {o.batch for o in problem.orders if o.satellite == site}
    for values, batches, maximum in ((thresholds, present, problem.operating_policy.cycle_end),
                                     (required, set(problem.batch_map), total)):
        for bid, value in values.items():
            if bid not in batches or not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= maximum:
                raise ValueError('Conditions require known batches and bounded integers')
    infeasible = certificate.get('subproblem_infeasible', False)
    if infeasible and certificate.get('status') != gp.GRB.INFEASIBLE:
        raise ValueError('A no-good requires proved subproblem infeasibility')
    costs = lift_physical_costs(model, context)
    theta = costs[site]
    theta.LB = max(theta.LB, base_floor)
    e1 = context['e1']
    supplies = e1.get('supplies', e1.get('deliveries'))
    if supplies is None:
        raise ValueError('Joint context must retain integer supply variables')
    triggers = []
    for bid, threshold in sorted(thresholds.items()):
        variable = supplies[site, bid]
        if variable.VType not in (gp.GRB.INTEGER, gp.GRB.BINARY):
            raise ValueError('Supply thresholds require integer variables')
        flag = _integer_at_least(model, variable, threshold, variable.LB, variable.UB,
                                 f'{name}_supply_{bid}')
        triggers.append(dict(flag=flag, expression=variable, threshold=threshold,
                             kind='supply', batch=bid))
    for bid, threshold in sorted(required.items()):
        # The E1 model equates these integer allocations to pickup quantities
        # at the satellite for each cutoff, for BOTH E1 backends.
        expression = gp.quicksum(o.count*e1['allocation'][o.id, batch.id]
            for o in problem.orders if o.satellite == site
            for batch in problem.operating_policy.batches
            if problem.batch_index[batch.id] <= problem.batch_index[bid])
        flag = _integer_at_least(model, expression, threshold, 0, total,
                                 f'{name}_returns_{bid}')
        triggers.append(dict(flag=flag, expression=expression, threshold=threshold,
                             kind='returns', batch=bid))
    flags = [t['flag'] for t in triggers]
    if any(isinstance(v, int) and v == 0 for v in flags):
        active = 0
    else:
        variables = [v for v in flags if not isinstance(v, int)]
        if not variables:
            active = 1
        elif len(variables) == 1:
            active = variables[0]
        else:
            active = model.addVar(vtype=gp.GRB.BINARY, name=name+'_active')
            for i, var in enumerate(variables):
                model.addConstr(active <= var, name=f'{name}_and_upper_{i}')
            model.addConstr(active >= gp.quicksum(variables)-len(variables)+1,
                            name=name+'_and_lower')
    row = None
    if infeasible:
        # A constant true condition makes the relaxation infeasible, while a
        # constant false condition adds no row. Neither changes proof flags.
        row = model.addConstr(gp.LinExpr(active) <= 0, name=name+'_no_good')
    elif bound > base_floor:
        row = model.addConstr(theta >= base_floor+(bound-base_floor)*active,
                              name=name+'_cost_floor')
    model.update()
    return dict(satellite=site, active=active, triggers=triggers, cost=theta, row=row,
        bound=bound, base_floor=base_floor, infeasible=infeasible,
        cost_target='actual_site_cost_epigraph', scope='experimental_conditional_physical_cut',
        proven_optimal=False, proven_infeasible=False)


def project_physical_costs(problem, context, initial, *, cuts=(), fix=False):
    """Set actual site costs and exact trigger flags after set_joint_projection."""
    from models.routing.common.cycle.audit import audit_solution
    if not audit_solution(problem, initial)['valid']:
        raise ValueError('Physical cost projection requires an audited full M5 schedule')
    def put(var, value):
        if isinstance(var, int):
            if var != value:
                raise ValueError('Projection contradicts a constant condition')
            return
        var.Start = value
        if fix:
            var.LB = var.UB = value
    values = {site:0.0 for site in context['physical_costs']}
    for trip in initial['e2_trips']:
        site = trip['satellite_id']
        places = [site]+[problem.order_map[e['order_id']].customer for e in trip['events']]+[site]
        values[site] += math.fsum(problem.distance(a,b) for a,b in zip(places,places[1:]))
    for site, var in context['physical_costs'].items():
        put(var, values[site])
    # Existing projection helpers let Gurobi derive supply variables from
    # selected E1 routes, so their Start attribute need not be populated.
    # Reconstruct the actual projection instead of reading an undefined start.
    e1 = context['e1']
    projected_supply = {}
    if context['backend'] == 'patterns':
        patterns = {(r['batch'], tuple(r['delivery']), tuple(r['pickup'])):r
                    for r in e1['patterns']}
        for trip in initial['e1_trips']:
            pattern = patterns[trip['batch_id'],
                tuple(e['site_id'] for e in trip['events'] if e['kind']=='d'),
                tuple(e['site_id'] for e in trip['events'] if e['kind']=='p')]
            projected_supply.update({(site,trip['batch_id']):value
                                      for site,value in pattern['supply'].items()})
    else:
        projected_supply = {(e['site_id'],trip['batch_id']):e['start']+problem.service(e['site_id'])
            for trip in initial['e1_trips'] for e in trip['events'] if e['kind']=='d'}
    supplies = e1.get('supplies', e1.get('deliveries'))
    for key, var in supplies.items():
        put(var, projected_supply[key])
    returned = {e['order_id']:trip['return_complete'] for trip in initial['e2_trips']
                for e in trip['events'] if e['kind']=='p'}
    for cut in cuts:
        enabled = []
        for trigger in cut['triggers']:
            if trigger['kind'] == 'supply':
                value = projected_supply[cut['satellite'], trigger['batch']]
            else:
                value = sum(o.count for o in problem.orders if o.satellite==cut['satellite']
                            and returned[o.id] <= problem.batch_map[trigger['batch']].cutoff)
            flag = int(round(value) >= trigger['threshold'])
            put(trigger['flag'], flag)
            enabled.append(flag)
        put(cut['active'], int(all(enabled)))
    return dict(actual_site_costs=values, cost_target='actual_site_cost_epigraph')
