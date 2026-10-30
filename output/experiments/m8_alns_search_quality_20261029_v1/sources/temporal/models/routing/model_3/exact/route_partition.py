"""Input-derived route-partition lower bound for small fixed-assignment M3.

Every original E2 trip projects to a task-set column whose minimum relaxed
distance is no greater than its physical distance. A covering tour in the
shortest-distance closure lower-bounds the sum of E1 tours. Supply, vehicle
calendars and cross-trip timing are relaxed here; the complete M3 MILP still
finds and certifies the original integer-time schedule.
"""
from __future__ import annotations

import itertools
import math
import time

MAX_ORDERS = 9
MAX_PREFIXES = 500_000


def enumerate_trips(problem, parameters, *, deadline, max_prefixes=MAX_PREFIXES):
    """Enumerate all relaxed task masks; never return an incomplete library.

    Return-to-base tests only filter completed columns, never prefixes. This
    matters for directed, nonmetric networks, where another visit can shorten
    the eventual return. Forward earliest scheduling is exact within a fixed
    sequence: all release/minimum-use constraints point forward.
    """
    orders = list(problem.orders)
    n = len(orders)
    policy, fleet = problem.operating_policy, problem.fleet
    windows = parameters.task_windows
    columns, prefixes = {}, 0

    def check():
        if time.perf_counter() >= deadline:
            raise TimeoutError('Route-partition preparation exhausted its budget')

    for site in problem.satellites:
        local = [i for i, order in enumerate(orders) if order.satellite == site]
        for size in range(len(local) + 1):
            for chosen in itertools.combinations(local, size):
                check()
                weight0 = sum(problem.task(orders[i].id, 'd')[1] for i in chosen)
                volume0 = sum(problem.task(orders[i].id, 'd')[2] for i in chosen)
                if weight0 > fleet.e2_weight_cap + 1e-8 or volume0 > fleet.e2_volume_cap + 1e-8:
                    continue
                dm = sum(1 << i for i in chosen)
                delivered_at = {}

                def walk(at, clock, mask, remaining, weight, volume, distance):
                    nonlocal prefixes
                    prefixes += 1
                    if prefixes > max_prefixes:
                        raise TimeoutError('Route-partition enumeration reached its prefix limit')
                    if prefixes % 128 == 0:
                        check()
                    if not remaining and mask:
                        end = clock + problem.travel(at, site) + policy.e2_return_unload_time
                        has_pickups = mask >> n
                        if (end + policy.e2_turnaround_time <= policy.cycle_end
                                and (not has_pickups or end <= policy.batches[0].cutoff)):
                            length = distance + problem.distance(at, site)
                            if mask not in columns or length < columns[mask]['distance']:
                                columns[mask] = dict(mask=mask, distance=length, satellite=site)
                    for i in chosen:
                        if not remaining & (1 << i):
                            continue
                        order = orders[i]
                        lo, hi = windows[order.id, 'd']
                        arrive = max(clock + problem.travel(at, order.customer), lo)
                        if arrive > hi:
                            continue
                        _, dw, dv = problem.task(order.id, 'd')
                        delivered_at[i] = arrive
                        walk(order.customer, arrive + order.service_time, mask | (1 << i),
                             remaining ^ (1 << i), weight - dw, volume - dv,
                             distance + problem.distance(at, order.customer))
                        del delivered_at[i]
                    for i in local:
                        order, bit = orders[i], 1 << (n + i)
                        if mask & bit or remaining & (1 << i):
                            continue
                        _, pw, pv = problem.task(order.id, 'p')
                        if weight + pw > fleet.e2_weight_cap + 1e-8 or volume + pv > fleet.e2_volume_cap + 1e-8:
                            continue
                        lo, hi = windows[order.id, 'p']
                        ready = max(lo, delivered_at.get(i, windows[order.id, 'd'][0])
                                    + order.service_time + order.min_use_time)
                        arrive = max(clock + problem.travel(at, order.customer), ready)
                        if arrive > hi:
                            continue
                        walk(order.customer, arrive + order.service_time, mask | bit, remaining,
                             weight + pw, volume + pv,
                             distance + problem.distance(at, order.customer))

                start = policy.cycle_start
                if chosen:
                    start = max(start, parameters.bounds['supply'][site][0]
                                + policy.e2_dispatch_prep_time)
                walk(site, start, 0, dm, weight0, volume0, 0.)
    check()
    return list(columns.values()), prefixes


