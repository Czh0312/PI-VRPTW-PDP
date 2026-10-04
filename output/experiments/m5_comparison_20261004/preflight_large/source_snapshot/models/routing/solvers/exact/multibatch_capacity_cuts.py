"""Rounded delivery-capacity rows; restricted LPs only select candidate rows."""
from itertools import combinations
import math
import time


def capacity_floor(problem, nodes):
    """A full trip carries all its delivery loads simultaneously at departure."""
    return max(1, *(math.ceil(math.fsum(n[resource] for n in nodes)/(cap+1e-6))
        for resource, cap in [('weight', problem.fleet.e2_weight_cap),
                              ('volume', problem.fleet.e2_volume_cap)]))


def capacity_subsets(problem, context, subsets):
    keys, required = {}, {}
    for sid, subset in enumerate(subsets or []):
        tasks = frozenset(tuple(k) for k in subset)
        if not tasks or any(k not in context['lookup'] or k[1] != 'd' for k in tasks):
            raise ValueError('Capacity subsets must contain known delivery tasks')
        keys[sid] = tasks
        required[sid] = capacity_floor(problem, [context['lookup'][k] for k in tasks])
    return keys, required


def select_capacity_subsets(problem, context, columns, *, deadline, max_per_batch=6,
                            max_batch_tasks=16):
    """Select violated, diverse rows; NEVER expose the restricted objective as LB.

    Selection is bounded even when a large batch has exponentially many subsets.
    Every emitted row derives its RHS from original capacities, independent of
    the selected columns and their restricted objective.
    """
    import gurobipy as gp
    if max_per_batch < 1 or time.perf_counter() >= deadline or not columns:
        return []
    model = gp.Model('M5_capacity_row_selection_only')
    model.Params.OutputFlag = 0
    model.Params.Threads = 1
    try:
        sets = [frozenset(tuple(k) for k in c['tasks']) for c in columns]
        x = model.addVars(len(columns), obj={i: c['cost'] for i, c in enumerate(columns)})
        for node in context['nodes']:
            model.addConstr(gp.quicksum(x[i] for i, keys in enumerate(sets) if node['key'] in keys) == 1)
        model.addConstr(x.sum() <= context['maximum_trips'])
        batches = {}
        for bid in context['release']:
            nodes = [n for n in context['nodes'] if n['key'][1] == 'd' and n['order'].batch == bid]
            if nodes:
                keys = frozenset(n['key'] for n in nodes)
                batches[bid] = keys
                model.addConstr(gp.quicksum(x[i] for i, tasks in enumerate(sets) if tasks & keys)
                                >= capacity_floor(problem, nodes))
        model.Params.TimeLimit = max(0, deadline-time.perf_counter())
        model.optimize()
        if model.Status != gp.GRB.OPTIMAL:
            return []
        weights = [x[i].X for i in range(len(columns))]
        chosen = []
        for keys in batches.values():
            if len(keys) > max_batch_tasks:
                continue
            candidates = []
            ordered = sorted(keys)
            for size in range(2, len(keys)):
                for subset in combinations(ordered, size):
                    if time.perf_counter() >= deadline:
                        return chosen
                    rhs = capacity_floor(problem, [context['lookup'][k] for k in subset])
                    if rhs < 2:
                        continue
                    tasks = frozenset(subset)
                    lhs = math.fsum(w for w, column in zip(weights, sets) if column & tasks)
                    if rhs-lhs > 1e-5:
                        candidates.append((rhs-lhs, subset))
            kept = []
            for _, subset in sorted(candidates, key=lambda row: (-row[0], len(row[1]), row[1])):
                tasks = frozenset(subset)
                if any(len(tasks & old) >= min(len(tasks), len(old))-1 for old in kept):
                    continue
                chosen.append(subset)
                kept.append(tasks)
                if len(kept) >= max_per_batch:
                    break
        return chosen
    finally:
        model.dispose()
