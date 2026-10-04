"""Experimental integrality of task co-location in the complete route domain.

In a full schedule, two distinct tasks either share one E2 trip or do not.
Their common-route column sum is therefore 0 or 1, including compressed
continuous route columns. This adds necessary integrality, not a complete
physical vehicle model or a full optimality claim.
"""


def add_task_pair_integrality(problem, model, context, site, pairs, *, name='task_pair'):
    import gurobipy as gp
    if site not in context['routes']:
        raise ValueError('Unknown pair satellite')
    known = {(o.id, kind) for o in problem.orders if o.satellite == site for kind in ('d', 'p')}
    normalized = []
    for pair in pairs:
        keys = tuple(sorted(tuple(key) for key in pair))
        if len(keys) != 2 or keys[0] == keys[1] or any(key not in known for key in keys):
            raise ValueError('Task pairs require two distinct known site tasks')
        if keys in normalized:
            raise ValueError('Duplicate task pair')
        normalized.append(keys)
    data = context['routes'][site]
    task_sets = {i: frozenset(row['keys']) for i, row in enumerate(data['rows'])}
    records = []
    for i, keys in enumerate(normalized):
        required = frozenset(keys)
        expression = gp.quicksum(var for (row, _), var in data['choices'].items()
                                 if required <= task_sets[row])
        flag = model.addVar(vtype=gp.GRB.BINARY, name=f'{name}_{i}')
        model.addConstr(flag == expression, name=f'{name}_match_{i}')
        records.append(dict(site=site, tasks=keys, flag=flag, expression=expression,
                            scope='necessary_full_schedule_task_pair_integrality',
                            proven_optimal=False, proven_infeasible=False))
    model.update()
    return records


def project_task_pairs(problem, initial, records, *, fix=False):
    from models.routing.common.cycle.audit import audit_solution
    if not audit_solution(problem, initial)['valid']:
        raise ValueError('Task pair projection requires an audited full M5 schedule')
    trips = {}
    for trip in initial['e2_trips']:
        trips.setdefault(trip['satellite_id'], []).append(
            {(e['order_id'], e['kind']) for e in trip['events']})
    for record in records:
        tasks = set(record['tasks'])
        value = int(any(tasks <= keys for keys in trips.get(record['site'], [])))
        record['flag'].Start = value
        if fix:
            record['flag'].LB = record['flag'].UB = value