def e1_distance_bound(problem, *, deadline):
    """Held-Karp visit-set bound in a directed shortest-distance closure."""
    nodes = list(problem.network.nodes)
    distances = {(a, b): problem.distance(a, b) for a in nodes for b in nodes}
    for via in nodes:
        for a in nodes:
            if time.perf_counter() >= deadline:
                raise TimeoutError('Distance closure exhausted the route-bound budget')
            for b in nodes:
                distances[a, b] = min(distances[a, b], distances[a, via] + distances[via, b])
    sites = sorted({order.satellite for order in problem.orders})
    dp = {(1 << i, i): distances[problem.depot, site] for i, site in enumerate(sites)}
    for mask in range(1, 1 << len(sites)):
        for j in range(len(sites)):
            if not mask & (1 << j):
                continue
            previous = mask ^ (1 << j)
            if previous:
                dp[mask, j] = min(dp[previous, i] + distances[sites[i], sites[j]]
                                  for i in range(len(sites)) if previous & (1 << i))
    return min(dp[(1 << len(sites)) - 1, i] + distances[site, problem.depot]
               for i, site in enumerate(sites))


def strengthen(problem, model, context, *, deadline, threads=1, seed=42):
    """Add a valid objective floor; discard aborted enumerations in full.

    This is an independently solved relaxation, not a feasible M3 solution.
    Its bound never changes the final native status or bypasses the auditor.
    """
    info = dict(applied=False, kind='input_only_route_partition',
                complete_library=False, historical_information_used=False)
    begun = time.perf_counter()

    def finish(reason=None):
        if reason is not None:
            info['reason'] = reason
        info['total_seconds'] = time.perf_counter() - begun
        return info

    if (problem.model_number != 3 or problem.free_assignment or not problem.reusable
            or not 1 <= len(problem.orders) <= MAX_ORDERS):
        return finish('outside_small_fixed_assignment_scope')
    if time.perf_counter() >= deadline:
        return finish('preparation_budget_exhausted')
    if getattr(problem, 'cost_objective', False):
        rates = problem.cost_profile.rates
        e1_rate = rates['distance_scale_to_km'] * rates['e1_distance_cost_per_km']
        e2_rate = rates['distance_scale_to_km'] * rates['e2_distance_cost_per_km']
        constant = (rates['container_pool_cost_per_period'] * problem.operating_policy.container_pool_size
                    + sum(problem.cost_profile.constant_cost(problem.total).values()))
        if any(value < 0 for value in rates.values() if isinstance(value, (int, float))):
            return finish('negative_cost_rate')
    else:
        e1_rate = e2_rate = 1.
        constant = 0.
    try:
        columns, prefixes = enumerate_trips(problem, context['parameters'], deadline=deadline)
        first_distance = e1_distance_bound(problem, deadline=deadline)
    except TimeoutError:
        # Optimizing a truncated route set would yield an unsafe lower bound.
        return finish('incomplete_enumeration_discarded')
    info.update(complete_library=True, columns=len(columns), enumerated_prefixes=prefixes,
                preparation_seconds=time.perf_counter()-begun)
    if not columns or time.perf_counter() >= deadline:
        return finish('no_relaxation_budget')
    import gurobipy as gp

    relaxation = gp.Model('m3_input_route_partition_lower_bound')
    try:
        relaxation.Params.OutputFlag = 0
        relaxation.Params.Threads = threads
        relaxation.Params.Seed = seed
        relaxation.Params.MIPGap = 0
        relaxation.Params.MIPGapAbs = 0
        route = relaxation.addVars(len(columns), vtype=gp.GRB.BINARY, name='trip')
        for task in range(2 * len(problem.orders)):
            relaxation.addConstr(gp.quicksum(route[c] for c, col in enumerate(columns)
                                            if col['mask'] & (1 << task)) == 1)
        relaxation.setObjective(constant + e1_rate * first_distance
                                + e2_rate * gp.quicksum(route[c] * col['distance']
                                                       for c, col in enumerate(columns)))
        relaxation.Params.TimeLimit = max(0., deadline-time.perf_counter())
        relaxation.optimize()
        info.update(native_status=relaxation.Status, optimize_seconds=relaxation.Runtime,
                    work=relaxation.Work, nodes=relaxation.NodeCount)
        try:
            bound = float(relaxation.ObjBound)
        except (AttributeError, gp.GurobiError):
            return finish('no_finite_relaxation_bound')
        if relaxation.SolCount:
            info['selected_trip_masks'] = [columns[c]['mask'] for c in route if route[c].X > .5]
        if math.isfinite(bound) and abs(bound) < 1e90 and relaxation.Status != gp.GRB.INFEASIBLE:
            # Round down one representable step. An arbitrary absolute slack
            # leaves an artificial positive gap with MIPGap=0 even when the
            # independently solved relaxation already proves the optimum.
            floor = math.nextafter(bound, -math.inf)
            model.addConstr(model.getObjective() >= floor, name='input_route_partition_cost_floor')
            model.update()
            info.update(applied=True, lower_bound=bound, objective_floor=floor,
                        e1_distance_lower_bound=first_distance)
        return finish()
    finally:
        relaxation.dispose()


