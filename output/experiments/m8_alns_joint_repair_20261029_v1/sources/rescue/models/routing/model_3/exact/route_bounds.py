"""Size-independent E2 relaxations and fresh route candidates for fixed M3."""
from __future__ import annotations

import math
import time


def site_nodes(problem, parameters, site):
    return [dict(site=o.customer, order=o, kind=kind, quantity=o.count,
                 w=problem.task(o.id, kind)[1], v=problem.task(o.id, kind)[2],
                 service=o.service_time, window=parameters.task_windows[o.id, kind])
            for o in problem.orders if o.satellite == site for kind in problem.task_kinds]


def minimum_trip_count(problem, site):
    """Necessary number of nonempty trips, from original homogeneous capacity."""
    orders = [order for order in problem.orders if order.satellite == site]
    if not orders:
        return 0
    minimum = 1
    for kind in problem.task_kinds:
        for offset, capacity in ((1, problem.fleet.e2_weight_cap), (2, problem.fleet.e2_volume_cap)):
            total = sum(problem.task(order.id, kind)[offset] for order in orders)
            if capacity > 0:
                minimum = max(minimum, math.ceil(total/capacity-1e-9))
    return minimum


def assignment_distance_bound(problem, parameters, site, *, deadline, threads=1, seed=42):
    """Polynomial arc-cover LP; incomplete LPs never supply a primal bound.

    Every physical trip projects to the arc cover. Degree equations, legal
    time-window arcs and a necessary number of departures/returns are kept;
    route connectivity, load propagation and physical schedules are relaxed.
    """
    from models.routing.model_3.exact.preprocessing.model_bounds import route_arcs
    nodes = site_nodes(problem, parameters, site)
    if not nodes:
        return 0., dict(kind='empty_site')
    if time.perf_counter() >= deadline:
        return 0., dict(kind='nonnegative_distance', reason='budget_exhausted')

    def check():
        if time.perf_counter() >= deadline:
            raise TimeoutError('Arc-cover preparation budget exhausted')

    try:
        arcs = route_arcs(problem, nodes, site, 'e2', check_budget=check)
    except TimeoutError:
        return 0., dict(kind='nonnegative_distance', reason='arc_preparation_timeout')
    costs = {(a, b): problem.distance(site if a == -1 else nodes[a]['site'],
                                    site if b == -2 else nodes[b]['site']) for a, b in arcs}
    incoming = {i: [] for i in range(len(nodes))}
    outgoing = {i: [] for i in range(len(nodes))}
    for edge in arcs:
        if edge[1] >= 0:
            incoming[edge[1]].append(edge)
        if edge[0] >= 0:
            outgoing[edge[0]].append(edge)
    analytic = max(sum(min((costs[e] for e in edges), default=0.) for edges in incoming.values()),
                   sum(min((costs[e] for e in edges), default=0.) for edges in outgoing.values()))
    info = dict(kind='degree_minima', analytic_lower_bound=analytic, arcs=len(arcs))
    if time.perf_counter() >= deadline:
        return analytic, info
    import gurobipy as gp
    model = gp.Model('m3_site_arc_relaxation')
    try:
        model.Params.OutputFlag = 0
        model.Params.Threads = threads
        model.Params.Seed = seed
        x = model.addVars(arcs, ub=1., name='arc')
        for i in range(len(nodes)):
            model.addConstr(gp.quicksum(x[e] for e in incoming[i]) == 1)
            model.addConstr(gp.quicksum(x[e] for e in outgoing[i]) == 1)
        minimum_trips = minimum_trip_count(problem, site)
        model.addConstr(gp.quicksum(x[a, b] for a, b in arcs if a == -1) >= minimum_trips)
        model.addConstr(gp.quicksum(x[a, b] for a, b in arcs if b == -2) >= minimum_trips)
        model.setObjective(gp.quicksum(costs[e]*x[e] for e in arcs))
        model.Params.TimeLimit = max(0., deadline-time.perf_counter())
        model.optimize()
        info.update(native_status=model.Status, optimize_seconds=model.Runtime, work=model.Work,
                    minimum_trips=minimum_trips)
        if model.Status == gp.GRB.OPTIMAL:
            value = max(analytic, float(model.ObjVal))
            info.update(kind='arc_cover_LP', lower_bound=value)
            return value, info
        return analytic, info
    finally:
        model.dispose()


