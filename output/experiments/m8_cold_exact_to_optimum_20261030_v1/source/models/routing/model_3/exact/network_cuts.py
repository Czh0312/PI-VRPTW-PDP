"""Input-only rounded capacity/connectivity cuts, valid for every M3 route."""
import math
import time


def required_crossings(nodes, subset, weight_cap, volume_cap):
    required = 1
    for kind in ('d', 'p'):
        for key, capacity in (('w', weight_cap), ('v', volume_cap)):
            total = sum(nodes[i][key] for i in subset if nodes[i]['kind'] == kind)
            if capacity > 0:
                required = max(required, math.ceil(total/capacity-1e-9))
    return required


def separate(nodes, arcs, values, *, weight_cap, volume_cap, distance, known, deadline):
    """Find valid violated subsets; an unfinished separation is harmless.

    Min-cuts separate connectivity even when the positive support is weakly
    connected. Spatial/time prefixes also expose rounded capacity violations.
    This is not a claim of complete rounded-capacity-cut separation.
    """
    if time.perf_counter() >= deadline:
        return []
    count, root = len(nodes), len(nodes)
    capacities = [dict() for _ in range(count+1)]
    for (a, b), value in values.items():
        if value <= 1e-9:
            continue
        left, right = root if a == -1 else a, root if b == -2 else b
        capacities[left][right] = capacities[left].get(right, 0.)+value
        capacities[right].setdefault(left, 0.)
    candidates = set()

    def disconnected_side(start):
        residual = [dict(row) for row in capacities]
        flow = 0.
        while flow < 1.-1e-7:
            parent, queue = {start: None}, [start]
            for at in queue:
                if root in parent:
                    break
                for nxt, capacity in residual[at].items():
                    if capacity > 1e-9 and nxt not in parent:
                        parent[nxt] = at
                        queue.append(nxt)
            if root not in parent:
                return tuple(sorted(parent))
            amount, at = 1.-flow, root
            while parent[at] is not None:
                previous = parent[at]
                amount = min(amount, residual[previous][at])
                at = previous
            at = root
            while parent[at] is not None:
                previous = parent[at]
                residual[previous][at] -= amount
                residual[at][previous] = residual[at].get(previous, 0.)+amount
                at = previous
            flow += amount
        return None

    for i in range(count):
        if time.perf_counter() >= deadline:
            break
        subset = disconnected_side(i)
        if subset is not None and subset not in known:
            candidates.add(subset)

    def best_prefix(ranked):
        chosen, outgoing, best = set(), 0., None
        for i in ranked:
            if time.perf_counter() >= deadline:
                break
            outgoing += sum(capacities[i].values())-sum(
                capacities[i].get(j, 0.)+capacities[j].get(i, 0.) for j in chosen)
            chosen.add(i)
            subset = tuple(sorted(chosen))
            if subset in known:
                continue
            rhs = required_crossings(nodes, chosen, weight_cap, volume_cap)
            violation = rhs-outgoing
            if violation > 1e-7 and (best is None or violation > best[0]):
                best = violation, subset
        if best is not None:
            candidates.add(best[1])

    for i in range(count):
        if time.perf_counter() >= deadline:
            break
        best_prefix(sorted(range(count), key=lambda j: (distance(nodes[i]['site'], nodes[j]['site']), j)))
    for endpoint in (0, 1):
        if time.perf_counter() >= deadline:
            break
        ranked = sorted(range(count), key=lambda j: (nodes[j]['window'][endpoint], j))
        best_prefix(ranked)
        best_prefix(list(reversed(ranked)))
    result = []
    for subset in sorted(candidates):
        chosen = set(subset)
        rhs = required_crossings(nodes, chosen, weight_cap, volume_cap)
        outgoing = sum(value for (a, b), value in values.items() if a in chosen and b not in chosen)
        if outgoing < rhs-1e-7:
            result.append(dict(tasks=list(subset), required=rhs))
    return result


def add_to_main(model, context, site, nodes, arcs, cuts, *, deadline):
    """Transfer only universally valid subset rows to the complete model."""
    if not cuts or not context.get('e2') or 'variables' not in context:
        return 0
    import gurobipy as gp
    variables = context['variables']
    keys = [(node['order'].id, node['kind']) for node in nodes]
    routes = [route for route in context['e2'] if route['satellite'] == site]
    active = {}
    for a, b in arcs:
        shared = variables.aggregate_arcs.get((keys[a], keys[b])) if a >= 0 and b >= 0 else None
        active[a, b] = shared if shared is not None else gp.quicksum(
            route['x'][a, b] for route in routes if (a, b) in route['x'])
    count = 0
    for cut in cuts:
        if time.perf_counter() >= deadline:
            break
        chosen = set(cut['tasks'])
        model.addConstr(gp.quicksum(active[a, b] for a, b in arcs if a in chosen and b not in chosen)
                        >= cut['required'], name=f'e2_{site}_capacity_subset_{count}')
        count += 1
    return count
