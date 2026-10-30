"""Exact grouped encoding of the shared master's trip/recovery contacts.

Nonempty task-set groups and representative-pair groups overlap at least
one task, so exact cover makes their selected-column sum at most one.
Replacing per-column links by a single group link preserves every binary
solution and can tighten the LP relaxation. No K or route mask is removed.
"""


def augment(problem, model, ctx, library, *, aggregate=True):
    import gurobipy as gp
    orders = list(problem.orders)
    n = len(orders)
    B = max(ctx['selectors'])
    pickup_columns, pairs = {}, {}
    for c, row in enumerate(library):
        delivery = row['deliveries']
        pickup = row['mask'] >> n
        if pickup.bit_count() > 1:
            pickup_columns.setdefault(pickup, []).append(c)
        if delivery and pickup:
            i = (delivery & -delivery).bit_length()-1
            j = (pickup & -pickup).bit_length()-1
            pairs.setdefault((i, j), []).append(c)
    same = model.addVars(sorted(pickup_columns), range(B), lb=0, ub=1, name='sync_pickup_batch')
    for group in sorted(pickup_columns):
        for b in range(B):
            mask = group
            while mask:
                bit = mask & -mask
                i = bit.bit_length()-1
                model.addConstr(same[group, b] <= ctx['recovery'][i, b])
                mask ^= bit
        if aggregate:
            model.addConstr(gp.quicksum(ctx['route'][c] for c in pickup_columns[group]) <= same.sum(group, '*'))
        else:
            for c in pickup_columns[group]:
                model.addConstr(ctx['route'][c] <= same.sum(group, '*'))
    rows = 0
    for (i, j), columns in pairs.items():
        terms = [gp.quicksum(ctx['route'][c] for c in columns)] if aggregate else [ctx['route'][c] for c in columns]
        for term in terms:
            for b in range(1, B):
                model.addConstr(term+gp.quicksum(ctx['assignments'][i, h] for h in range(b, B))
                    +gp.quicksum(ctx['recovery'][j, h] for h in range(b)) <= 2)
                rows += 1
    model.update()
    return dict(pickup_groups=len(pickup_columns), representative_pairs=len(pairs),
                conditional_precedence_rows=rows, aggregated=aggregate,
                proof='Columns within each nonempty pickup-set or representative-pair group share a task; '
                      'exact coverage permits at most one. Group links preserve the binary feasible set.')
