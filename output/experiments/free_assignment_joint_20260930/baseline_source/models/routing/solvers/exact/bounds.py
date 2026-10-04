"""Conservative distance bounds on mandatory visits, including nonmetric arcs."""
import math


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
