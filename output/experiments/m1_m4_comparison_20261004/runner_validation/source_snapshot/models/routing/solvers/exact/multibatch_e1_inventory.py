"""E1 routing/inventory relaxation: a bound on E1 cost, not a M5 solver.

Every complete M5 schedule projects with unchanged E1 routes and cost.
E2 trips are replaced by independent order return times subject to necessary
supply/use/shortest-time bounds. Cutoff quantities, E1 capacity, physical
vehicles and clean stock remain. Never use a relaxation incumbent as a bound.
"""
from __future__ import annotations

import math
from pathlib import Path
import time

from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.precheck import travel_lower_bounds


def add_order_returns(problem, m, deliveries, travel, check):
    """Common necessary E2 supply/use/return conditions for both E1 models."""
    import gurobipy as gp
    from gurobipy import GRB
    p, returns, allocation, order_times = problem.operating_policy, {}, {}, {}
    for order in problem.orders:
        check()
        site, bid = order.satellite, order.batch
        outward = travel['outward'][site][order.customer]
        inward = travel['inward'][site][order.customer]
        supplied = deliveries[site, bid]+p.e2_dispatch_prep_time+outward
        m.addConstr(supplied <= order.delivery_window[1])
        d = m.addVar(vtype=GRB.INTEGER, lb=order.delivery_window[0], ub=order.delivery_window[1], name=f'delivery_{order.id}')
        pick = m.addVar(vtype=GRB.INTEGER, lb=order.pickup_window[0], ub=order.pickup_window[1], name=f'pickup_time_{order.id}')
        order_times[order.id, 'd'], order_times[order.id, 'p'] = d, pick
        m.addConstr(d >= supplied)
        m.addConstr(pick >= d+order.service_time+order.min_use_time)
        returned = m.addVar(vtype=GRB.INTEGER, lb=p.cycle_start,
            ub=min(p.batches[-1].cutoff, p.cycle_end-p.e2_turnaround_time), name=f'e2_return_{order.id}')
        m.addConstr(returned >= pick+order.service_time+inward+p.e2_return_unload_time)
        returns[order.id] = returned
        choices = []
        for index, b in enumerate(p.batches):
            z = m.addVar(vtype=GRB.BINARY, name=f'cutoff_{order.id}_{b.id}')
            allocation[order.id, b.id] = z
            low = p.cycle_start if index == 0 else p.batches[index-1].cutoff+1
            m.addConstr((z == 1) >> (returned >= low))
            m.addConstr((z == 1) >> (returned <= b.cutoff))
            choices.append(z)
        m.addConstr(gp.quicksum(choices) == 1)
    return returns, allocation, order_times


