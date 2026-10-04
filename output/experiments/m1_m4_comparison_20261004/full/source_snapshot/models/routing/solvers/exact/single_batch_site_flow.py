"""Warehouse flow for the compressed, free-assignment single-batch model."""
from __future__ import annotations


def add_site_flow(model, problem, routes, nodes_by_site, arcs_by_site,
                  global_arcs, assignments, check_budget):
    """Disaggregate global arcs over eligible warehouses without binary copies.

    A selected integer arc joins tasks on one physical route, hence at one
    warehouse. Assigning its flow to that warehouse extends every feasible
    integer solution. Conversely the original route-identity constraints stay
    in place. These equations only remove fractional cross-warehouse flows.
    """
    import gurobipy as gp

    choices = {key: [] for key in global_arcs}
    entering, leaving = {}, {}
    for site, nodes in nodes_by_site.items():
        check_budget()
        keys = [(n['order'].id, n['kind']) for n in nodes]
        for key in keys:
            entering[site, key], leaving[site, key] = [], []
        for a, b in arcs_by_site[site]:
            if a >= 0 and b >= 0:
                choices[keys[a], keys[b]].append(site)
    site_arcs = {}
    for index, (key, sites) in enumerate(choices.items()):
        if index % 64 == 0:
            check_budget()
        left, right = key
        for site in sites:
            # No auxiliary is needed when an arc is eligible at only one site.
            flow = global_arcs[key] if len(sites) == 1 else model.addVar(
                ub=1, name=f'e2_site_link_{index}_{site}')
            site_arcs[site, left, right] = flow
            leaving[site, left].append(flow)
            entering[site, right].append(flow)
        if len(sites) > 1:
            model.addConstr(gp.quicksum(site_arcs[site, left, right] for site in sites)
                            == global_arcs[key])
    for route in routes:
        check_budget()
        site = route['satellite']
        for i, node in enumerate(route['nodes']):
            key = node['order'].id, node['kind']
            if (-1, i) in route['x']:
                entering[site, key].append(route['x'][-1, i])
            if (i, -2) in route['x']:
                leaving[site, key].append(route['x'][i, -2])
    for (site, key), incoming in entering.items():
        check_budget()
        assigned = assignments[problem.order_map[key[0]].customer, site]
        model.addConstr(gp.quicksum(incoming) == assigned)
        model.addConstr(gp.quicksum(leaving[site, key]) == assigned)
    return site_arcs
