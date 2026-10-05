"""Integer temporal separation for an elementary pure-delivery pricing path."""


def infeasible_time_path(problem, context, indices):
    """Return valid forbidden-prefix/full-path arcs, or None if time feasible.

    A violated customer upper window cannot be repaired by appending tasks.
    A violated RETURN can be repaired in a nonmetric network, so that cut
    includes the terminal arc and only forbids the complete selected path.
    Capacity and elementary-path structure are enforced by the pricing master.
    """
    nodes, site = context['nodes'], context['satellite']
    clock, previous, prefix = context['release'], -1, []
    for index in indices:
        node = nodes[index]
        left = site if previous == -1 else nodes[previous]['order'].customer
        clock = max(clock+problem.travel(left,node['order'].customer),node['window'][0])
        prefix.append((previous,index))
        if clock > node['window'][1]:
            return tuple(prefix), 'window'
        clock += node['order'].service_time
        previous = index
    if previous == -1:
        raise ValueError('Temporal separation requires a nonempty elementary path')
    clock += problem.travel(nodes[previous]['order'].customer,site)+problem.operating_policy.e2_return_unload_time
    if clock > context['end_limit']:
        return tuple(prefix+[(previous,-2)]), 'return'
    return None