def seed_columns(problem, parameters, site, *, deadline):
    """Greedy feasible single-trip sequences, used only as candidate columns.

    Their distances are NOT exact task-set minima. Only a subsequent complete
    enumeration may promote the library to a lower-bound formulation.
    """
    orders = list(problem.orders)
    n = len(orders)
    local = [i for i, o in enumerate(orders) if o.satellite == site]
    policy, fleet, windows = problem.operating_policy, problem.fleet, parameters.task_windows
    columns = {}

    def evaluate(sequence):
        chosen = {bit for bit in sequence if bit < n}
        weight = sum(problem.task(orders[i].id, 'd')[1] for i in chosen)
        volume = sum(problem.task(orders[i].id, 'd')[2] for i in chosen)
        if weight > fleet.e2_weight_cap+1e-8 or volume > fleet.e2_volume_cap+1e-8:
            return None
        clock = policy.cycle_start
        if chosen:
            clock = max(clock, parameters.bounds['supply'][site][0]+policy.e2_dispatch_prep_time)
        at, distance, delivered = site, 0., {}
        for bit in sequence:
            i, kind = bit % n, 'd' if bit < n else 'p'
            o = orders[i]
            lo, hi = windows[o.id, kind]
            if kind == 'p':
                if i in chosen and i not in delivered:
                    return None
                lo = max(lo, delivered.get(i, windows[o.id, 'd'][0])+o.service_time+o.min_use_time)
            arrive = max(clock+problem.travel(at, o.customer), lo)
            if arrive > hi:
                return None
            if kind == 'd':
                delivered[i] = arrive
            _, dw, dv = problem.task(o.id, kind)
            weight += (-1 if kind == 'd' else 1)*dw
            volume += (-1 if kind == 'd' else 1)*dv
            if not (-1e-8 <= weight <= fleet.e2_weight_cap+1e-8 and -1e-8 <= volume <= fleet.e2_volume_cap+1e-8):
                return None
            distance += problem.distance(at, o.customer)
            clock, at = arrive+o.service_time, o.customer
        end = clock+problem.travel(at, site)+policy.e2_return_unload_time
        if end+policy.e2_turnaround_time > policy.cycle_end or (any(bit >= n for bit in sequence) and end > policy.batches[0].cutoff):
            return None
        return dict(mask=sum(1 << bit for bit in sequence), distance=distance+problem.distance(at, site),
                    satellite=site, sequence=tuple(sequence))

    def keep(row):
        if row is not None and (row['mask'] not in columns or row['distance'] < columns[row['mask']]['distance']):
            columns[row['mask']] = row

    groups = []
    for i in local:
        if time.perf_counter() >= deadline:
            return list(columns.values()), [], dict(complete_candidate=False, reason='seed_budget_exhausted')
        for seq in ((i,), (n+i,)):
            keep(evaluate(seq))
        pair = evaluate((i, n+i))
        if pair is not None:
            keep(pair)
            groups.append(pair)
        else:
            singles = [columns.get(1 << bit) for bit in (i, n+i)]
            if any(row is None for row in singles):
                return list(columns.values()), [], dict(complete_candidate=False, reason='no_single_order_partition')
            groups.extend(singles)
    initial = list(groups)
    best_groups = list(groups)
    # Two deterministic insertion sweeps avoid the repeated all-pairs merge
    # search. Their work grows with actual groups/tasks, without a size switch.
    for endpoint in (0, 1):
        ranked = sorted(initial, key=lambda row: min(
            windows[orders[bit % n].id, 'd' if bit < n else 'p'][endpoint] for bit in row['sequence']))
        assembled = []
        for position, right in enumerate(ranked):
            if time.perf_counter() >= deadline:
                assembled.extend(ranked[position:])
                break
            best = None
            for i, left in enumerate(assembled):
                joined = left['sequence']+right['sequence']
                sequences = (joined, right['sequence']+left['sequence'],
                    tuple(sorted(joined, key=lambda bit: (windows[orders[bit % n].id, 'd' if bit < n else 'p'][0], bit))),
                    tuple(sorted(joined, key=lambda bit: (windows[orders[bit % n].id, 'd' if bit < n else 'p'][1], bit))))
                for sequence in sequences:
                    row = evaluate(sequence)
                    keep(row)
                    if row is None:
                        continue
                    saving = left['distance']+right['distance']-row['distance']
                    if best is None or saving > best[0]:
                        best = saving, i, row
            if best is None:
                assembled.append(right)
            else:
                _, i, row = best
                assembled[i] = row
        if (len(assembled), sum(row['distance'] for row in assembled)) < (len(best_groups), sum(row['distance'] for row in best_groups)):
            best_groups = assembled
    return list(columns.values()), [row['mask'] for row in best_groups], dict(complete_candidate=True, groups=len(best_groups))
