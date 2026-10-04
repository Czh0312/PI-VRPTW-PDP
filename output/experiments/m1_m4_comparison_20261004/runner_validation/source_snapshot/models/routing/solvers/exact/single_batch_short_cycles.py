"""Static valid two-cycle inequalities for the complete single-batch MILP.

Strict task steps already prohibit every directed cycle at integer solutions,
including zero-time and duplicate-location tasks. Explicit opposite-arc rows
strengthen the fractional relaxation without deleting any feasible route or
assignment. Delivery/pickup task identities remain distinct.
"""


def add_two_cycle_rows(model, internal_arcs, check_budget):
    rows, seen = [], set()
    for (left,right),arc in internal_arcs.items():
        check_budget()
        reverse = (right,left)
        pair = frozenset((left,right))
        if left==right or reverse not in internal_arcs or pair in seen:
            continue
        seen.add(pair)
        rows.append(model.addConstr(arc+internal_arcs[reverse] <= 1,
                                    name=f'B1_two_cycle_{len(rows)}'))
    return rows
