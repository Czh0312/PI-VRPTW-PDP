"""Conservative distance bounds on mandatory visits, including nonmetric arcs."""
import math
import heapq


def _visit_bound(problem, base, tasks, delivery_first=False):
    if not tasks:
        return 0.0
    incoming = outgoing = 0.0
    for i, (site, kind) in enumerate(tasks):
        incoming += min([problem.distance(base, site)]+[
            problem.distance(other, site) for j, (other, previous) in enumerate(tasks)
            if j != i and not (delivery_first and previous == 'p' and kind == 'd')])
        outgoing += min([problem.distance(site, base)]+[
            problem.distance(site, other) for j, (other, following) in enumerate(tasks)
            if j != i and not (delivery_first and kind == 'p' and following == 'd')])
    # At least one trip must leave and return to this base. Allow any task as
    # predecessor/successor: this relaxation cannot exclude a feasible route.
    incoming += min(problem.distance(s, base) for s, _ in tasks)
    outgoing += min(problem.distance(base, s) for s, _ in tasks)
    return max(incoming, outgoing)


def distance_lower_bound(problem):
    if len(problem.operating_policy.batches) != 1:
        return 0.0
    active = {o.satellite for o in problem.orders}
    bound = _visit_bound(problem, problem.depot,
                         [(s, k) for s in sorted(active) for k in problem.task_kinds], True)
    for site in problem.satellites:
        bound += _visit_bound(problem, site, [(o.customer, k) for o in problem.orders
                                             if o.satellite == site for k in problem.task_kinds])
    # Round conservatively before using the bound to prune another partition.
    return max(0.0, bound-1e-7*max(1.0, bound)) if math.isfinite(bound) else 0.0


def free_assignment_lower_bound(problem, domains):
    """Bound every remaining assignment, not only an evaluated partition.

    Task in/out arcs may choose any compatible satellite or task predecessor.
    E1 must reach the satellite of each customer and return; relaxed shortest
    distances use only E1-eligible locations and may ignore the phase order.
    These are lower bounds on separate echelons and can safely be added.
    """
    if not problem.orders or any(not sites for sites in domains.values()):
        return 0.0
    tasks = [(o, k) for o in problem.orders for k in problem.task_kinds]
    incoming = outgoing = 0.0
    for i, (order, kind) in enumerate(tasks):
        sites = domains[order.customer]
        incoming += min([problem.distance(s, order.customer) for s in sites]+[
            problem.distance(other.customer, order.customer) for j, (other, previous) in enumerate(tasks)
            if i != j and set(sites).intersection(domains[other.customer])
            and not (other.id == order.id and previous == 'p' and kind == 'd')])
        outgoing += min([problem.distance(order.customer, s) for s in sites]+[
            problem.distance(order.customer, other.customer) for j, (other, following) in enumerate(tasks)
            if i != j and set(sites).intersection(domains[other.customer])
            and not (other.id == order.id and kind == 'p' and following == 'd')])
    incoming += min(problem.distance(o.customer, s) for o in problem.orders for s in domains[o.customer])
    outgoing += min(problem.distance(s, o.customer) for o in problem.orders for s in domains[o.customer])

    def shortest(reverse=False):
        distances = {node: math.inf for node in (problem.depot,)+problem.satellites}
        distances[problem.depot] = 0
        queue = [(0.0, problem.depot)]
        while queue:
            value, node = heapq.heappop(queue)
            if value != distances[node]:
                continue
            for other in distances:
                edge = problem.distance(other, node) if reverse else problem.distance(node, other)
                candidate = value+edge
                if candidate < distances[other]:
                    distances[other] = candidate
                    heapq.heappush(queue, (candidate, other))
        return distances
    outward, inward = shortest(), shortest(True)
    e1 = max(min(outward[s]+inward[s] for s in sites) for sites in domains.values())
    value = e1+max(incoming, outgoing)
    return max(0.0, value-1e-7*max(1.0, value)) if math.isfinite(value) else 0.0
