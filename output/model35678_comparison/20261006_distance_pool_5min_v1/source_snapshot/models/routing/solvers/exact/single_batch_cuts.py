"""Bounded separation of rounded E2 capacity cuts for B=1.

For any task subset U, a route's deliveries in U fit its departure load;
its pickups in U fit its return load. Thus at least max(ceil(Dw/Qw),
ceil(Dv/Qv), ceil(Pw/Qw), ceil(Pv/Qv), 1) routes intersect U. Each such
route has at least one entry into U, so its internal arcs total at most
|U|-required_routes. Delivery and pickup amounts must be rounded separately:
a route may deliver, then collect into the newly freed space.
"""
from itertools import combinations
import math


def minimum_routes(problem, tasks):
    tasks = tuple(tasks)
    if not tasks:
        return 0
    bound = 1
    for kind in problem.task_kinds:
        for resource, cap in ((1, problem.fleet.e2_weight_cap),
                              (2, problem.fleet.e2_volume_cap)):
            amount = math.fsum(problem.task(*key)[resource] for key in tasks if key[1] == kind)
            if amount > 0:
                # Use the auditor's absolute capacity tolerance conservatively.
                relaxed = cap+max(1e-6, abs(cap)*1e-12)
                bound = max(bound, math.ceil(amount/relaxed))
    return bound


class CapacityCutSeparator:
    """Separate at root only; avoid Python scans throughout the search tree."""

    def __init__(self, variables):
        self.variables = variables
        self.candidates = []
        self.added = set()
        self.error = None

    def __call__(self, model, where):
        import gurobipy as gp
        from gurobipy import GRB
        if where != GRB.Callback.MIPNODE or self.error:
            return
        try:
            if (model.cbGet(GRB.Callback.MIPNODE_STATUS) != GRB.OPTIMAL or
                    model.cbGet(GRB.Callback.MIPNODE_NODCNT) > 0):
                return
            values = model.cbGetNodeRel(self.variables)
            violated = []
            for index, (terms, rhs) in enumerate(self.candidates):
                if index not in self.added:
                    excess = math.fsum(values[i] for i in terms)-rhs
                    if excess > 1e-6:
                        violated.append((excess, index))
            for _, index in sorted(violated, reverse=True)[:20]:
                terms, rhs = self.candidates[index]
                model.cbCut(gp.quicksum(self.variables[i] for i in terms) <= rhs)
                self.added.add(index)
        except Exception as exc:
            # Callback failures cannot silently produce a proof of optimality.
            self.error = f'{type(exc).__name__}: {exc}'
            model.terminate()


def build_capacity_separator(problem, internal_arcs, check_budget, *, families='all'):
    if len(problem.operating_policy.batches) != 1:
        raise ValueError('Single-batch cuts require B=1')
    if families not in ('all', 'tasks', 'pairs'):
        raise ValueError('Unknown capacity cut family')
    separator = CapacityCutSeparator(list(internal_arcs.values()))
    indexed = [(a, b, i) for i, (a, b) in enumerate(internal_arcs)]
    groups = [problem.orders]
    if not problem.free_assignment:
        groups.extend([o for o in problem.orders if o.satellite == site] for site in problem.satellites)
    seen = set()
    for orders in groups:
        orders = sorted(orders, key=lambda o: o.id)
        subsets = [orders]
        if len(orders) <= 12:
            subsets.extend(combinations(orders, 2))
        for order in orders:
            check_budget()
            nearby = sorted(orders, key=lambda o: (
                o.id != order.id,
                problem.distance(order.customer, o.customer)+problem.distance(o.customer, order.customer),
                o.id))
            subsets.extend(nearby[:k] for k in (4, 8, 16) if k < len(orders))
        # Consecutive deadline groups catch demand concentrated in time.
        for kind in problem.task_kinds:
            timed = sorted(orders, key=lambda o: ((o.delivery_window if kind == 'd' else o.pickup_window)[1], o.id))
            for size in (4, 8, 16):
                if size < len(timed):
                    subsets.extend(timed[i:i+size] for i in range(0, len(timed)-size+1, max(1, size//2)))
        kinds = [(kind,) for kind in problem.task_kinds] if families != 'pairs' else []
        if problem.reusable and families != 'tasks':
            kinds.append(problem.task_kinds)
        for selected in subsets:
            for group in kinds:
                check_budget()
                tasks = frozenset((o.id, kind) for o in selected for kind in group)
                if len(tasks) < 2 or tasks in seen:
                    continue
                seen.add(tasks)
                rhs = len(tasks)-minimum_routes(problem, tasks)
                terms = [i for a, b, i in indexed if a in tasks and b in tasks]
                if len(terms) > rhs:
                    separator.candidates.append((terms, rhs))
    return separator