def build_e1_inventory(problem, *, deadline=None, inventory=True, verbose=False, travel_bounds=None,
                       symmetry=True, distance_bounds=None):
    import gurobipy as gp
    from gurobipy import GRB
    if (problem.model_number != 5 or problem.free_assignment or not problem.reusable
            or len(problem.operating_policy.batches) <= 1):
        raise ValueError('E1 inventory certificates require fixed reusable multibatch M5')
    p, f = problem.operating_policy, problem.fleet
    H, total = p.cycle_end, max(1, problem.total)
    travel = travel_bounds or travel_lower_bounds(problem)
    m = gp.Model('M5_E1_inventory_relaxation')
    m.Params.OutputFlag = int(verbose)
    m.Params.Threads = 1
    m.Params.MIPGap = m.Params.MIPGapAbs = 0
    m.Params.DualReductions = 0
    routes, objective, costs = [], gp.LinExpr(), {}
    deliveries = {(s, b.id): m.addVar(vtype=GRB.INTEGER, ub=H, name=f'supply_{s}_{b.id}')
                  for b in p.batches for s in problem.satellites if problem.demands[s, b.id]}
    counts = {s: sum(o.count for o in problem.orders if o.satellite == s) for s in problem.satellites}
    def check():
        if deadline is not None and time.perf_counter() >= deadline:
            raise TimeoutError('E1 inventory build reached its total budget')
    try:
        for b in p.batches:
            costs[b.id] = gp.LinExpr()
            for vehicle in range(f.e1_count):
                check()
                label = f'{b.id}_{vehicle}'
                used = m.addVar(vtype=GRB.BINARY, name=f'used_{label}')
                has_d = m.addVar(vtype=GRB.BINARY, name=f'delivery_trip_{label}')
                only_p = m.addVar(vtype=GRB.BINARY, name=f'pickup_only_{label}')
                m.addConstr(only_p == used-has_d)
                start = m.addVar(vtype=GRB.INTEGER, ub=H, name=f'departure_{label}')
                end = m.addVar(vtype=GRB.INTEGER, ub=H, name=f'return_{label}')
                m.addConstr(start <= H*used)
                m.addConstr(end <= H*used)
                m.addConstr((used == 1) >> (start >= b.start))
                m.addConstr((has_d == 1) >> (start == b.start+p.e1_load_time))
                m.addConstr((only_p == 1) >> (start >= b.cutoff))
                m.addConstr((used == 1) >> (end <= b.end))
                m.addConstr((used == 1) >> (end+p.e1_turnaround_time <= H))
                nodes = []
                for site in problem.satellites:
                    if problem.demands[site, b.id]:
                        q = problem.demands[site, b.id]
                        nodes.append(dict(site=site, kind='d', quantity=q,
                            weight=problem.weights[site, b.id], volume=q*f.container_volume))
                    if counts[site]:
                        q = m.addVar(vtype=GRB.INTEGER, ub=counts[site], name=f'pickup_{label}_{site}')
                        nodes.append(dict(site=site, kind='p', quantity=q,
                            weight=q*f.container_weight, volume=q*f.container_volume))
                n = len(nodes)
                visit = m.addVars(n, vtype=GRB.BINARY, name=f'visit_{label}')
                times = m.addVars(n, vtype=GRB.INTEGER, ub=H, name=f'time_{label}')
                rank = m.addVars(n, ub=n, name=f'rank_{label}')
                arcs = [(-1, i) for i in range(n)]+[(i, -2) for i in range(n)]
                arcs += [(i, j) for i in range(n) for j in range(n) if i != j
                         and not (nodes[i]['kind'] == 'p' and nodes[j]['kind'] == 'd')]
                x = m.addVars(arcs, vtype=GRB.BINARY, name=f'arc_{label}')
                m.addConstr(gp.quicksum(x[-1, i] for i in range(n)) == used)
                m.addConstr(gp.quicksum(x[i, -2] for i in range(n)) == used)
                dvis = [visit[i] for i, node in enumerate(nodes) if node['kind'] == 'd']
                m.addConstr(has_d <= gp.quicksum(dvis))
                for v in dvis:
                    m.addConstr(v <= has_d)
                for i, node in enumerate(nodes):
                    m.addConstr(gp.quicksum(active for (a, c), active in x.items() if c == i) == visit[i])
                    m.addConstr(gp.quicksum(active for (a, c), active in x.items() if a == i) == visit[i])
                    m.addConstr(visit[i] <= used)
                    m.addConstr(rank[i] >= visit[i])
                    m.addConstr(rank[i] <= n*visit[i])
                    m.addConstr(times[i] <= H*visit[i])
                    if node['kind'] == 'd':
                        m.addConstr((visit[i] == 1) >> (times[i]+problem.service(node['site']) <= b.cutoff))
                        m.addConstr((visit[i] == 1) >> (deliveries[node['site'], b.id]
                                                       == times[i]+problem.service(node['site'])))
                    else:
                        m.addConstr(node['quantity'] >= visit[i])
                        m.addConstr(node['quantity'] <= counts[node['site']]*visit[i])
                        m.addConstr((visit[i] == 1) >> (end+p.cleaning_duration <= H))
                for resource, cap in (('weight', f.e1_weight_cap), ('volume', f.e1_volume_cap)):
                    capacity = cap+1e-6
                    loads = m.addVars(n, ub=capacity, name=f'{resource}_{label}')
                    initial = gp.quicksum(node[resource]*visit[i] for i, node in enumerate(nodes) if node['kind'] == 'd')
                    m.addConstr(initial <= capacity)
                    for i in range(n):
                        m.addConstr(loads[i] <= capacity*visit[i])
                    for (a, c), active in x.items():
                        if c == -2:
                            continue
                        delta = nodes[c][resource]*(1 if nodes[c]['kind'] == 'p' else -1)
                        before = initial if a == -1 else loads[a]
                        m.addConstr((active == 1) >> (loads[c] == before+delta))
                for (a, c), active in x.items():
                    left = problem.depot if a == -1 else nodes[a]['site']
                    right = problem.depot if c == -2 else nodes[c]['site']
                    objective += problem.distance(left, right)*active
                    costs[b.id] += problem.distance(left, right)*active
                    if c == -2:
                        m.addConstr((active == 1) >> (end >= times[a]+problem.service(left)
                                                     +problem.travel(left, right)+p.depot_return_unload_time))
                    else:
                        previous = start if a == -1 else times[a]+problem.service(left)
                        m.addConstr((active == 1) >> (times[c] >= previous+problem.travel(left, right)))
                        if nodes[c]['kind'] == 'p' and (a == -1 or nodes[a]['kind'] == 'd'):
                            # The return leg starts at its last delivery site
                            # (or depot) only after the cutoff snapshot.
                            m.addConstr((active == 1) >> (times[c] >= b.cutoff+problem.travel(left, right)))
                        if a != -1:
                            m.addConstr(rank[c] >= rank[a]+1-(n+1)*(1-active))
                quantity = gp.quicksum(node['quantity'] for node in nodes if node['kind'] == 'p')
                for previous in routes:
                    if previous['vehicle'] == vehicle:
                        both = m.addVar(vtype=GRB.BINARY)
                        m.addConstr(both <= previous['used'])
                        m.addConstr(both <= used)
                        m.addConstr(both >= previous['used']+used-1)
                        m.addConstr((both == 1) >> (start-p.e1_load_time*has_d
                                                   >= previous['end']+p.e1_turnaround_time))
                routes.append(dict(batch=b, vehicle=vehicle, used=used, has_delivery=has_d,
                                   start=start, end=end, nodes=nodes, visit=visit, times=times,
                                   rank=rank, x=x, quantity=quantity))
                if symmetry and p.e1_turnaround_time == 0 and vehicle:
                    # Nonoverlapping calendars make E1 vehicle identities
                    # interchangeable separately in each batch only when
                    # turnaround is zero. Sort nonempty routes by their
                    # smallest task index; each task has at most one visitor.
                    previous = routes[-2]
                    for i in range(n):
                        m.addConstr(visit[i] <= gp.quicksum(previous['visit'][j] for j in range(i)))
        for key in deliveries:
            site, bid = key
            m.addConstr(gp.quicksum(r['visit'][i] for r in routes if r['batch'].id == bid
                for i, node in enumerate(r['nodes']) if node['site'] == site and node['kind'] == 'd') == 1)
        returns, allocation, order_times = add_order_returns(problem, m, deliveries, travel, check)
        for site in problem.satellites:
            for b in p.batches:
                picked = [(r, i, node) for r in routes if r['batch'].id == b.id
                          for i, node in enumerate(r['nodes']) if node['site'] == site and node['kind'] == 'p']
                m.addConstr(gp.quicksum(node['quantity'] for _, _, node in picked)
                    == gp.quicksum(o.count*allocation[o.id, b.id] for o in problem.orders if o.satellite == site))
                m.addConstr(gp.quicksum(r['visit'][i] for r, i, _ in picked) <= 1)
        releases = {}
        if inventory:
            cumulative = 0
            for b in p.batches:
                cumulative += problem.batch_demands[b.id]
                released = []
                for index, r in enumerate(routes):
                    if problem.batch_index[r['batch'].id] >= problem.batch_index[b.id]:
                        continue
                    ready = m.addVar(vtype=GRB.BINARY, name=f'clean_ready_{index}_{b.id}')
                    m.addConstr(ready <= r['used'])
                    m.addConstr((ready == 1) >> (r['end']+p.cleaning_duration <= b.start))
                    q = m.addVar(vtype=GRB.INTEGER, ub=total, name=f'clean_quantity_{index}_{b.id}')
                    m.addConstr(q <= r['quantity'])
                    m.addConstr(q <= total*ready)
                    m.addConstr(q >= r['quantity']-total*(1-ready))
                    releases[index, b.id] = (ready, q)
                    released.append(q)
                m.addConstr(p.container_pool_size+gp.quicksum(released) >= cumulative, f'clean_stock_{b.id}')
        for row in (distance_bounds or {}).get('components', []):
            if row['echelon'] == 'E1':
                lower = max(0, row['bound']-1e-7*max(1, row['bound']))
                m.addConstr(costs[row['batch']] >= lower, f'delivery_distance_{row["batch"]}')
        m.setObjective(objective)
        m.update()
        check()
        return m, dict(routes=routes, deliveries=deliveries, returns=returns, allocation=allocation,
                       releases=releases, inventory=inventory,
                       batch_symmetry=symmetry and p.e1_turnaround_time == 0, order_times=order_times)
    except Exception:
        m.dispose()
        raise


