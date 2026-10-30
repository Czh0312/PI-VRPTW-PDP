"""Perspective capacity inequalities for activated physical trips."""


def add_activation_capacity(problem, context):
    """Bound outbound goods and inbound empties separately by used*capacity.

    All deliveries are loaded at the trip's warehouse departure and all
    pickups stay aboard until its return. Delivery plus pickup totals must
    NOT be added together: the same box capacity may serve both directions.
    """
    import gurobipy as gp

    model = context['variables'].model
    count = 0
    for layer, weight_cap, volume_cap in (
        ('e1', problem.fleet.e1_weight_cap, problem.fleet.e1_volume_cap),
        ('e2', problem.fleet.e2_weight_cap, problem.fleet.e2_volume_cap),
    ):
        for route in context[layer]:
            for kind in problem.task_kinds:
                for resource, capacity in (('w', weight_cap), ('v', volume_cap)):
                    terms = [node[resource] if layer == 'e1' and problem.free_assignment
                             else node[resource]*route['visit'][i]
                             for i, node in enumerate(route['nodes']) if node['kind'] == kind]
                    if terms:
                        model.addConstr(gp.quicksum(terms) <= capacity*route['used'],
                            name=f'{route["label"]}_{kind}_{resource}_activated_capacity')
                        count += 1
    return count
