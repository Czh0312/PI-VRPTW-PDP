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


def insertion_columns(problem, context, duals, known_columns, *, phase_one=False, deadline=None):
    """Find useful columns cheaply; absence is never a pricing certificate."""
    def score(column):
        return (0.0 if phase_one else column['cost'])-math.fsum(duals.get(key, 0.0) for key in column['tasks'])
    seeds = sorted(known_columns, key=lambda c: (score(c), c['cost'], c['tasks']))[:3]
    singletons = [column for node in context['nodes']
                  for column in [evaluate_column(problem, context, [node['key']])] if column is not None]
    seeds += sorted(singletons, key=lambda c: (score(c), c['cost'], c['tasks']))[:5]
    candidates = {}
    tasks = [node['key'] for node in context['nodes']]
    for seed in seeds:
        if deadline is not None and time.perf_counter() >= deadline:
            break
        current = seed
        while len(current['tasks']) < len(tasks):
            best, current_score = None, score(current)
            for key in tasks:
                if key in current['tasks']:
                    continue
                for position in range(len(current['tasks'])+1):
                    if deadline is not None and time.perf_counter() >= deadline:
                        return list(candidates.values())
                    sequence = current['tasks'][:position]+(key,)+current['tasks'][position:]
                    proposed = evaluate_column(problem, context, sequence)
                    if proposed is None or score(proposed) >= current_score-1e-6:
                        continue
                    if best is None or (score(proposed), proposed['cost'], proposed['tasks']) < (score(best), best['cost'], best['tasks']):
                        best = proposed
            if best is None:
                break
            current = best
            if score(current) < -1e-6:
                candidates[current['tasks']] = current
    return sorted(candidates.values(), key=lambda c: (score(c), c['cost'], c['tasks']))[:8]