def set_e1_projection(problem, context, initial_result, *, fix=False):
    """Set a full audited schedule's projection; fixing supports proof tests."""
    def put(var, value):
        var.Start = value
        if fix:
            var.LB = var.UB = value
    mapped = {(trip['batch_id'], trip['vehicle_id']): trip for trip in initial_result['e1_trips']}
    if context['batch_symmetry']:
        mapped = {}
        for batch in problem.operating_policy.batches:
            template = next(r for r in context['routes'] if r['batch'].id == batch.id)
            indices = {(node['site'], node['kind']): i for i, node in enumerate(template['nodes'])}
            trips = [t for t in initial_result['e1_trips'] if t['batch_id'] == batch.id]
            ordered = sorted(trips, key=lambda t: min(indices[e['site_id'], e['kind']] for e in t['events']))
            mapped.update({(batch.id, i): t for i, t in enumerate(ordered)})
    for r in context['routes']:
        trip = mapped.get((r['batch'].id, r['vehicle']))
        events = trip['events'] if trip else []
        has_d = int(any(e['kind'] == 'd' for e in events))
        put(r['used'], int(bool(trip)))
        put(r['has_delivery'], has_d)
        put(r['start'], trip['departure'] if trip else 0)
        put(r['end'], trip['return_complete'] if trip else 0)
        indices = {(node['site'], node['kind']): i for i, node in enumerate(r['nodes'])}
        order = [indices[e['site_id'], e['kind']] for e in events]
        selected = set(zip([-1]+order, order+[-2])) if order else set()
        event_map = {(e['site_id'], e['kind']): e for e in events}
        for i, node in enumerate(r['nodes']):
            event = event_map.get((node['site'], node['kind']))
            put(r['visit'][i], int(bool(event)))
            put(r['times'][i], event['start'] if event else 0)
            put(r['rank'][i], order.index(i)+1 if event else 0)
            if node['kind'] == 'p':
                put(node['quantity'], event['quantity'] if event else 0)
        for edge, var in r['x'].items():
            put(var, int(edge in selected))
    returned = {e['order_id']: trip['return_complete'] for trip in initial_result['e2_trips']
                for e in trip['events'] if e['kind'] == 'p'}
    for oid, var in context['returns'].items():
        put(var, returned[oid])
        first = next(b.id for b in problem.operating_policy.batches if returned[oid] <= b.cutoff)
        for b in problem.operating_policy.batches:
            put(context['allocation'][oid, b.id], int(first == b.id))
    for (index, bid), (ready, quantity) in context['releases'].items():
        r = context['routes'][index]
        trip = mapped.get((r['batch'].id, r['vehicle']))
        eligible = bool(trip and trip['return_complete']+problem.operating_policy.cleaning_duration <= problem.batch_map[bid].start)
        put(ready, int(eligible))
        put(quantity, sum(e['quantity'] for e in trip['events'] if e['kind'] == 'p') if eligible else 0)


