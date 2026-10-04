"""Elementary E2 trip pricing for a documented relaxation of fixed M5.

A trip may deliver one batch and pick up any batch, including pickup-only
trips. Physical vehicle sequencing, actual E1 supply and cross-trip use-time
precedence are relaxed. Necessary time bounds and within-trip precedence,
weight/volume profiles and elementary TASK visits are retained. Locations
may repeat; distances and travel times need not satisfy triangle inequalities.
"""
from __future__ import annotations

import math
import time

from models.routing.common.cycle.precheck import travel_lower_bounds


def route_context(problem, satellite, travel_bounds=None):
    p = problem.operating_policy
    travel = travel_bounds or travel_lower_bounds(problem)
    orders = [o for o in problem.orders if o.satellite == satellite]
    release = {b.id: b.start+p.e1_load_time+travel['outward'][problem.depot][satellite]
               +problem.service(satellite)+p.e2_dispatch_prep_time for b in p.batches}
    nodes = []
    for order in orders:
        earliest_d = max(order.delivery_window[0], release[order.batch]
                         +travel['outward'][satellite][order.customer])
        earliest_p = max(order.pickup_window[0], earliest_d+order.service_time+order.min_use_time)
        for kind in ('d', 'p'):
            _, weight, volume = problem.task(order.id, kind)
            window = ((earliest_d, order.delivery_window[1]) if kind == 'd' else
                      (earliest_p, min(order.pickup_window[1], p.batches[-1].cutoff
                       -order.service_time-travel['inward'][satellite][order.customer]-p.e2_return_unload_time)))
            nodes.append(dict(key=(order.id, kind), order=order, weight=weight, volume=volume, window=window))
    return dict(satellite=satellite, release=release, nodes=nodes,
                lookup={n['key']: n for n in nodes},
                maximum_trips=min(len(nodes), problem.fleet.e2_per_sat_count*p.e2_max_trips_per_vehicle))


def evaluate_column(problem, context, sequence):
    """Independently reconstruct an earliest relaxed trip and its raw cost."""
    sequence = tuple(tuple(key) for key in sequence)
    if not sequence or len(set(sequence)) != len(sequence):
        return None
    if any(key not in context['lookup'] for key in sequence):
        return None
    nodes = [context['lookup'][key] for key in sequence]
    batches = {n['order'].batch for n in nodes if n['key'][1] == 'd'}
    if len(batches) > 1:
        return None
    p, f, site = problem.operating_policy, problem.fleet, context['satellite']
    departure = max(p.cycle_start, max((context['release'][b] for b in batches), default=p.cycle_start))
    current, previous, cost = departure, site, 0.0
    weights = [sum(n[resource] for n in nodes if n['key'][1] == 'd') for resource in ('weight', 'volume')]
    caps = (f.e2_weight_cap, f.e2_volume_cap)
    if any(load > cap+1e-6 for load, cap in zip(weights, caps)):
        return None
    delivered, starts = {}, []
    keys = set(sequence)
    for node in nodes:
        order, kind = node['order'], node['key'][1]
        current = max(current+problem.travel(previous, order.customer), node['window'][0])
        if kind == 'p' and (order.id, 'd') in keys:
            if order.id not in delivered:
                return None
            current = max(current, delivered[order.id]+order.service_time+order.min_use_time)
        if current > node['window'][1]:
            return None
        starts.append(int(current))
        if kind == 'd':
            delivered[order.id] = current
        for i, resource in enumerate(('weight', 'volume')):
            weights[i] += (1 if kind == 'p' else -1)*node[resource]
            if weights[i] < -1e-6 or weights[i] > caps[i]+1e-6:
                return None
        cost += problem.distance(previous, order.customer)
        current += order.service_time
        previous = order.customer
    returned = current+problem.travel(previous, site)+p.e2_return_unload_time
    deadline = p.cycle_end-p.e2_turnaround_time
    if any(kind == 'p' for _, kind in sequence):
        deadline = min(deadline, p.batches[-1].cutoff)
    if returned > deadline:
        return None
    cost += problem.distance(previous, site)
    return dict(tasks=sequence, cost=float(cost), departure=int(departure),
                starts=starts, return_complete=int(returned))


