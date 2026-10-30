"""Bounded complete-plan moves ranked by audited total cost, without a solver."""
from itertools import combinations
import math
import time

from models.routing.model_8.alns.evaluator import evaluate
from models.routing.model_8.alns.neighborhoods import _goods_ready, _route_score
from models.routing.model_8.alns.state import clone_joint_state, state_key


def _key(state):
    return state_key(state), tuple(sorted(state['cutoffs'].items()))


def _physical(child, evaluator, assignment, state):
    fixed = evaluator.fixed(assignment, state['cutoffs'])
    _, raw, _ = evaluate(fixed, state)
    return raw


def _profiles(child, raw, old):
    """All bounded productive splits of this schedule's distinct return events.

    These are proposals, not restrictions on the physical search domain.
    Full audit still checks vehicles, E1 routes, cleaning and cyclic inventory.
    """
    batches = child.operating_policy.batches
    events = sorted({trip['return_complete'] for trip in raw['e2_trips']
                     if any(event['kind'] == 'p' for event in trip['events'])})
    k = len(batches)
    if len(events) < k:
        return []
    delivery_floor = {b.id: b.start for b in batches}
    for trip in raw['e1_trips']:
        for event in trip['events']:
            if event['kind'] == 'd':
                delivery_floor[trip['batch_id']] = max(delivery_floor[trip['batch_id']],
                    event['start']+child.service(event['site_id']))
    if k == 1:
        delivery_floor[batches[0].id] = max(delivery_floor[batches[0].id],
            max((event['start']+child.order_map[event['order_id']].service_time
                 for trip in raw['e2_trips'] for event in trip['events'] if event['kind'] == 'd'),
                default=batches[0].start))
    step = child.calendar_decision_step
    rows = []
    for cuts in combinations(range(1, len(events)), k-1):
        ends = cuts+(len(events),)
        proposed = {}
        for batch, end in zip(batches, ends):
            lo, hi = child.cutoff_bounds[batch.id]
            required = max(lo, events[end-1], delivery_floor[batch.id])
            selected = -(-required//step)*step
            if selected > hi or (end < len(events) and selected >= events[end]):
                break
            proposed[batch.id] = selected
        if len(proposed) == k:
            rows.append(proposed)
    return sorted(rows, key=lambda row: (sum(abs(row[bid]-old[bid]) for bid in row), tuple(row.values())))


def _chain_shapes(child, state, raw, deadline, max_shapes=10):
    original = clone_joint_state(state)
    shapes = [original]
    for bid, rows in state['deliveries'].items():
        for vehicle, sites in enumerate(rows):
            if len(sites) > 1 and len(shapes) < 3:
                trial = clone_joint_state(state)
                trial['deliveries'][bid][vehicle] = list(reversed(sites))
                shapes.append(trial)
    if time.perf_counter() >= deadline:
        return shapes
    batches = child.operating_policy.batches
    plans = [i for i, route in enumerate(state['e2']) if route['tasks']]
    if len(plans) != len(raw['e2_trips']):
        return shapes
    groups = {}
    for index, trip in zip(plans, raw['e2_trips']):
        if not any(event['kind'] == 'p' for event in trip['events']):
            continue
        batch_index = next((i for i, batch in enumerate(batches)
                            if trip['return_complete'] <= state['cutoffs'][batch.id]), None)
        if batch_index is not None and batch_index < len(batches)-1:
            groups.setdefault((batch_index, trip['satellite_id']), []).append((index, trip['return_complete']))
    for (batch_index, site), members in sorted(groups.items()):
        if time.perf_counter() >= deadline or len(shapes) >= max_shapes:
            break
        last_return = max(value for _, value in members)
        other_returns = sorted({trip['return_complete']+1 for trip in raw['e2_trips']
            if trip['satellite_id'] != site and trip['return_complete'] >= last_return
            and any(event['kind'] == 'p' for event in trip['events'])})
        targets = [state['cutoffs'][batches[batch_index].id]+1]+other_returns[:1]
        for target in dict.fromkeys(targets):
            if target > child.cutoff_bounds[batches[-1].id][1] or len(shapes) >= max_shapes:
                continue
            trial = clone_joint_state(state)
            for index, _ in members:
                route = trial['e2'][index]
                route['return_after'] = max(target, route.get('return_after', 0))
            shapes.append(trial)
            for vehicle, sites in enumerate(trial['deliveries'][batches[batch_index].id]):
                if len(sites) > 1 and len(shapes) < max_shapes:
                    paired = clone_joint_state(trial)
                    paired['deliveries'][batches[batch_index].id][vehicle] = list(reversed(sites))
                    shapes.append(paired)
    return shapes


def recovery_frontier(child, evaluator, assignment, state, deadline, max_evaluations=96):
    """Move the entire cutoff profile with E1 prefixes and return waits."""
    best = clone_joint_state(state)
    score, result, _ = evaluator(assignment, best)
    best_cost = score if result is not None else math.inf
    evaluations = improvements = 0
    if (len(child.orders) > 9 or not child.optimize_cutoffs or child.shared_cutoff
            or time.perf_counter() >= deadline):
        return best, evaluations, improvements
    raw = _physical(child, evaluator, assignment, state)
    if not raw['e2_trips']:
        return best, evaluations, improvements
    iterators, seen = [], {_key(state)}
    for shape in _chain_shapes(child, state, raw, deadline):
        if time.perf_counter() >= deadline:
            break
        physical = raw if shape == state else _physical(child, evaluator, assignment, shape)
        candidates = [dict(shape['cutoffs'])]+_profiles(child, physical, shape['cutoffs'])
        iterators.append((shape, iter(candidates)))
    while iterators and evaluations < max_evaluations and time.perf_counter() < deadline:
        remaining = []
        for shape, profiles in iterators:
            if evaluations >= max_evaluations or time.perf_counter() >= deadline:
                break
            try:
                cutoffs = next(profiles)
            except StopIteration:
                continue
            remaining.append((shape, profiles))
            trial = clone_joint_state(shape)
            trial['cutoffs'] = cutoffs
            key = _key(trial)
            if key in seen:
                continue
            seen.add(key)
            value, candidate, _ = evaluator(assignment, trial)
            evaluations += 1
            if candidate is not None and value < best_cost-1e-8:
                best, best_cost = trial, value
                improvements += 1
        iterators = remaining
    return best, evaluations, improvements


def _distance(problem, routes):
    value = 0.
    for route in routes:
        nodes = [route['satellite']]+[problem.order_map[o].customer for o, _ in route['tasks']]+[route['satellite']]
        value += sum(problem.distance(a, b) for a, b in zip(nodes, nodes[1:]))
    return value


def _insertions(problem, routes, task):
    oid, kind = task
    order = problem.order_map[oid]
    delivery = next(((i, row['vehicle'], row['tasks'].index((oid, 'd')))
                     for i, row in enumerate(routes) if (oid, 'd') in row['tasks']), None)
    options = []
    for index, row in enumerate(routes):
        if row['satellite'] != order.satellite:
            continue
        if kind == 'd' and any(k == 'd' and problem.order_map[o].batch != order.batch for o, k in row['tasks']):
            continue
        if kind == 'p' and delivery is not None and row['vehicle'] == delivery[1] and index < delivery[0]:
            continue
        for pos in range(len(row['tasks'])+1):
            if kind == 'p' and delivery is not None and index == delivery[0] and pos <= delivery[2]:
                continue
            changed = [{**route, 'tasks': list(route['tasks'])} for route in routes]
            changed[index]['tasks'].insert(pos, task)
            changed[index].pop('depart_after', None)
            changed[index].pop('return_after', None)
            options.append(changed)
    for vehicle in range(problem.fleet.e2_per_sat_count):
        used = sum(row['satellite'] == order.satellite and row['vehicle'] == vehicle for row in routes)
        if used >= problem.operating_policy.e2_max_trips_per_vehicle:
            continue
        window = order.delivery_window if kind == 'd' else order.pickup_window
        pos = next((i for i, row in enumerate(routes) if row['satellite'] == order.satellite and row['vehicle'] == vehicle
                    and min((problem.order_map[o].delivery_window if k == 'd' else problem.order_map[o].pickup_window)[0]
                            for o, k in row['tasks']) > window[0]), len(routes))
        if kind == 'p' and delivery is not None and vehicle == delivery[1]:
            pos = max(pos, delivery[0]+1)
        changed = [{**route, 'tasks': list(route['tasks'])} for route in routes]
        changed.insert(pos, dict(satellite=order.satellite, vehicle=vehicle, tasks=[task]))
        options.append(changed)
    return sorted(options, key=lambda rows: _distance(problem, rows))


def _merge_candidates(problem, state, deadline):
    """Offer whole-trip joins that need no feasible single-task intermediate."""
    rows, ranked = state['e2'], []
    goods = _goods_ready(problem, state)
    for i, left in enumerate(rows):
        for j in range(i+1, len(rows)):
            if time.perf_counter() >= deadline:
                return [row[3] for row in sorted(ranked, key=lambda row: row[:3])]
            right = rows[j]
            if left['satellite'] != right['satellite']:
                continue
            tasks = left['tasks']+right['tasks']
            if len({problem.order_map[oid].batch for oid, kind in tasks if kind == 'd'}) > 1:
                continue
            sequences = {tuple(tasks), tuple(right['tasks']+left['tasks']),
                         tuple([task for task in tasks if task[1] == 'd']+[task for task in tasks if task[1] == 'p'])}
            for sequence in sequences:
                for vehicle in sorted({left['vehicle'], right['vehicle']}):
                    trial = clone_joint_state(state)
                    trial['e2'] = [row for index, row in enumerate(trial['e2']) if index not in (i, j)]
                    merged = dict(satellite=left['satellite'], vehicle=vehicle, tasks=list(sequence))
                    trial['e2'].insert(i, merged)
                    local_value, _ = _route_score(problem, merged, goods, problem.operating_policy.cycle_start,
                                                  supply_aware=True)
                    nodes = [left['satellite']]+[problem.order_map[oid].customer for oid, _ in sequence]+[left['satellite']]
                    distance = sum(problem.distance(a, b) for a, b in zip(nodes, nodes[1:]))
                    ranked.append((max(0., local_value-distance), _distance(problem, trial['e2']), repr(_key(trial)), trial))
    return [row[3] for row in sorted(ranked, key=lambda row: row[:3])]


def complete_request_repair(child, evaluator, assignment, state, order_ids, deadline, max_evaluations=72):
    """Insert D/P independently, but compare only complete audited schedules."""
    best = clone_joint_state(state)
    value, result, _ = evaluator(assignment, best)
    best_cost = value if result is not None else math.inf
    evaluations = improvements = 0
    if len(child.orders) > 9 or time.perf_counter() >= deadline:
        return best, evaluations, improvements
    fixed = evaluator.fixed(assignment, state['cutoffs'])
    seen = {_key(best)}
    for trial in _merge_candidates(fixed, state, deadline)[:min(12, max_evaluations//4)]:
        if evaluations >= max_evaluations or time.perf_counter() >= deadline:
            break
        key = _key(trial)
        if key in seen:
            continue
        seen.add(key)
        score, candidate, _ = evaluator(assignment, trial)
        evaluations += 1
        if candidate is not None and score < best_cost-1e-8:
            best, best_cost = trial, score
            improvements += 1
    for number, oid in enumerate(order_ids):
        if time.perf_counter() >= deadline or evaluations >= max_evaluations:
            break
        trial_base = clone_joint_state(best)
        for route in trial_base['e2']:
            tasks = [task for task in route['tasks'] if task[0] != oid]
            if tasks != route['tasks']:
                route.pop('depart_after', None)
                route.pop('return_after', None)
            route['tasks'] = tasks
        trial_base['e2'] = [row for row in trial_base['e2'] if row['tasks']]
        deliveries = _insertions(fixed, trial_base['e2'], (oid, 'd'))
        # Keep independent new-trip alternatives even when they add distance.
        deliveries = deliveries[:6]+[rows for rows in deliveries[6:]
                                     if any(row['tasks'] == [(oid, 'd')] for row in rows)][:2]
        streams = [iter(_insertions(fixed, rows, (oid, 'p'))) for rows in deliveries]
        allowance = evaluations+max(1, (max_evaluations-evaluations)//max(1, len(order_ids)-number))
        while streams and evaluations < allowance and time.perf_counter() < deadline:
            pending = []
            for stream in streams:
                if evaluations >= allowance or time.perf_counter() >= deadline:
                    break
                try:
                    rows = next(stream)
                except StopIteration:
                    continue
                pending.append(stream)
                trial = clone_joint_state(trial_base)
                trial['e2'] = rows
                key = _key(trial)
                if key in seen:
                    continue
                seen.add(key)
                score, candidate, _ = evaluator(assignment, trial)
                evaluations += 1
                if candidate is not None and score < best_cost-1e-8:
                    best, best_cost = trial, score
                    improvements += 1
            streams = pending
    return best, evaluations, improvements
