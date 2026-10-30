"""Sparse arc conflicts implied by original d->p and route causal steps."""


def conflict_pairs(nodes, arcs):
    internal = {(a, b) for a, b in arcs if a >= 0 and b >= 0}
    by_order = {}
    for i, node in enumerate(nodes):
        by_order.setdefault(node['order'].id, {})[node['kind']] = i
    result = set()
    for a, b in sorted(internal):
        successors = [b]
        predecessors = [a]
        if nodes[b]['kind'] == 'd':
            successors += [by_order[nodes[b]['order'].id]['p']]
        if nodes[a]['kind'] == 'p':
            predecessors += [by_order[nodes[a]['order'].id]['d']]
        for c in successors:
            for d in predecessors:
                other = c, d
                if other in internal and other != (a, b):
                    result.add(tuple(sorted(((a, b), other))))
    return sorted(result)


def add_conflicts(parameters, variables, nodes_by_site, arcs_by_site, *, check_budget):
    if parameters.free_assignment or not parameters.problem.reusable:
        return dict(applied=False, reason='outside_fixed_reusable_scope')
    import gurobipy as gp
    result = dict(applied=True, rows=0, sites={}, time_lifts=0)
    for site, nodes in nodes_by_site.items():
        check_budget()
        keys = [(node['order'].id, node['kind']) for node in nodes]
        routes = [r for r in variables.routes['e2'] if r['satellite'] == site]
        active = {}
        for a, b in arcs_by_site[site]:
            if a < 0 or b < 0:
                continue
            shared = variables.aggregate_arcs.get((keys[a], keys[b]))
            active[a, b] = shared if shared is not None else gp.quicksum(
                r['x'][a, b] for r in routes if (a, b) in r['x'])
        pairs = conflict_pairs(nodes, arcs_by_site[site])
        for index, (first, second) in enumerate(pairs):
            if index % 64 == 0:
                check_budget()
            variables.model.addConstr(active[first]+active[second] <= 1,
                                      name=f'e2_{site}_precedence_conflict_{index}')
        result['rows'] += len(pairs)
        result['sites'][site] = len(pairs)
        if parameters.share_e2:
            by_order = {}
            for i, node in enumerate(nodes):
                by_order.setdefault(node['order'].id, {})[node['kind']] = i
            for (a, b), selected in active.items():
                check_budget()
                prefix, suffix = [(a, 0)], [(b, 0)]
                if nodes[a]['kind'] == 'p':
                    order = nodes[a]['order']
                    prefix.append((by_order[order.id]['d'], order.service_time+order.min_use_time))
                if nodes[b]['kind'] == 'd':
                    order = nodes[b]['order']
                    suffix.append((by_order[order.id]['p'], order.service_time+order.min_use_time))
                travel = nodes[a]['service']+parameters.problem.travel(nodes[a]['site'], nodes[b]['site'])
                original_slack = max(0, parameters.task_windows[keys[a]][1]+travel
                                     -parameters.task_windows[keys[b]][0])
                for source, before in prefix:
                    for target, after in suffix:
                        if source == a and target == b:
                            continue
                        lag = before+travel+after
                        slack = max(0, parameters.task_windows[keys[source]][1]+lag
                                    -parameters.task_windows[keys[target]][0])
                        if 0 < slack < original_slack-1e-7:
                            variables.model.addConstr(variables.task_times[keys[target]]
                                >= variables.task_times[keys[source]]+lag-slack*(1-selected))
                            result['time_lifts'] += 1
    return result