class RoutePricer:
    """One reusable integer pricing model per satellite, without trip copies."""

    def __init__(self, problem, context, *, deadline=None, seed=42, verbose=False):
        import gurobipy as gp
        from gurobipy import GRB
        self.problem, self.context = problem, context
        self.model = m = gp.Model('M5_relaxed_E2_trip_pricing')
        m.Params.OutputFlag = int(verbose)
        m.Params.Threads = 1
        m.Params.Seed = int(seed)
        m.Params.MIPGap = m.Params.MIPGapAbs = 0
        m.Params.DualReductions = 0
        nodes, site, p, f = context['nodes'], context['satellite'], problem.operating_policy, problem.fleet
        n, H = len(nodes), p.cycle_end
        def check():
            if deadline is not None and time.perf_counter() >= deadline:
                raise TimeoutError('route pricing build reached its budget')
        try:
            self.visit = y = m.addVars(n, vtype=GRB.BINARY, name='visit')
            times = m.addVars(n, vtype=GRB.INTEGER, ub=H, name='service_start')
            ranks = m.addVars(n, ub=n, name='causal_rank')
            start = m.addVar(lb=p.cycle_start, ub=H, vtype=GRB.INTEGER, name='departure')
            end = m.addVar(lb=p.cycle_start, ub=H, vtype=GRB.INTEGER, name='return_complete')
            m.addConstr(end+p.e2_turnaround_time <= H)
            batches = m.addVars(context['release'], vtype=GRB.BINARY, name='delivery_batch')
            m.addConstr(gp.quicksum(batches.values()) <= 1)
            for bid, active in batches.items():
                ds = [y[i] for i, node in enumerate(nodes) if node['key'][1] == 'd' and node['order'].batch == bid]
                m.addConstr(active <= gp.quicksum(ds))
                m.addConstr((active == 1) >> (start >= context['release'][bid]))
                for selected in ds:
                    m.addConstr(selected <= active)
            by_key = {node['key']: i for i, node in enumerate(nodes)}
            loads = []
            for resource, cap in (('weight', f.e2_weight_cap), ('volume', f.e2_volume_cap)):
                # Relax capacity by the same audit tolerance; never exclude a
                # physically valid input at a floating-point boundary.
                capacity = cap+1e-6
                load = m.addVars(n, ub=capacity, name=resource)
                initial = gp.quicksum(node[resource]*y[i] for i, node in enumerate(nodes) if node['key'][1] == 'd')
                m.addConstr(initial <= capacity)
                loads.append((load, resource, initial))
                for i in range(n):
                    m.addConstr(load[i] <= capacity*y[i])
            for i, node in enumerate(nodes):
                check()
                low, high = node['window']
                m.addConstr(times[i] <= H*y[i])
                m.addConstr((y[i] == 1) >> (times[i] >= low))
                m.addConstr((y[i] == 1) >> (times[i] <= high))
                m.addConstr(ranks[i] >= y[i])
                m.addConstr(ranks[i] <= n*y[i])
                if node['key'][1] == 'p':
                    m.addConstr((y[i] == 1) >> (end <= p.batches[-1].cutoff))
                    j = by_key[node['key'][0], 'd']
                    lag = node['order'].service_time+node['order'].min_use_time
                    m.addConstr(times[i] >= times[j]+lag-(H+lag)*(2-y[i]-y[j]))
                    m.addConstr(ranks[i] >= ranks[j]+1-(n+1)*(2-y[i]-y[j]))
            arcs = []
            for i, left in enumerate(nodes):
                check()
                if p.cycle_start+problem.travel(site, left['order'].customer) <= left['window'][1]:
                    arcs.append((-1, i))
                limit = p.batches[-1].cutoff if left['key'][1] == 'p' else H-p.e2_turnaround_time
                if left['window'][0]+left['order'].service_time+problem.travel(left['order'].customer, site)+p.e2_return_unload_time <= limit:
                    arcs.append((i, -2))
                for j, right in enumerate(nodes):
                    if i == j or (left['key'][1] == right['key'][1] == 'd' and left['order'].batch != right['order'].batch):
                        continue
                    if left['key'][1] == 'p' and right['key'] == (left['key'][0], 'd'):
                        continue
                    if left['window'][0]+left['order'].service_time+problem.travel(left['order'].customer, right['order'].customer) <= right['window'][1]:
                        arcs.append((i, j))
            self.arcs = x = m.addVars(arcs, vtype=GRB.BINARY, name='arc')
            m.addConstr(gp.quicksum(v for (a, b), v in x.items() if a == -1) == 1)
            m.addConstr(gp.quicksum(v for (a, b), v in x.items() if b == -2) == 1)
            for i in range(n):
                m.addConstr(gp.quicksum(v for (a, b), v in x.items() if b == i) == y[i])
                m.addConstr(gp.quicksum(v for (a, b), v in x.items() if a == i) == y[i])
            self.cost = gp.LinExpr()
            for k, ((a, b), active) in enumerate(x.items()):
                if k % 64 == 0:
                    check()
                left = site if a == -1 else nodes[a]['order'].customer
                right = site if b == -2 else nodes[b]['order'].customer
                self.cost += problem.distance(left, right)*active
                if a == -1:
                    m.addConstr((active == 1) >> (times[b] >= start+problem.travel(left, right)))
                    for load, resource, initial in loads:
                        delta = nodes[b][resource]*(1 if nodes[b]['key'][1] == 'p' else -1)
                        m.addConstr((active == 1) >> (load[b] == initial+delta))
                elif b == -2:
                    m.addConstr((active == 1) >> (end >= times[a]+nodes[a]['order'].service_time+problem.travel(left, right)+p.e2_return_unload_time))
                else:
                    m.addConstr((active == 1) >> (times[b] >= times[a]+nodes[a]['order'].service_time+problem.travel(left, right)))
                    m.addConstr(ranks[b] >= ranks[a]+1-(n+1)*(1-active))
                    for load, resource, initial in loads:
                        delta = nodes[b][resource]*(1 if nodes[b]['key'][1] == 'p' else -1)
                        m.addConstr((active == 1) >> (load[b] == load[a]+delta))
            m.update()
            check()
        except Exception:
            m.dispose()
            raise

    def price(self, duals, fleet_dual=0.0, *, time_limit=2, phase_one=False, stop_at_negative=False):
        import gurobipy as gp
        from gurobipy import GRB
        m, nodes = self.model, self.context['nodes']
        fleet_dual = min(0.0, float(fleet_dual))
        m.setObjective((0 if phase_one else self.cost)
                       -gp.quicksum(float(duals.get(node['key'], 0))*self.visit[i] for i, node in enumerate(nodes))-fleet_dual)
        m.Params.TimeLimit = max(0.0, time_limit)
        def negative(model, where):
            if where == GRB.Callback.MIPSOL and model.cbGet(GRB.Callback.MIPSOL_OBJ) < -1e-6:
                model.terminate()
        m.optimize(negative if stop_at_negative else None)
        status = m.Status
        analytic = -math.fsum(max(0.0, float(duals.get(node['key'], 0))) for node in nodes)-fleet_dual
        try:
            raw = m.ObjBound if status != GRB.INFEASIBLE else None
        except (gp.GurobiError, AttributeError):
            raw = None
        margin = 1e-7*max(1.0, math.fsum(abs(float(duals.get(node['key'], 0))) for node in nodes), abs(fleet_dual))
        lower = max(analytic, raw-margin) if raw is not None and math.isfinite(raw) else analytic
        column, reduced = None, None
        if m.SolCount:
            selected = {a: b for (a, b), var in self.arcs.items() if var.X > .5}
            indices, seen, current = [], set(), selected.get(-1)
            while current is not None and current != -2 and current not in seen:
                seen.add(current)
                indices.append(current)
                current = selected.get(current)
            if current != -2 or len(indices) != sum(var.X > .5 for var in self.visit.values()):
                raise RuntimeError('Pricing solution is not a single elementary trip')
            column = evaluate_column(self.problem, self.context, [nodes[i]['key'] for i in indices])
            if column is None:
                raise RuntimeError('Pricing column failed independent relaxed trip replay')
            reduced = (0.0 if phase_one else column['cost'])-math.fsum(float(duals.get(key, 0)) for key in column['tasks'])-fleet_dual
            if not math.isclose(reduced, m.ObjVal, rel_tol=1e-7, abs_tol=1e-5):
                raise RuntimeError('Pricing objective differs from raw route reduced cost')
        return dict(status=int(status), optimal=status == GRB.OPTIMAL, infeasible=status == GRB.INFEASIBLE,
                    lower_bound=float(lower), raw_lower_bound=float(raw) if raw is not None and math.isfinite(raw) else None,
                    margin=margin, reduced_cost=reduced, column=column, runtime=m.Runtime,
                    work=m.Work, variables=m.NumVars, constraints=m.NumConstrs)

    def dispose(self):
        self.model.dispose()