def prepare_start(problem, model, context, masks, *, deadline, threads=1, seed=42):
    """Find a fresh schedule in a copy restricted to one relaxed partition.

    All original routes, orders, clocks, E1 decisions and vehicle schedules
    remain in the copy. Equal visits keep each task set on one trip; distinct
    sets use distinct trips. No route order, physical vehicle or slot is fixed.
    Only the complete unrestricted main model can certify global optimality.
    """
    info = dict(supplied=False, source='fresh_route_partition_full_schedule')
    begun = time.perf_counter()
    if not masks or begun >= deadline:
        return dict(info, total_seconds=0., reason='no_candidate_or_budget')
    orders = list(problem.orders)
    n = len(orders)
    groups = {}
    for mask in masks:
        keys = [(order.id, kind) for i, order in enumerate(orders) for kind, bit in (
            ('d', i), ('p', n+i)) if mask & (1 << bit)]
        sites = {problem.order_map[key[0]].satellite for key in keys}
        if len(sites) != 1:
            raise ValueError('A trip partition must belong to one satellite')
        groups.setdefault(next(iter(sites)), []).append(keys)
    model.update()
    pilot = model.copy()
    try:
        pilot.ModelName = 'm3_fresh_partition_schedule'
        copy_vars = pilot.getVars()
        for route in context['e2']:
            if time.perf_counter() >= deadline:
                return dict(info, total_seconds=time.perf_counter()-begun, reason='pilot_preparation_timeout')
            partitions = groups.get(route['satellite'], [])
            used = copy_vars[route['used'].index]
            if route['slot'] >= len(partitions):
                used.UB = 0
                continue
            visits = {(node['order'].id, node['kind']): copy_vars[route['visit'][i].index]
                      for i, node in enumerate(route['nodes'])}
            anchors = []
            for keys in partitions:
                anchor = visits[keys[0]]
                anchors.append(anchor)
                for key in keys[1:]:
                    pilot.addConstr(visits[key] == anchor)
            import gurobipy as gp
            pilot.addConstr(used == gp.quicksum(anchors))
        pilot.Params.OutputFlag = 0
        pilot.Params.Threads = threads
        pilot.Params.Seed = seed
        pilot.Params.TimeLimit = max(0., deadline-time.perf_counter())
        pilot.optimize()
        info.update(native_status=pilot.Status, optimize_seconds=pilot.Runtime,
                    work=pilot.Work, nodes=pilot.NodeCount)
        if pilot.SolCount:
            original_vars = model.getVars()
            assert len(original_vars) == len(copy_vars)
            model.setAttr('Start', original_vars, pilot.getAttr('X', copy_vars))
            info.update(supplied=True, objective=pilot.ObjVal)
        info['total_seconds'] = time.perf_counter()-begun
        return info
    finally:
        pilot.dispose()
