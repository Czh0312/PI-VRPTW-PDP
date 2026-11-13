"""Valid, shared route-mask bounds added to the COMPLETE original arc MILP.

Do not replace a real route by its shortest relaxed representative. Instead,
partition tasks with an exhaustive optimistic mask library, link physical
adjacencies and trip counts, and lower-bound actual route distance. Every
original feasible schedule extends to these variables with its actual masks.
"""
import itertools


def first_echelon_patterns(problem):
    sites = list(problem.satellites)
    if len(sites) > 4:
        raise ValueError('Research E1 mask enumeration supports at most four satellites')
    size = len(sites)
    result = []
    for mask in range(1, 1 << (2*size)):
        ds = [i for i in range(size) if mask & (1 << i)]
        ps = [i for i in range(size) if mask & (1 << (size+i))]
        distance = min(
            sum(problem.distance(a,b) for a,b in zip(nodes,nodes[1:]))
            for left in itertools.permutations(ds)
            for right in itertools.permutations(ps)
            for nodes in [[problem.depot]+[sites[i] for i in left+right]+[problem.depot]]
        )
        result.append(dict(mask=mask, distance=distance))
    return result


def add_shared_route_bounds(problem, model, ctx, library):
    import gurobipy as gp

    orders = list(problem.orders)
    if any(o.count != 1 for o in orders) or len(orders) > 9:
        raise ValueError('Research route-mask bounds require at most nine one-box orders')
    if len({row['mask'] for row in library}) != len(library):
        raise ValueError('Library must have one minimum-distance representative per task mask')
    n = len(orders)
    index = {o.id:i for i,o in enumerate(orders)}
    choose = model.addVars(len(library), vtype=gp.GRB.BINARY, name='bound_shared_mask')
    for task in range(2*n):
        model.addConstr(gp.quicksum(choose[c] for c,row in enumerate(library)
                                   if row['mask'] & (1 << task)) == 1,
                        name=f'bound_mask_cover_{task}')
    adjacency_rows = 0
    for site in problem.satellites:
        local = [(c,row) for c,row in enumerate(library) if row['satellite'] == site]
        actual = [route for route in ctx['e2'] if route['base'] == site]
        if not actual:
            continue
        model.addConstr(gp.quicksum(choose[c] for c,_ in local)
                        == gp.quicksum(route['used'] for route in actual),
                        name=f'bound_trip_count_{site}')
        model.addConstr(gp.quicksum(route['distance'] for route in actual)
                        >= gp.quicksum(row['distance']*choose[c] for c,row in local),
                        name=f'bound_mask_distance_{site}')
        nodes = actual[0]['nodes']
        edges = sorted({edge for route in actual for edge in route['x']
                        if edge[0] >= 0 and edge[1] >= 0})
        for a,b in edges:
            tasks = [index[nodes[j]['order'].id]+(n if nodes[j]['kind']=='p' else 0)
                     for j in (a,b)]
            both = (1 << tasks[0]) | (1 << tasks[1])
            support = gp.quicksum(choose[c] for c,row in local if row['mask'] & both == both)
            edge = gp.quicksum(route['x'].get((a,b),0) for route in actual)
            model.addConstr(edge <= support, name=f'bound_mask_adjacency_{site}_{a}_{b}')
            adjacency_rows += 1
    # A trip's deliveries all come from the same original batch. This links
    # the shared library to actual assignment decisions, not just total cost.
    groups = sorted({row['deliveries'] for row in library if row['deliveries'].bit_count() > 1})
    batches = [b.id for b in problem.operating_policy.batches]
    together = model.addVars(groups,batches,lb=0,ub=1,name='bound_same_delivery_batch')
    for group in groups:
        for batch in batches:
            for i,order in enumerate(orders):
                if group & (1 << i):
                    model.addConstr(together[group,batch] <= ctx['assignments'].get((order.id,batch),0))
    for c,row in enumerate(library):
        if row['deliveries'] in groups:
            model.addConstr(choose[c] <= gp.quicksum(together[row['deliveries'],b] for b in batches))

    # At binary original visits, a convex combination of binary patterns
    # matching those visits can only use the identical mask. Its minimum
    # phase-ordered distance therefore safely bounds the actual E1 distance.
    patterns = first_echelon_patterns(problem)
    sites = list(problem.satellites)
    for route in ctx['e1']:
        weight = model.addVars(len(patterns),lb=0,ub=1,name=route['label']+'_bound_e1_mask')
        model.addConstr(weight.sum() == route['used'])
        for j,node in enumerate(route['nodes']):
            task = sites.index(node['site'])+(len(sites) if node['kind']=='p' else 0)
            model.addConstr(route['visit'][j] == gp.quicksum(
                weight[c] for c,row in enumerate(patterns) if row['mask'] & (1 << task)))
        model.addConstr(route['distance'] >= gp.quicksum(row['distance']*weight[c]
                                                        for c,row in enumerate(patterns)),
                        name=route['label']+'_bound_e1_distance')
    model.update()
    return dict(shared_mask_columns=len(library), physical_adjacency_rows=adjacency_rows,
                delivery_groups=len(groups), e1_patterns=len(patterns),
                scope='Additive valid bounds; all original arcs, clocks, inventory and vehicle constraints retained')
