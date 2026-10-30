"""Static directed connectivity / rounded-capacity inequalities for M8 E2.

Every feasible trip starts at its satellite and visits each task at most once.
For a task set T, its internal arcs are at most |T| minus the number of trips
visiting T. Total delivery (and final pickup) loads give a lower bound on that
number of trips. Apply this to delivery, pickup and paired task sets of each
order subset. The proof needs no metric distances or fixed order batches.
"""
from itertools import combinations
import math


def conservative_ceiling(value, capacity):
    if capacity <= 0:
        raise ValueError('Rounded capacity bounds require positive vehicle capacities')
    ratio = value/capacity
    return math.ceil(ratio-1e-9*max(1.,abs(ratio)))


def subset_bounds(problem):
    f = problem.fleet
    for site in problem.satellites:
        orders = [o for o in problem.orders if o.satellite == site]
        # Bounded small-case probe. Larger sites retain singleton/pair/full
        # subsets, without claiming a complete subset separation procedure.
        sizes = range(1,len(orders)+1) if len(orders)<=10 else sorted({1,2,len(orders)})
        for size in sizes:
            for selected in combinations(range(len(orders)),size):
                totals = {}
                for kind in ('d','p'):
                    weights = [problem.task(orders[i].id,kind)[1] for i in selected]
                    volumes = [problem.task(orders[i].id,kind)[2] for i in selected]
                    totals[kind] = max(1,conservative_ceiling(math.fsum(weights),f.e2_weight_cap),
                                       conservative_ceiling(math.fsum(volumes),f.e2_volume_cap))
                for kinds in [('d',),('p',),('d','p')]:
                    tasks = [2*i+(0 if k=='d' else 1) for i in selected for k in kinds]
                    yield dict(site=site,orders=[orders[i].id for i in selected],kinds=list(kinds),tasks=tasks,
                               minimum_routes=max(totals[k] for k in kinds))


def add_capacity_bounds(problem,model,context):
    import gurobipy as gp

    groups = {}
    for r in context['e2']:
        site = r['satellite']
        for (a,b),variable in r['x'].items():
            if a>=0 and b>=0:
                groups.setdefault((site,a,b),[]).append(variable)
    physical = context['variables'].global_arcs
    edges = {key:physical[key] if key in physical else gp.quicksum(copies)
             for key,copies in groups.items()}
    count = 0
    for row in subset_bounds(problem):
        tasks = set(row['tasks'])
        terms = [expr for (site,a,b),expr in edges.items() if site==row['site'] and a in tasks and b in tasks]
        rhs = len(tasks)-row['minimum_routes']
        if not terms and rhs>=0:
            continue
        model.addConstr(gp.quicksum(terms)<=rhs,name=f'e2_subset_capacity_{count}')
        count += 1
    return dict(rows=count,source='static_directed_task_subset_connectivity_capacity',
                scope='all order subsets at sites of at most 10 orders; otherwise singleton/pair/full',
                capacity_ceiling_relative_slack=1e-9)
