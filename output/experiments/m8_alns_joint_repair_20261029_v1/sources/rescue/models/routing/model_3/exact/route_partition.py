"""Input-derived, resource-bounded route bounds for fixed-assignment M3.

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

MAX_PREFIXES = 500_000


class IncompleteEnumeration(TimeoutError):
    """Partial columns are candidates only, never a route-partition bound."""

    def __init__(self, message, columns, prefixes):
        super().__init__(message)
        self.columns = list(columns.values())
        self.prefixes = prefixes


def enumerate_trips(problem, parameters, *, deadline, max_prefixes=MAX_PREFIXES,
                    sites=None, initial_columns=()):
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
    columns = {row['mask']: row for row in initial_columns}
    prefixes = 0

    def check():
        if time.perf_counter() >= deadline:
            raise IncompleteEnumeration('Route-partition preparation exhausted its budget', columns, prefixes)

    for site in (problem.satellites if sites is None else sites):
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
                path = []

                def walk(at, clock, mask, remaining, weight, volume, distance):
                    nonlocal prefixes
                    prefixes += 1
                    if prefixes > max_prefixes:
                        raise IncompleteEnumeration('Route-partition enumeration reached its prefix limit', columns, prefixes)
                    if prefixes % 128 == 0:
                        check()
                    if not remaining and mask:
                        end = clock + problem.travel(at, site) + policy.e2_return_unload_time
                        has_pickups = mask >> n
                        if (end + policy.e2_turnaround_time <= policy.cycle_end
                                and (not has_pickups or end <= policy.batches[0].cutoff)):
                            length = distance + problem.distance(at, site)
                            if mask not in columns or length < columns[mask]['distance']:
                                columns[mask] = dict(mask=mask, distance=length, satellite=site, sequence=tuple(path))
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
                        path.append(i)
                        walk(order.customer, arrive + order.service_time, mask | (1 << i),
                             remaining ^ (1 << i), weight - dw, volume - dv,
                             distance + problem.distance(at, order.customer))
                        path.pop()
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
                        path.append(n+i)
                        walk(order.customer, arrive + order.service_time, mask | bit, remaining,
                             weight + pw, volume + pv,
                             distance + problem.distance(at, order.customer))
                        path.pop()

                start = policy.cycle_start
                if chosen:
                    start = max(start, parameters.bounds['supply'][site][0]
                                + policy.e2_dispatch_prep_time)
                walk(site, start, 0, dm, weight0, volume0, 0.)
    check()
    return list(columns.values()), prefixes


def e1_distance_bound(problem, *, deadline):
    """Connected-tour bound, with an optional time-bounded Held-Karp bound.

    The undirected MST uses min(d(a,b),d(b,a)). Every union of depot tours
    visiting all active sites is connected and pays at least this tree cost,
    including on nonmetric inputs. An unfinished closure/DP is discarded.
    """
    sites = sorted({order.satellite for order in problem.orders})
    if not sites:
        return 0.
    remaining, reached, tree = set(sites), {problem.depot}, 0.
    while remaining:
        if time.perf_counter() >= deadline:
            return 0.
        cost, selected = min((min(problem.distance(a, b), problem.distance(b, a)), b)
                             for a in reached for b in remaining)
        tree += cost
        reached.add(selected)
        remaining.remove(selected)
    basic = max(tree, min(problem.distance(problem.depot, s) for s in sites)
                     + min(problem.distance(s, problem.depot) for s in sites))
    nodes = list(problem.network.nodes)
    distances = {(a, b): problem.distance(a, b) for a in nodes for b in nodes}
    for via in nodes:
        for a in nodes:
            if time.perf_counter() >= deadline:
                return basic
            for b in nodes:
                distances[a, b] = min(distances[a, b], distances[a, via] + distances[via, b])
    dp = {(1 << i, i): distances[problem.depot, site] for i, site in enumerate(sites)}
    for mask in range(1, 1 << len(sites)):
        if time.perf_counter() >= deadline:
            return basic
        for j in range(len(sites)):
            if not mask & (1 << j):
                continue
            previous = mask ^ (1 << j)
            if previous:
                dp[mask, j] = min(dp[previous, i] + distances[sites[i], sites[j]]
                                  for i in range(len(sites)) if previous & (1 << i))
    return max(basic, min(dp[(1 << len(sites)) - 1, i] + distances[site, problem.depot]
                         for i, site in enumerate(sites)))


def partition_distance_bound(problem, site, columns, *, complete, deadline, threads=1, seed=42):
    """A partition lower bound requires a COMPLETE library for this site."""
    if not complete or not columns or time.perf_counter() >= deadline:
        return None, [], dict(reason='no_complete_partition_model')
    import gurobipy as gp
    relaxation = gp.Model('m3_input_route_partition_lower_bound')
    try:
        relaxation.Params.OutputFlag = 0
        relaxation.Params.Threads = threads
        relaxation.Params.Seed = seed
        relaxation.Params.MIPGap = 0
        relaxation.Params.MIPGapAbs = 0
        route = relaxation.addVars(len(columns), vtype=gp.GRB.BINARY, name='trip')
        n = len(problem.orders)
        for i, order in enumerate(problem.orders):
            if order.satellite != site:
                continue
            for task in (i, n+i):
                relaxation.addConstr(gp.quicksum(route[c] for c, col in enumerate(columns)
                                                if col['mask'] & (1 << task)) == 1)
        relaxation.setObjective(gp.quicksum(route[c]*col['distance'] for c, col in enumerate(columns)))
        relaxation.Params.TimeLimit = max(0., deadline-time.perf_counter())
        relaxation.optimize()
        info = dict(native_status=relaxation.Status, optimize_seconds=relaxation.Runtime,
                    work=relaxation.Work, nodes=relaxation.NodeCount)
        selected = [columns[c]['mask'] for c in route if route[c].X > .5] if relaxation.SolCount else []
        try:
            bound = float(relaxation.ObjBound)
        except (AttributeError, gp.GurobiError):
            return None, selected, info
        valid = math.isfinite(bound) and abs(bound) < 1e90 and relaxation.Status != gp.GRB.INFEASIBLE
        return bound if valid else None, selected, info
    finally:
        relaxation.dispose()


def strengthen(problem, model, context, *, deadline, threads=1, seed=42):
    """Compose valid site bounds at ANY size, with bounded optional enumeration.

    An unfinished warehouse contributes its polynomial arc-cover bound.
    Complete libraries at other warehouses remain useful. Partial columns
    can guide a new schedule, but never define a restricted-set lower bound.
    """
    from models.routing.model_3.exact.route_bounds import assignment_distance_bound, seed_columns
    begun = time.perf_counter()
    info = dict(applied=False, kind='input_only_decomposed_route_bounds',
                complete_library=False, historical_information_used=False, sites={})

    def finish(reason=None):
        if reason:
            info['reason'] = reason
        info['total_seconds'] = time.perf_counter()-begun
        return info

    if problem.model_number != 3 or problem.free_assignment or not problem.reusable or not problem.orders:
        return finish('outside_fixed_assignment_scope')
    if begun >= deadline:
        return finish('preparation_budget_exhausted')
    if getattr(problem, 'cost_objective', False):
        rates = problem.cost_profile.rates
        if any(value < 0 for value in rates.values() if isinstance(value, (int, float))):
            return finish('negative_cost_rate')
        e1_rate = rates['distance_scale_to_km']*rates['e1_distance_cost_per_km']
        e2_rate = rates['distance_scale_to_km']*rates['e2_distance_cost_per_km']
        constant = (rates['container_pool_cost_per_period']*problem.operating_policy.container_pool_size
                    + sum(problem.cost_profile.constant_cost(problem.total).values()))
    else:
        e1_rate = e2_rate = 1.
        constant = 0.
    first_distance = e1_distance_bound(problem, deadline=begun+(deadline-begun)*.1)
    sites = sorted({order.satellite for order in problem.orders},
                   key=lambda site: (sum(o.satellite == site for o in problem.orders), site))
    selected, second_distance, total_columns, prefixes = [], 0., 0, 0
    selected_sequences = {}
    completed = []
    for index, site in enumerate(sites):
        now = time.perf_counter()
        share = max(0., deadline-now)/(len(sites)-index)
        site_deadline = now+share
        lower, analytic = assignment_distance_bound(problem, context['parameters'], site,
            deadline=now+share*.2, threads=threads, seed=seed)
        columns, candidate, seed_info = seed_columns(problem, context['parameters'], site,
            deadline=time.perf_counter()+max(0., site_deadline-time.perf_counter())*.2)
        site_info = dict(assignment=analytic, seeds=seed_info, complete_library=False,
                         distance_lower_bound=lower, partition_bound_used=False)
        complete = False
        site_prefixes = 0
        if time.perf_counter() < site_deadline:
            try:
                columns, site_prefixes = enumerate_trips(problem, context['parameters'],
                    deadline=time.perf_counter()+max(0., site_deadline-time.perf_counter())*.65,
                    sites=[site], initial_columns=columns)
                complete = True
                completed.append(site)
            except TimeoutError as error:
                columns = getattr(error, 'columns', columns)
                site_prefixes = getattr(error, 'prefixes', 0)
                site_info['enumeration_reason'] = str(error)
        if complete:
            value, partition, evidence = partition_distance_bound(problem, site, columns,
                complete=True, deadline=site_deadline, threads=threads, seed=seed)
            site_info['partition'] = evidence
            if value is not None:
                lower = max(lower, value)
                site_info['partition_bound_used'] = True
            if partition:
                candidate = partition
        site_info.update(complete_library=complete, columns=len(columns), enumerated_prefixes=site_prefixes,
                         distance_lower_bound=lower)
        info['sites'][site] = site_info
        second_distance += lower
        total_columns += len(columns)
        prefixes += site_prefixes
        selected.extend(candidate)
        by_mask = {row['mask']: row for row in columns}
        for mask in candidate:
            if mask in by_mask and 'sequence' in by_mask[mask]:
                selected_sequences[str(mask)] = list(by_mask[mask]['sequence'])
    # Exactly one warehouse owns each task. Missing/overlapping candidate
    # groups are never sent to the scheduling pilot.
    covered = 0
    candidate_complete = True
    for mask in selected:
        if mask <= 0 or covered & mask:
            candidate_complete = False
        covered |= mask
    candidate_complete = candidate_complete and covered == (1 << (2*len(problem.orders)))-1
    bound = constant+e1_rate*first_distance+e2_rate*second_distance
    floor = math.nextafter(bound, -math.inf)
    model.addConstr(model.getObjective() >= floor, name='input_route_partition_cost_floor')
    model.update()
    info.update(applied=True, complete_library=len(completed) == len(sites), completed_sites=completed,
        columns=total_columns, enumerated_prefixes=prefixes,
        selected_trip_masks=selected if candidate_complete else [], candidate_complete=candidate_complete,
        selected_trip_sequences=selected_sequences if candidate_complete else {},
        lower_bound=bound, objective_floor=floor, e1_distance_lower_bound=first_distance,
        e2_distance_lower_bound=second_distance)
    return finish()


def prepare_start(problem, model, context, masks, *, deadline, threads=1, seed=42, sequences=None):
    """Find a fresh schedule in a copy restricted to one relaxed partition.

    Equal visits keep each task set on one trip; distinct sets use distinct
    trips. First try a deterministic vehicle allocation, then release that
    allocation if needed. All such restrictions stay in the copy. Only the
    complete unrestricted main model can certify global optimality.
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
        pilot.update()
        order_index = {o.id: i for i, o in enumerate(orders)}
        allocation = {}
        available_routes = {(r['satellite'], r['vehicle'], r['slot']) for r in context['e2']}
        allocation_valid = True
        for site, partitions in groups.items():
            vehicles = sorted({r['vehicle'] for r in context['e2'] if r['satellite'] == site})
            ranked = sorted(partitions, key=lambda keys: min(context['parameters'].task_windows[key][0] for key in keys))
            for i, keys in enumerate(ranked):
                owner = site, vehicles[i % len(vehicles)], i // len(vehicles)
                if owner not in available_routes:
                    allocation_valid = False
                    break
                allocation[owner] = set(keys)
        saved = {}
        if allocation_valid:
            for route in context['e2']:
                keys = allocation.get((route['satellite'], route['vehicle'], route['slot']), set())
                fixed = [(copy_vars[route['used'].index], int(bool(keys)))]
                fixed += [(copy_vars[route['visit'][i].index], int((node['order'].id, node['kind']) in keys))
                          for i, node in enumerate(route['nodes'])]
                for variable, value in fixed:
                    saved[variable.index] = variable.LB, variable.UB
                    if value < variable.LB or value > variable.UB:
                        allocation_valid = False
                    variable.LB = variable.UB = value
        path_allocation = False
        if allocation_valid and sequences:
            all_edges = {variable.index for route in context['e2'] for variable in route['x'].values()}
            chosen_edges = set()
            path_allocation = True
            for route in context['e2']:
                keys = allocation.get((route['satellite'], route['vehicle'], route['slot']))
                if not keys:
                    continue
                mask = sum(1 << (order_index[key[0]]+(n if key[1] == 'p' else 0)) for key in keys)
                sequence = sequences.get(str(mask), ())
                if not sequence or sum(1 << bit for bit in sequence) != mask:
                    path_allocation = False
                    break
                local = {(node['order'].id, node['kind']): i for i, node in enumerate(route['nodes'])}
                visits = [local[orders[bit % n].id, 'd' if bit < n else 'p'] for bit in sequence]
                edges = list(zip([-1]+visits, visits+[-2]))
                if any(edge not in route['x'] for edge in edges):
                    path_allocation = False
                    break
                chosen_edges.update(route['x'][edge].index for edge in edges)
            if path_allocation:
                for index in all_edges:
                    variable, value = copy_vars[index], int(index in chosen_edges)
                    saved[index] = variable.LB, variable.UB
                    if value < variable.LB or value > variable.UB:
                        allocation_valid = False
                    variable.LB = variable.UB = value
        attempts = []

        def attempt(scope, seconds):
            pilot.Params.TimeLimit = max(0., seconds)
            pilot.optimize()
            attempts.append(dict(scope=scope, native_status=pilot.Status, seconds=pilot.Runtime,
                                 work=pilot.Work, nodes=pilot.NodeCount))

        if allocation_valid and time.perf_counter() < deadline:
            attempt('generated_vehicle_and_path_allocation' if path_allocation else 'generated_vehicle_allocation',
                    (deadline-time.perf_counter())*.5)
        if not allocation_valid or (not pilot.SolCount and time.perf_counter() < deadline):
            for index, (lower, upper) in saved.items():
                copy_vars[index].LB, copy_vars[index].UB = lower, upper
            pilot.reset()
            attempt('free_vehicle_partition', deadline-time.perf_counter())
        if not attempts:
            return dict(info, total_seconds=time.perf_counter()-begun, reason='pilot_budget_exhausted')
        info.update(native_status=pilot.Status, optimize_seconds=sum(a['seconds'] for a in attempts),
                    work=sum(a['work'] for a in attempts), nodes=sum(a['nodes'] for a in attempts),
                    attempts=attempts)
        if pilot.SolCount:
            original_vars = model.getVars()
            assert len(original_vars) == len(copy_vars)
            model.setAttr('Start', original_vars, pilot.getAttr('X', copy_vars))
            info.update(supplied=True, objective=pilot.ObjVal)
        info['total_seconds'] = time.perf_counter()-begun
        return info
    finally:
        pilot.dispose()