def e1_inventory_certificate(problem, *, time_limit=10, initial_result=None, inventory=True,
                             seed=42, verbose=False, diagnostics_path=None, travel_bounds=None,
                             symmetry=True, distance_bounds=None, backend='auto'):
    import gurobipy as gp
    from gurobipy import GRB
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('time_limit must be finite and positive')
    if backend not in ('auto', 'patterns', 'indexed'):
        raise ValueError('backend must be auto, patterns or indexed')
    active = len({o.satellite for o in problem.orders})
    selected = 'patterns' if backend == 'auto' and active <= 4 else 'indexed' if backend == 'auto' else backend
    if selected == 'patterns' and active > 5:
        raise ValueError('Complete E1 enumeration is limited to five active satellites')
    begun = time.perf_counter()
    deadline = begun+time_limit
    if initial_result is not None and not audit_solution(problem, initial_result)['valid']:
        raise ValueError('Initial result must pass the full M5 independent audit')
    model = None
    try:
        if selected == 'patterns':
            from .multibatch_e1_routes import build_e1_routes, set_route_projection
            model, context = build_e1_routes(problem, deadline=deadline, inventory=inventory,
                verbose=verbose, travel_bounds=travel_bounds, distance_bounds=distance_bounds)
        else:
            model, context = build_e1_inventory(problem, deadline=deadline, inventory=inventory,
                verbose=verbose, travel_bounds=travel_bounds, symmetry=symmetry, distance_bounds=distance_bounds)
        model.Params.Seed = int(seed)
        if initial_result is not None:
            if selected == 'patterns':
                set_route_projection(problem, context, initial_result)
            else:
                set_e1_projection(problem, context, initial_result)
        model.Params.TimeLimit = max(0, deadline-time.perf_counter())
        model.optimize()
        try:
            raw = model.ObjBound
        except (gp.GurobiError, AttributeError):
            raw = None
        raw = float(raw) if raw is not None and math.isfinite(raw) and model.Status != GRB.INFEASIBLE else None
        margin = 1e-7*max(1, abs(raw)) if raw is not None else 0
        if diagnostics_path is not None:
            path = Path(diagnostics_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            model.write(str(path))
        return dict(bound=max(0, raw-margin) if raw is not None else 0.0,
            raw_bound=raw, margin=margin, status=int(model.Status), relaxation_optimal=model.Status == GRB.OPTIMAL,
            relaxation_infeasible=model.Status == GRB.INFEASIBLE,
            relaxation_objective=float(model.ObjVal) if model.SolCount else None,
            scope='M5_E1_routing_cutoff_and_clean_inventory_relaxation', inventory=inventory,
            backend=selected, enumeration_complete=context.get('enumeration_complete', False),
            route_columns=len(context['patterns']) if selected == 'patterns' else None,
            proven_optimal=False, proven_infeasible=False, variables=model.NumVars,
            constraints=model.NumConstrs, general_constraints=model.NumGenConstrs,
            work=model.Work, runtime=time.perf_counter()-begun)
    except TimeoutError:
        return dict(bound=0.0, raw_bound=None, status='TIME_LIMIT_BUILD', relaxation_optimal=False,
                    scope='M5_E1_routing_cutoff_and_clean_inventory_relaxation', inventory=inventory,
                    runtime=time.perf_counter()-begun, proven_optimal=False, proven_infeasible=False)
    finally:
        if model is not None:
            model.dispose()
