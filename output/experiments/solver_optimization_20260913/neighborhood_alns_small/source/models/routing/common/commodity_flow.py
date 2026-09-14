"""Vehicle-independent, complete two-echelon delivery/pickup formulation.

Each selected arc carries remaining deliveries and collected pickups. Their
sum is bounded by vehicle capacity for weight and volume separately. Degree
constraints form depot-rooted routes; order variables also exclude zero-time
subtours. Absolute task times couple both echelons and cross-vehicle precedence.
No routes, customer assignments, or handoff candidates are enumerated here.
"""
import math
import time
from importlib import import_module

import gurobipy as gp
from gurobipy import GRB

from .domain.node import Task
from models.routing.alns.types import is_tfp_active


def solve_commodity_flow(problem, model_number, vehicles_per_satellite, *,
                         e1_tasks=None, e2_tasks_by_sat=None, TFp=9999.0,
                         time_limit=None, mip_gap=0.0, gurobi_params=None,
                         verbose=False, return_info=False,
                         return_incumbent_on_time_limit=False,
                         warm_start_routes=None, auto_warm_start=True,
                         e2_cost_lower_bounds=None, e1_cost_lower_bound=None,
                         zero_pickup_load=False, excluded_assignments=None):
    started = time.perf_counter()
    free = model_number in (2, 4)
    pickup = model_number in (3, 4)
    depot = problem.depot.id
    satellites = [s.id for s in problem.satellites]
    customers = [c for c in problem.customers if pickup or c.demand > 1e-9]
    m = gp.Model(f'M{model_number}_complete_commodity_flow')
    m.Params.OutputFlag = int(verbose)
    m.Params.MIPGap = mip_gap
    m.Params.MIPGapAbs = 1e-7
    m.Params.IntFeasTol = 1e-9
    m.Params.FeasibilityTol = 1e-9
    if gurobi_params:
        for key, value in gurobi_params.items():
            m.setParam(key, value)
    w = {}
    active = {}
    e1_map = {}
    e2_map = {}
    required = {}
    weight, volume = {}, {}
    if free:
        w = {(c.id, s): m.addVar(vtype=GRB.BINARY, name=f'w[{c.id},{s}]')
             for c in customers for s in satellites}
        for c in customers:
            m.addConstr(gp.quicksum(w[c.id, s] for s in satellites) == 1)
        for assignment in excluded_assignments or []:
            m.addConstr(gp.quicksum(w[c.id, assignment[c.id]] for c in customers) <= len(customers)-1)
        cw, cv = problem.fleet.container_weight, problem.fleet.container_volume
        for c in customers:
            if c.demand > 1e-9:
                e2_map[f'{c.id}d'] = Task(f'{c.id}d', c.id, 'd', c.weight_del+cw, cv, c.tw_del)
            if pickup:
                e2_map[f'{c.id}p'] = Task(f'{c.id}p', c.id, 'p',
                    0.0 if zero_pickup_load else cw, 0.0 if zero_pickup_load else cv, c.tw_pick)
        for s in satellites:
            active[s] = m.addVar(vtype=GRB.BINARY, name=f'active[{s}]')
            total = gp.quicksum(w[c.id, s] for c in customers)
            m.addConstr(total <= len(customers)*active[s])
            m.addConstr(total >= active[s])
            for kind in ('d', 'p') if pickup else ('d',):
                node = f'{s}{kind}'
                e1_map[node] = Task(node, s, kind, 0, 0, (0, math.inf))
                weight[node] = gp.quicksum(
                    (c.weight_del+cw if kind == 'd' else cw)*w[c.id, s] for c in customers)
                volume[node] = cv*total
            required[s] = {t: w[task.cust, s] for t, task in e2_map.items()}
    else:
        e1_map = {t.id: t for t in e1_tasks}
        e2_map = {t.id: t for tasks in e2_tasks_by_sat.values() for t in tasks}
        weight = {t.id: t.w for t in e1_tasks}
        volume = {t.id: t.v for t in e1_tasks}
        active = {s: int(any(t.cust == s for t in e1_tasks)) for s in satellites}
        required = {s: {t.id: 1 for t in e2_tasks_by_sat.get(s, [])} for s in satellites}

    tasks = {**e1_map, **e2_map}
    phys = {t.id: t.cust for t in tasks.values()}
    service = {t: getattr(problem.nodes[task.cust], 'service_time', 0.0) for t, task in tasks.items()}
    def travel(i, j):
        return problem.travel_time(phys.get(i, i), phys.get(j, j))
    def distance(i, j):
        return problem.distance(phys.get(i, i), phys.get(j, j))
    finite = [v for t in tasks.values() for v in t.tw if math.isfinite(v)] + [0.0]
    if is_tfp_active(TFp):
        finite.append(TFp)
    max_arc = max((max(row) for row in problem.time), default=0.0)
    horizon = max(finite) + (len(tasks)+2)*max_arc + sum(service.values()) + 1.0
    lower = {n: max(0.0, t.tw[0]) for n, t in tasks.items()}
    upper = {n: min(horizon, t.tw[1]) for n, t in tasks.items()}
    for n, t in e1_map.items():
        if t.kind == 'p' and is_tfp_active(TFp):
            lower[n] = max(lower[n], TFp)
    times = {n: m.addVar(lb=lower[n], ub=upper[n], name=f'time[{n}]') for n in tasks}
    objective = gp.LinExpr()
    graphs = []

    def graph(label, base, nodes, demand, caps, start_node=None, return_node=None):
        nonlocal objective
        all_nodes = [base] + list(nodes)
        arcs = []
        for i in all_nodes:
            for j in all_nodes:
                if i == j:
                    continue
                if i != base and j != base:
                    if tasks[i].cust == tasks[j].cust and tasks[i].kind == 'p' and tasks[j].kind == 'd':
                        continue
                    if lower[i]+service[i]+travel(i, j) > upper[j]+1e-9:
                        continue
                arcs.append((i, j))
        arcvars = {a: m.addVar(vtype=GRB.BINARY, name=f'{label}[{a[0]},{a[1]}]') for a in arcs}
        incoming = {n: [a for a in arcs if a[1] == n] for n in all_nodes}
        outgoing = {n: [a for a in arcs if a[0] == n] for n in all_nodes}
        for n in nodes:
            m.addConstr(gp.quicksum(arcvars[a] for a in incoming[n]) == demand[n])
            m.addConstr(gp.quicksum(arcvars[a] for a in outgoing[n]) == demand[n])
        departures = gp.quicksum(arcvars[a] for a in outgoing[base])
        m.addConstr(departures == gp.quicksum(arcvars[a] for a in incoming[base]))
        m.addConstr(departures <= (problem.e1_vehicle_count if label == 'x' else vehicles_per_satellite))
        # Explicit order is needed even for co-located, zero-service tasks.
        order = {n: m.addVar(lb=0, ub=len(nodes), name=f'order_{label}[{n}]') for n in nodes}
        for (i, j), z in arcvars.items():
            if j != base:
                if i == base:
                    m.addConstr(order[j] >= z)
                    origin = times[start_node]+service[start_node] if start_node else 0.0
                    origin_ub = upper[start_node]+service[start_node] if start_node else 0.0
                else:
                    m.addConstr(order[j] >= order[i]+1-(len(nodes)+1)*(1-z))
                    origin = times[i]+service[i]
                    origin_ub = upper[i]+service[i]
                big_m = max(0.0, origin_ub+travel(i, j)-lower[j])
                m.addConstr(times[j] >= origin+travel(i, j)-big_m*(1-z))
            elif return_node:
                big_m = max(0.0, upper[i]+service[i]+travel(i, j)-lower[return_node])
                m.addConstr(times[return_node] >= times[i]+service[i]+travel(i, j)-big_m*(1-z))
        # Four commodities: remaining delivery / collected pickup, W / V.
        for dimension, capacity in zip(('w', 'v'), caps):
            delivery = {a: m.addVar(lb=0, ub=capacity, name=f'D{dimension}_{label}[{a[0]},{a[1]}]') for a in arcs}
            collected = {a: m.addVar(lb=0, ub=capacity, name=f'P{dimension}_{label}[{a[0]},{a[1]}]') for a in arcs}
            for a, z in arcvars.items():
                m.addConstr(delivery[a]+collected[a] <= capacity*z)
                if a[1] == base:
                    delivery[a].UB = 0
                if a[0] == base:
                    collected[a].UB = 0
            for n in nodes:
                task = tasks[n]
                amount = ((weight if dimension == 'w' else volume)[n] if label == 'x'
                          else (task.w if dimension == 'w' else task.v)*demand[n])
                m.addConstr(gp.quicksum(delivery[a] for a in incoming[n])-gp.quicksum(delivery[a] for a in outgoing[n])
                            == (amount if task.kind == 'd' else 0))
                m.addConstr(gp.quicksum(collected[a] for a in outgoing[n])-gp.quicksum(collected[a] for a in incoming[n])
                            == (amount if task.kind == 'p' else 0))
        route_cost = gp.quicksum(distance(*a)*z for a, z in arcvars.items())
        objective += route_cost
        graphs.append((label, base, arcvars))
        return route_cost

    e1_cost = graph('x', depot, e1_map, {n: active[t.cust] for n, t in e1_map.items()},
                    (problem.e1_weight_cap, problem.e1_volume_cap))
    if e1_cost_lower_bound:
        m.addConstr(e1_cost >= e1_cost_lower_bound)
    for s in satellites:
        if not required[s]:
            continue
        e2_cost = graph(f'z_{s}', s, required[s], required[s],
                       (problem.e2_weight_cap, problem.e2_volume_cap),
                       start_node=f'{s}d' if f'{s}d' in e1_map else None,
                       return_node=f'{s}p' if f'{s}p' in e1_map else None)
        if (e2_cost_lower_bounds or {}).get(s):
            m.addConstr(e2_cost >= e2_cost_lower_bounds[s])
    for task in e2_map.values():
        d, p = f'{task.cust}d', f'{task.cust}p'
        if task.kind == 'p' and d in times:
            m.addConstr(times[p] >= times[d]+service[d])
    if free:
        for c in customers:
            if math.isfinite(c.tw_del[1]):
                for s in satellites:
                    # Same satellite delivery due-date contract as the indexed MIP.
                    m.addConstr(times[f'{s}d'] <= c.tw_del[1]+horizon*(1-w[c.id, s]))
    m.setObjective(objective, GRB.MINIMIZE)

    warm_seconds = 0.0
    warm_info = None
    if auto_warm_start and warm_start_routes is None and time_limit != 0:
        cap = {1:0.05, 2:0.1, 3:0.2, 4:0.5}[model_number]
        allowance = cap if time_limit is None else min(cap, max(0.0, time_limit*0.05))
        remaining = math.inf if time_limit is None else time_limit-(time.perf_counter()-started)
        allowance = min(allowance, max(0.0, remaining))
        if allowance >= 0.02:
            warm_started = time.perf_counter()
            solver = import_module(f'models.routing.alns.variants.model_{model_number}').solve_alns
            kwargs = dict(problem=problem, vehicles_per_satellite=vehicles_per_satellite,
                          max_time=allowance, max_iterations=1000000, seed=int((gurobi_params or {}).get('Seed',42)),
                          verbose=False, return_info=True)
            if not free:
                kwargs.update(e1_tasks=e1_tasks, e2_tasks_by_sat=e2_tasks_by_sat)
            if pickup:
                kwargs['TFp'] = TFp
            result = solver(**kwargs)
            warm_info = result[-1]
            if warm_info.get('feasible'):
                warm_start_routes = result[:2]
            warm_seconds = time.perf_counter()-warm_started
    if warm_start_routes:
        r1, r2 = warm_start_routes
        for label, base, arcvars in graphs:
            routes = r1 if label == 'x' else (r2 or {}).get(base, [])
            selected = set()
            for route in routes or []:
                # M1 historically emits physical E1 satellite identifiers.
                normalized = [f'{n}d' if label == 'x' and n in satellites else n for n in route]
                selected.update(zip(normalized, normalized[1:]))
            for a, z in arcvars.items():
                z.Start = float(a in selected)
        if free:
            assignment = {n[:-1]: s for s, routes in (r2 or {}).items() for r in routes for n in r[1:-1]}
            for (c, s), z in w.items():
                z.Start = float(assignment.get(c) == s)
            for s, z in active.items():
                z.Start = float(s in assignment.values())
    build_seconds = time.perf_counter()-started-warm_seconds
    if time_limit is not None:
        m.Params.TimeLimit = max(0.0, time_limit-(time.perf_counter()-started))
    m.optimize()
    feasible = m.SolCount > 0
    proven = m.Status == GRB.OPTIMAL and float(m.Params.MIPGap) == 0.0
    info = dict(status_code=m.Status, status={GRB.OPTIMAL:'OPTIMAL',GRB.TIME_LIMIT:'TIME_LIMIT',GRB.INFEASIBLE:'INFEASIBLE'}.get(m.Status,str(m.Status)),
                solution_type='proven_optimal' if proven else ('feasible_not_proven' if feasible else 'none'),
                proven_optimal=proven, proof_scope='complete_vehicle_independent_commodity_flow',
                formulation=('zero_pickup_load_commodity_flow_relaxation' if zero_pickup_load
                             else 'complete_two_echelon_four_commodity_flow'), runtime=time.perf_counter()-started,
                mip_runtime=m.Runtime, build_time=build_seconds, warm_start_time=warm_seconds,
                warm_start_objective=(warm_info or {}).get('objective'),
                objective=m.ObjVal if feasible else None, best_bound=m.ObjBound,
                mip_gap=m.MIPGap if feasible else None, sol_count=m.SolCount,
                model_rows=m.NumConstrs, model_columns=m.NumVars, model_binary=m.NumBinVars,
                node_count=m.NodeCount, assignment=None)
    r1 = r2 = assignment = None
    if feasible and (proven or return_incumbent_on_time_limit):
        routes_by_label = {}
        for label, base, arcvars in graphs:
            selected = {a for a, z in arcvars.items() if z.X > 0.5}
            successors = {i:j for i,j in selected if i != base}
            routes = []
            for _, first in sorted(a for a in selected if a[0] == base):
                route = [base, first]
                while route[-1] != base:
                    route.append(successors[route[-1]])
                    if len(route) > len(tasks)+2:
                        raise RuntimeError('Disconnected route in flow incumbent')
                routes.append(route)
            routes_by_label[label] = routes
        r1 = routes_by_label['x']
        if model_number == 1:
            r1 = [[phys.get(n,n) for n in r] for r in r1]
        r2 = {s: routes_by_label.get(f'z_{s}',[]) for s in satellites}
        if free:
            assignment = {c.id: max(satellites, key=lambda s:w[c.id,s].X) for c in customers}
            info['assignment'] = assignment
    m.dispose()
    result = (r1, r2, assignment) if free else (r1, r2)
    return (*result, info) if return_info else result