class TripPricingModel:
    """One reusable integer pricing model per satellite, without trip copies."""

    def __init__(self, problem, context, *, deadline=None, seed=42, verbose=False, linear_links=True, require_batch=None, subset_keys=None):
        import gurobipy as gp
        from gurobipy import GRB
        self.problem, self.context = problem, context
        self.model = m = gp.Model('M5_relaxed_E2_trip_pricing')
        m.Params.OutputFlag = int(verbose)
        m.Params.Threads = 1
        m.Params.Seed = int(seed)
        m.Params.MIPGap = m.Params.MIPGapAbs = 0
        m.Params.DualReductions = 0
        # Near-zero reduced costs amplify small fractional flow residuals.
        # Keep the integer route and its raw-distance replay consistent;
        # the independent objective check below must remain strict.
        m.Params.IntFeasTol = 1e-9
        m.Params.FeasibilityTol = 1e-9
        m.Params.IntegralityFocus = 1
        nodes, site, p, f = context['nodes'], context['satellite'], problem.operating_policy, problem.fleet
        n, H = len(nodes), p.cycle_end
        def check():
            if deadline is not None and time.perf_counter() >= deadline:
                raise TimeoutError('route pricing build reached its budget')
        try:
            self.visit = y = m.addVars(n, vtype=GRB.BINARY, name='visit')
            self.subset_keys = {sid: frozenset(keys) for sid, keys in (subset_keys or {}).items()}
            self.subset_flags = {}
            for sid, keys in self.subset_keys.items():
                selected = [y[i] for i, node in enumerate(nodes) if node['key'] in keys]
                if selected:
                    flag = m.addVar(vtype=GRB.BINARY, name='hits_subset_'+str(sid))
                    m.addConstr(flag <= gp.quicksum(selected))
                    for variable in selected:m.addConstr(variable <= flag)
                    self.subset_flags[sid] = flag
            valid = [node['window'][0] <= node['window'][1] for node in nodes]
            times = {i: m.addVar(vtype=GRB.INTEGER,
                lb=node['window'][0] if linear_links and valid[i] else 0,
                ub=node['window'][1] if linear_links and valid[i] else H, name=f'service_start_{i}')
                for i, node in enumerate(nodes)}
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
            if require_batch is not None:
                m.addConstr(batches[require_batch] == 1)
            by_key = {node['key']: i for i, node in enumerate(nodes)}
            loads = []
            for resource, cap in (('weight', f.e2_weight_cap), ('volume', f.e2_volume_cap)):
                # Relax capacity by the same audit tolerance; never exclude a
                # physically valid input at a floating-point boundary.
                capacity = cap+1e-6
                load = m.addVars(n, ub=capacity, name=resource)
                initial = m.addVar(ub=capacity, name='initial_'+resource)
                m.addConstr(initial == gp.quicksum(node[resource]*y[i] for i, node in enumerate(nodes) if node['key'][1] == 'd'))
                m.addConstr(gp.quicksum(node[resource]*y[i] for i, node in enumerate(nodes) if node['key'][1] == 'p') <= capacity)
                loads.append((load, resource, initial, capacity))
                for i, node in enumerate(nodes):
                    m.addConstr(load[i] <= capacity*y[i])
                    if node['key'][1] == 'd':
                        m.addConstr(load[i] <= max(0.0, capacity-node[resource])*y[i])
                    else:
                        m.addConstr(load[i] >= node[resource]*y[i])
            for i, node in enumerate(nodes):
                check()
                low, high = node['window']
                if not valid[i]:
                    y[i].UB = 0
                if not linear_links:
                    m.addConstr(times[i] <= H*y[i])
                    m.addConstr((y[i] == 1) >> (times[i] >= low))
                    m.addConstr((y[i] == 1) >> (times[i] <= high))
                m.addConstr(ranks[i] >= y[i])
                m.addConstr(ranks[i] <= n*y[i])
                if node['key'][1] == 'p':
                    m.addConstr((y[i] == 1) >> (end <= p.batches[-1].cutoff))
                    j = by_key.get((node['key'][0], 'd'))
                    if j is not None:
                        lag = node['order'].service_time+node['order'].min_use_time
                        m.addConstr(times[i] >= times[j]+lag-(H+lag)*(2-y[i]-y[j]))
                        m.addConstr(ranks[i] >= ranks[j]+1-(n+1)*(2-y[i]-y[j]))
            arcs = []
            for i, left in enumerate(nodes):
                check()
                if not valid[i]:
                    continue
                if p.cycle_start+problem.travel(site, left['order'].customer) <= left['window'][1]:
                    arcs.append((-1, i))
                limit = p.batches[-1].cutoff if left['key'][1] == 'p' else H-p.e2_turnaround_time
                if left['window'][0]+left['order'].service_time+problem.travel(left['order'].customer, site)+p.e2_return_unload_time <= limit:
                    arcs.append((i, -2))
                for j, right in enumerate(nodes):
                    if not valid[j] or i == j or (left['key'][1] == right['key'][1] == 'd' and left['order'].batch != right['order'].batch):
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
            def propagate_load(active, before, after, delta, capacity):
                if linear_links:
                    difference = after-before-delta
                    m.addConstr(difference <= max(0.0, capacity-delta)*(1-active))
                    m.addConstr(difference >= -max(0.0, capacity+delta)*(1-active))
                else:
                    m.addConstr((active == 1) >> (after == before+delta))
            for k, ((a, b), active) in enumerate(x.items()):
                if k % 64 == 0:
                    check()
                left = site if a == -1 else nodes[a]['order'].customer
                right = site if b == -2 else nodes[b]['order'].customer
                self.cost += problem.distance(left, right)*active
                if a == -1:
                    travel = problem.travel(left, right)
                    if linear_links:
                        m.addConstr(times[b] >= start+travel-max(0, H+travel-nodes[b]['window'][0])*(1-active))
                    else:
                        m.addConstr((active == 1) >> (times[b] >= start+travel))
                    for load, resource, initial, capacity in loads:
                        delta = nodes[b][resource]*(1 if nodes[b]['key'][1] == 'p' else -1)
                        propagate_load(active, initial, load[b], delta, capacity)
                elif b == -2:
                    travel = nodes[a]['order'].service_time+problem.travel(left, right)+p.e2_return_unload_time
                    if linear_links:
                        m.addConstr(end >= times[a]+travel-max(0, nodes[a]['window'][1]+travel-p.cycle_start)*(1-active))
                    else:
                        m.addConstr((active == 1) >> (end >= times[a]+travel))
                else:
                    travel = nodes[a]['order'].service_time+problem.travel(left, right)
                    if linear_links:
                        m.addConstr(times[b] >= times[a]+travel-max(0, nodes[a]['window'][1]+travel-nodes[b]['window'][0])*(1-active))
                    else:
                        m.addConstr((active == 1) >> (times[b] >= times[a]+travel))
                    m.addConstr(ranks[b] >= ranks[a]+1-(n+1)*(1-active))
                    for load, resource, initial, capacity in loads:
                        delta = nodes[b][resource]*(1 if nodes[b]['key'][1] == 'p' else -1)
                        propagate_load(active, load[a], load[b], delta, capacity)
            m.update()
            check()
        except Exception:
            m.dispose()
            raise

    def set_prices(self, duals, fleet_dual, phase_one, subset_duals=None):
        import gurobipy as gp
        m, nodes = self.model, self.context['nodes']
        fleet_dual = min(0.0, float(fleet_dual))
        signature = (bool(phase_one), fleet_dual,
                     tuple(float(duals.get(n['key'], 0)) for n in nodes),
                     tuple(float((subset_duals or {}).get(sid, 0)) for sid in self.subset_flags))
        if signature == getattr(self, '_price_signature', None):
            return
        m.setObjective((0 if phase_one else self.cost)
                       -gp.quicksum(float(duals.get(node['key'], 0))*self.visit[i] for i, node in enumerate(nodes))-fleet_dual
                       -gp.quicksum(float((subset_duals or {}).get(sid,0))*flag for sid,flag in self.subset_flags.items()))
        self._price_signature = signature

    def price(self, duals, fleet_dual=0.0, *, time_limit=2, phase_one=False, stop_at_negative=False, subset_duals=None):
        import gurobipy as gp
        from gurobipy import GRB
        m, nodes = self.model, self.context['nodes']
        fleet_dual = min(0.0, float(fleet_dual))
        subset_duals = subset_duals or {}
        self.set_prices(duals, fleet_dual, phase_one, subset_duals)
        m.Params.TimeLimit = max(0.0, time_limit)
        def negative(model, where):
            if where == GRB.Callback.MIPSOL and model.cbGet(GRB.Callback.MIPSOL_OBJ) < -1e-6:
                model.terminate()
        m.optimize(negative if stop_at_negative else None)
        status = m.Status
        analytic = -math.fsum(max(0.0, float(duals.get(node['key'], 0))) for node in nodes)-fleet_dual
        analytic -= math.fsum(max(0,float(subset_duals.get(sid,0))) for sid in self.subset_flags)
        try:
            raw = m.ObjBound if status != GRB.INFEASIBLE else None
        except (gp.GurobiError, AttributeError):
            raw = None
        margin = 1e-7*max(1.0, math.fsum(abs(float(duals.get(node['key'], 0))) for node in nodes)
            +math.fsum(abs(float(subset_duals.get(sid,0))) for sid in self.subset_flags), abs(fleet_dual))
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
            reduced -= math.fsum(float(subset_duals.get(sid,0)) for sid,keys in self.subset_keys.items() if keys.intersection(column['tasks']))
            if not math.isclose(reduced, m.ObjVal, rel_tol=1e-7, abs_tol=1e-5):
                raise RuntimeError('Pricing objective differs from raw route reduced cost')
        return dict(status=int(status), optimal=status == GRB.OPTIMAL, infeasible=status == GRB.INFEASIBLE,
                    lower_bound=float(lower), raw_lower_bound=float(raw) if raw is not None and math.isfinite(raw) else None,
                    margin=margin, reduced_cost=reduced, column=column, runtime=m.Runtime,
                    work=m.Work, variables=m.NumVars, constraints=m.NumConstrs)

    def dispose(self):
        self.model.dispose()


