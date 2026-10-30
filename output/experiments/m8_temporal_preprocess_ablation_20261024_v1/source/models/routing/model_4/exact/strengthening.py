"""Valid M4 inequalities; no external incumbent, bound, or search callback.

Every customer set includes ALL its delivery and pickup task copies. During
each route segment inside that set, at most one vehicle capacity of goods can
be delivered (or empties collected). Its integer number of entering arcs must
therefore cover the rounded demand/capacity bound. Distances may be directed
and nonmetric; neither geometry nor a nearest-warehouse restriction is used.
"""
from collections import defaultdict
from itertools import combinations
import math


PROFILES = ('none', 'bounds', 'capacity', 'combined', 'activation', 'capacity_activation')


def tighten_auxiliary_bounds(problem, context):
    """Tighten only artificial causal ranks and implied departure bounds."""
    params, variables = context['parameters'], context['variables']
    limit = context['e2_causal_limit']
    for (oid, kind), variable in variables.task_steps.items():
        variable.LB = 2 if kind == 'p' else 1
        variable.UB = limit-2 if problem.reusable and kind == 'd' else limit-1
    for route in context['e2']:
        slot = route['slot']
        # k preceding nonempty trips consume start -> task -> end -> next
        # start, at least three artificial edges per completed trip.
        route['step_start'].LB, route['step_start'].UB = 3*slot, limit-2
        route['step_end'].LB, route['step_end'].UB = 3*slot+2, limit
        latest = max((node['window'][1]-problem.travel(route['base'], node['site'])
                      for node in route['nodes']), default=0)
        # A route's first task is reached directly from its base. Its clock
        # cannot depart later than every possible first task's latest start.
        route['start'].UB = max(0, min(route['start'].UB, latest))
        variables.model.addConstr(route['start'] <= max(0, latest)*route['used'],
                                  name=f'{route["label"]}_departure_limit')
    return len(variables.task_steps)+len(context['e2'])*3


def add_rounded_capacity_constraints(problem, context, check_budget=lambda: None):
    """Round route-entry capacity bounds over complete customer task sets."""
    import gurobipy as gp

    customers = sorted({order.customer for order in problem.orders})
    source, internal = defaultdict(list), defaultdict(list)
    for route in context['e2']:
        for (left, right), variable in route['x'].items():
            if left == -1:
                source[route['nodes'][right]['order'].customer].append(variable)
            elif right >= 0 and not context['shared_e2']:
                a, b = (route['nodes'][i]['order'].customer for i in (left, right))
                if a != b:
                    internal[a, b].append(variable)
    if context['shared_e2']:
        for (left, right), variable in context['variables'].aggregate_arcs.items():
            a, b = (problem.order_map[key[0]].customer for key in (left, right))
            if a != b:
                internal[a, b].append(variable)
    amounts = {c: [0., 0., 0., 0.] for c in customers}
    for order in problem.orders:
        for kind, offset in (('d', 0), ('p', 2)):
            if kind not in problem.task_kinds:
                continue
            _, weight, volume = problem.task(order.id, kind)
            amounts[order.customer][offset] += weight
            amounts[order.customer][offset+1] += volume
    f = problem.fleet
    capacities = (f.e2_weight_cap, f.e2_volume_cap)*2
    # Exhaustive at the small scale. Larger instances get low-order sets and
    # the complete customer set, avoiding exponential model construction.
    sizes = range(1, len(customers)+1) if len(customers) <= 12 else [1, 2, 3, len(customers)]
    count = 0
    for size in sizes:
        for members in combinations(customers, size):
            check_budget()
            chosen = set(members)
            totals = [sum(amounts[c][i] for c in members) for i in range(4)]
            # Small conservative slack avoids rounding a floating ratio just
            # above an integer into an invalid extra vehicle requirement.
            needed = max([1]+[math.ceil(value/(cap+1e-6)-1e-9)
                                  for value, cap in zip(totals, capacities) if cap > 0])
            terms = [v for c in members for v in source[c]]
            terms.extend(v for (a, b), variables in internal.items()
                         if a not in chosen and b in chosen for v in variables)
            context['variables'].model.addConstr(gp.quicksum(terms) >= needed,
                                                  name=f'customer_capacity_{count}')
            count += 1
    return count


def add_first_echelon_connectivity(problem, context, check_budget=lambda: None):
    """Connect each activated warehouse set to the central depot."""
    import gurobipy as gp
    model = context['variables'].model
    sites = sorted(context['variables'].active_sites)
    count = 0
    sizes = range(1, len(sites)+1) if len(sites) <= 10 else [1, 2, len(sites)]
    for size in sizes:
        for members in combinations(sites, size):
            check_budget()
            chosen = set(members)
            entering = [variable for route in context['e1'] for (left, right), variable in route['x'].items()
                        if right >= 0 and route['nodes'][right]['site'] in chosen
                        and (left == -1 or route['nodes'][left]['site'] not in chosen)]
            expression = gp.quicksum(entering)
            for site in members:
                model.addConstr(expression >= context['variables'].active_sites[site],
                                name=f'warehouse_connectivity_{count}')
                count += 1
    weight = sum(problem.task(order.id, 'd')[1] for order in problem.orders)
    volume = sum(problem.task(order.id, 'd')[2] for order in problem.orders)
    necessary = max([1]+[math.ceil(value/(cap+1e-6)-1e-9) for value, cap in
        ((weight, problem.fleet.e1_weight_cap), (volume, problem.fleet.e1_volume_cap)) if cap > 0])
    if problem.orders:
        model.addConstr(gp.quicksum(route['used'] for route in context['e1']) >= necessary,
                        name='first_echelon_minimum_departures')
        count += 1
    return count


def strengthen(problem, context, profile, check_budget=lambda: None):
    if profile not in PROFILES:
        raise ValueError(f'Unknown M4 strengthening profile: {profile}')
    statistics = dict(profile=profile, auxiliary_bounds=0, rounded_capacity_constraints=0,
                      warehouse_connectivity=0, activation_capacity=0)
    if profile in ('bounds', 'combined'):
        statistics['auxiliary_bounds'] = tighten_auxiliary_bounds(problem, context)
    if profile in ('capacity', 'combined', 'capacity_activation'):
        statistics['rounded_capacity_constraints'] = add_rounded_capacity_constraints(problem, context, check_budget)
        statistics['warehouse_connectivity'] = add_first_echelon_connectivity(problem, context, check_budget)
    if profile in ('activation', 'capacity_activation'):
        from models.routing.model_4.exact.activation_capacity import add_activation_capacity
        statistics['activation_capacity'] = add_activation_capacity(problem, context)
    return statistics
