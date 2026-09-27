"""M8 v2: fixed delivery backbone, free pickups and homogeneous handoffs.

Uses the complete M6 feasible set with E1 routes, delivery ownership/order and
available vehicle labels conditioned on one baseline. Pickup-only vehicles may
retire; no new vehicle is activated. No capacity-trigger pruning in this MILP.
"""
import gurobipy as gp

from models.routing.variants.model_6.model import build_model as build_compact


def build_model(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                baseline, TFp=9999.0, allow_transfers=True):
    count = max((len(p) for p in baseline['e2_routes'].values()), default=0)
    model, variables, ctx = build_compact(
        problem, e1_tasks, e2_tasks_by_sat, count, TFp=TFp, strengthen=False)
    model.ModelName = 'Model8_Capacity_Cooperation'
    arcs = {(i, j, k) for k, path in enumerate(baseline['e1_routes'])
            for i, j in zip(path, path[1:])}
    for key, var in variables['x'].items():
        var.LB = var.UB = int(key in arcs)
        var.Start = var.LB
    owner = {n: (s, k) for s, paths in baseline['e2_routes'].items()
             for k, path in enumerate(paths) for n in path[1:-1]}
    for (n, vehicle), var in variables['serve'].items():
        if ctx['events'][n].kind == 'd':
            var.LB = var.UB = int(owner[n] == vehicle)
        var.Start = int(owner[n] == vehicle)
    for vehicle, var in variables['used'].items():
        if vehicle[1] >= len(baseline['e2_routes'][vehicle[0]]):
            var.UB = 0
        var.Start = int(vehicle[1] < len(baseline['e2_routes'][vehicle[0]]))
    for group in ('transfer', 'transfer_active'):
        for var in variables[group].values():
            var.Start = 0
            if not allow_transfers:
                var.UB = 0
    route_arcs = {(i, j, (s, k)) for s, paths in baseline['e2_routes'].items()
                  for k, r in enumerate(paths) for i, j in zip(r, r[1:])}
    for key, var in variables['z'].items():
        var.Start = int(key in route_arcs)
    for (n, v), var in variables['visit'].items():
        var.Start = int(owner[n] == v)
    rank = {}
    for s, nodes in ctx['events_by_sat'].items():
        limit = len(nodes) + 1
        for v in ctx['V_by_sat'][s]:
            for n in nodes:
                rank[n, v] = model.addVar(lb=0, ub=limit, name=f'rank_{n}_{v[1]}')
        for (i, j, v), arc in variables['z'].items():
            if v[0] == s and i != s and j != s:
                model.addConstr(rank[j, v] >= rank[i, v] + 1 - (limit + 1) * (1 - arc))
        for k, path in enumerate(baseline['e2_routes'][s]):
            deliveries = [n for n in path[1:-1] if ctx['events'][n].kind == 'd']
            for a, b in zip(deliveries, deliveries[1:]):
                model.addConstr(rank[b, (s, k)] >= rank[a, (s, k)] + 1)
    variables['backbone_rank'] = rank
    ctx.update(baseline=baseline,
               proof_scope='conditional_on_delivery_backbone_and_e1_routes')
    model.update()
    return model, variables, ctx
