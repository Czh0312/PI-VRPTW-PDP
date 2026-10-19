"""Necessary subset-row inequalities on the complete timed route domain.

For three distinct tasks, at most one selected physical trip can contain two
or more of them: two such trips would repeat a task. Hence the column sum
over those routes is <=1. This remains valid under supply compression, and
cuts fractional route covers without adding route binary variables.
"""
from itertools import combinations
import math


def separate_task_triangles(tasks, weighted_sets, *, limit=16, tolerance=1e-7):
    known = tuple(sorted(tuple(key) for key in tasks))
    if len(set(known)) != len(known):
        raise ValueError('Duplicate known task')
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError('Positive integer triangle limit required')
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError('Nonnegative finite tolerance required')
    rows = []
    for keys, weight in weighted_sets:
        keys = frozenset(tuple(key) for key in keys)
        if not keys <= set(known) or not math.isfinite(weight) or weight < 0:
            raise ValueError('Known task sets and nonnegative finite weights required')
        rows.append((keys, weight))
    violated = []
    for triangle in combinations(known, 3):
        subset = frozenset(triangle)
        lhs = math.fsum(weight for keys, weight in rows if len(keys & subset) >= 2)
        if lhs > 1+tolerance:
            violated.append(dict(tasks=triangle, lhs=lhs, violation=lhs-1))
    return sorted(violated, key=lambda row: (-row['violation'], row['tasks']))[:limit]


def add_triangle_rows(problem, model, context, site, triangles, *, name='subset_row'):
    import gurobipy as gp
    if site not in context['routes']:
        raise ValueError('Unknown triangle satellite')
    known = {(o.id, kind) for o in problem.orders if o.satellite==site for kind in ('d', 'p')}
    normalized = []
    for triangle in triangles:
        keys = tuple(sorted(tuple(key) for key in triangle))
        if len(keys)!=3 or len(set(keys))!=3 or not set(keys)<=known:
            raise ValueError('Three distinct known site tasks required')
        if keys in normalized:
            raise ValueError('Duplicate triangle')
        normalized.append(keys)
    data = context['routes'][site]
    task_sets = [frozenset(row['keys']) for row in data['rows']]
    records = []
    for i, triangle in enumerate(normalized):
        subset = frozenset(triangle)
        matching = {j for j, keys in enumerate(task_sets) if len(keys & subset)>=2}
        expression = gp.quicksum(var for (j, _), var in data['choices'].items() if j in matching)
        row = model.addConstr(expression<=1, name=f'{name}_{i}')
        records.append(dict(site=site, tasks=triangle, row=row,
                            scope='necessary_complete_M5_task_triangle_subset_row',
                            proven_optimal=False, proven_infeasible=False))
    model.update()
    return records