class RoutePricer:
    """Exact union of one delivery-batch pricer and a pickup-only pricer.

    The categories partition ALL relaxed trips. Each category excludes the
    other batches' delivery tasks, while retaining every pickup task. Their
    minimum certified bounds therefore bound the complete pricing problem.
    Unvisited categories use a safe analytic bound, never an incumbent cost.
    """

    def __init__(self, problem, context, **options):
        self.problem, self.context, self.groups = problem, context, []
        categories = [None]+[b.id for b in problem.operating_policy.batches
            if any(n['key'][1] == 'd' and n['order'].batch == b.id for n in context['nodes'])]
        try:
            for category in categories:
                nodes = [n for n in context['nodes'] if n['key'][1] == 'p' or n['order'].batch == category]
                subcontext = {**context, 'nodes': nodes, 'lookup': {n['key']: n for n in nodes}}
                self.groups.append((category, TripPricingModel(problem, subcontext, require_batch=category, **options)))
        except Exception:
            self.dispose()
            raise
        # Compatibility for aggregate statistics and diagnostic exports.
        self.model = self

    @property
    def NumVars(self):
        return sum(p.model.NumVars for _, p in self.groups)

    def write(self, filename):
        from pathlib import Path
        path = Path(filename)
        for i, (_, pricer) in enumerate(self.groups):
            pricer.model.write(str(path.with_name(path.stem+f'_group_{i}'+path.suffix)))

    def price(self, duals, fleet_dual=0.0, *, time_limit=2, phase_one=False, stop_at_negative=False, category_duals=None, subset_duals=None, reuse_budget=False):
        begun, deadline = time.perf_counter(), time.perf_counter()+time_limit
        nu = min(0.0, float(fleet_dual))
        reports, candidates, stopped = [], [], False
        category_duals = category_duals or {}
        for _, pricer in self.groups:
            pricer.set_prices(duals, nu, phase_one, subset_duals)
        # A high prize total is promising for discovering a negative column.
        ordered = sorted(self.groups, key=lambda group: -math.fsum(max(0.0, duals.get(n['key'], 0))
                         for n in group[1].context['nodes']))
        for i, (category, pricer) in enumerate(ordered):
            analytic = -math.fsum(max(0.0, duals.get(n['key'], 0)) for n in pricer.context['nodes'])-nu
            analytic -= math.fsum(max(0,float((subset_duals or {}).get(sid,0))) for sid in pricer.subset_flags)
            remaining = deadline-time.perf_counter()
            if not stopped and remaining > 0:
                report = pricer.price(duals, nu, time_limit=remaining/(len(ordered)-i),
                    phase_one=phase_one, stop_at_negative=stop_at_negative, subset_duals=subset_duals)
                prize = float(category_duals.get(category, 0.0))
                if prize:
                    report = {**report}
                    report['lower_bound'] -= prize
                    if report['raw_lower_bound'] is not None:report['raw_lower_bound'] -= prize
                    if report['reduced_cost'] is not None:report['reduced_cost'] -= prize
                    report['category_dual'] = prize
                if report['column'] is not None:
                    candidates.append(report)
                    if stop_at_negative and report['reduced_cost'] < -1e-6:
                        stopped = True
            else:
                report = dict(status=9, optimal=False, infeasible=False, lower_bound=analytic-float(category_duals.get(category,0)),
                              raw_lower_bound=analytic-float(category_duals.get(category,0)), margin=0, reduced_cost=None, column=None, runtime=0, work=0)
            reports.append(dict(category=category, **{key: value for key, value in report.items() if key != 'column'}))
        # Easy categories frequently leave most of their shares unused. With
        # unchanged prices, resume the unfinished categories within this same
        # deadline instead of abandoning their branch-and-bound certificate.
        pending = [r for r in reports if not r['optimal'] and not r['infeasible']]
        if reuse_budget and not stop_at_negative:
            for i, old in enumerate(pending):
                remaining = deadline-time.perf_counter()
                if remaining <= .001:
                    break
                category = old['category']
                pricer = next(p for c, p in self.groups if c == category)
                new = pricer.price(duals, nu, time_limit=remaining/(len(pending)-i),
                    phase_one=phase_one, subset_duals=subset_duals)
                prize = float(category_duals.get(category, 0.0))
                if prize:
                    new['lower_bound'] -= prize
                    if new['raw_lower_bound'] is not None:new['raw_lower_bound'] -= prize
                    if new['reduced_cost'] is not None:new['reduced_cost'] -= prize
                    new['category_dual'] = prize
                if new['column'] is not None:candidates.append(new)
                # Both bounds refer to exactly the same complete category.
                raw = [r['raw_lower_bound'] for r in (old, new) if r['raw_lower_bound'] is not None]
                merged = dict(category=category, **{k: v for k, v in new.items() if k != 'column'})
                merged.update(lower_bound=max(old['lower_bound'], new['lower_bound']),
                    raw_lower_bound=max(raw) if raw else None,
                    runtime=old['runtime']+new['runtime'], work=old['work']+new['work'],
                    budget_resumed=True)
                reports[reports.index(old)] = merged
        feasible_groups = [r for r in reports if not r['infeasible']]
        best = min(candidates, key=lambda r: r['reduced_cost']) if candidates else None
        lower = min((r['lower_bound'] for r in feasible_groups), default=0.0)
        raw = min((r['raw_lower_bound'] if r['raw_lower_bound'] is not None else r['lower_bound']
                   for r in feasible_groups), default=0.0)
        return dict(status=2 if all(r['optimal'] or r['infeasible'] for r in reports) else 9,
                    optimal=all(r['optimal'] or r['infeasible'] for r in reports), infeasible=not feasible_groups,
                    lower_bound=lower, raw_lower_bound=raw, margin=max((r['margin'] for r in reports), default=0),
                    reduced_cost=best['reduced_cost'] if best else None, column=best['column'] if best else None,
                    columns=[r['column'] for r in candidates],
                    runtime=time.perf_counter()-begun, work=sum(r['work'] for r in reports),
                    groups=reports, variables=self.NumVars,
                    max_model_variables=max((p.model.NumVars for _, p in self.groups), default=0))

    def dispose(self):
        for _, pricer in self.groups:
            pricer.dispose()
