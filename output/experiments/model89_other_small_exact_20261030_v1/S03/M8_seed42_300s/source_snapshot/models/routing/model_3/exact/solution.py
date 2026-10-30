"""Decode the model incumbent; feasibility and proofs remain with the caller."""


def extract_solution(problem, model, ctx, result):
    """Populate a JSON-native result before the Gurobi model is disposed."""
    if problem.free_assignment:
        result['assignment'] = {c: s for (c, s), variable in ctx['assignments'].items() if variable.X > .5}

    def int_value(var):
        raw = var if isinstance(var, int) else var.X
        if abs(raw-round(raw)) > 1e-4:
            raise RuntimeError(f'Nonintegral schedule/quantity in incumbent: {raw}')
        return int(round(raw))

    for layer in ('e1', 'e2'):
        for r in ctx[layer]:
            if r['used'].X < 0.5:
                continue
            following = {a: b for (a, b), x in r['x'].items() if x.X > 0.5 and
                         (not r.get('global_internal_arcs') or
                          (a == -1 or r['visit'][a].X > .5) and (b == -2 or r['visit'][b].X > .5))}
            order, cursor = [], -1
            while following.get(cursor, -2) != -2:
                cursor = following[cursor]
                if cursor in order:
                    raise RuntimeError('Cycle in extracted route')
                order.append(cursor)
            expected = {i for i, v in r['visit'].items() if v.X > 0.5}
            if set(order) != expected or len(order) != len(expected):
                raise RuntimeError('Disconnected incumbent route')
            trip = dict(trip_id=r['label'], vehicle_id=r['vehicle'],
                        departure=int_value(r['start']), return_complete=int_value(r['end']), events=[])
            if layer == 'e1':
                trip['batch_id'] = r['batch'].id
            else:
                trip.update(satellite_id=r['satellite'], trip_index=r['slot'])
            for i in order:
                n = r['nodes'][i]
                event = dict(kind=n['kind'], start=int_value(r['times'][i]), quantity=int_value(n['quantity']))
                event['site_id' if layer == 'e1' else 'order_id'] = n['site'] if layer == 'e1' else n['order'].id
                trip['events'].append(event)
            result[layer+'_trips'].append(trip)
    if getattr(problem, 'cost_objective', False):
        from models.common.cycle.single_batch_cost import attach_single_batch_cost
        # The physical auditor checks distance independently of the cost MILP.
        result['info']['objective'] = None
        attach_single_batch_cost(problem, result)
    return result
