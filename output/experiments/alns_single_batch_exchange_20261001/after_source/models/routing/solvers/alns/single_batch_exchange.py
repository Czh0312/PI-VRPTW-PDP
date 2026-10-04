"""Bounded whole-request exchange proposals for fixed single-batch recovery.

Distance and event-load checks only screen proposals. The full decoder and
independent audit still decide whether their schedules are feasible.
"""
from collections import Counter
import time

from .neighborhoods import clone_state


def exchange_pairs(problem, original, rng, deadline, *, samples=8,
                   distance=None, task=None):
    if not problem.reusable or len(problem.operating_policy.batches) != 1:
        raise ValueError('Paired exchange requires reusable B=1 requests')
    state = clone_state(original)
    if time.perf_counter() >= deadline:
        return state
    distance = problem.distance if distance is None else distance
    task = problem.task if task is None else task
    routes = original['e2']
    counts = Counter(t for route in routes for t in route['tasks'])
    groups = {}
    for index, route in enumerate(routes):
        positions = {t: i for i, t in enumerate(route['tasks'])}
        pairs = [oid for oid, kind in route['tasks'] if kind == 'd'
                 and counts[oid, 'd'] == counts[oid, 'p'] == 1
                 and (oid, 'p') in positions
                 and positions[oid, 'd'] < positions[oid, 'p']
                 and problem.order_map[oid].satellite == route['satellite']]
        if pairs:
            groups.setdefault(route['satellite'], []).append((index, pairs))
    groups = [rows for rows in groups.values() if len(rows) > 1]
    if not groups:
        return state

    def route_distance(route, tasks):
        previous, total = route['satellite'], 0.0
        for oid, _ in tasks:
            customer = problem.order_map[oid].customer
            total += distance(previous, customer)
            previous = customer
        return total+distance(previous, route['satellite'])

    def within_capacity(tasks):
        prepared = [(kind, *task(oid, kind)[1:]) for oid, kind in tasks]
        weight = sum(w for kind, w, _ in prepared if kind == 'd')
        volume = sum(v for kind, _, v in prepared if kind == 'd')
        weight_cap, volume_cap = problem.fleet.e2_weight_cap, problem.fleet.e2_volume_cap
        if weight > weight_cap+1e-6 or volume > volume_cap+1e-6:
            return False
        for kind, w, v in prepared:
            weight += w if kind == 'p' else -w
            volume += v if kind == 'p' else -v
            if min(weight, volume) < -1e-6 or weight > weight_cap+1e-6 or volume > volume_cap+1e-6:
                return False
        return True

    seen, before = set(), {}
    best, best_gain = None, 1e-8
    for _ in range(samples):
        if time.perf_counter() >= deadline:
            break
        (a, a_pairs), (b, b_pairs) = rng.sample(rng.choice(groups), 2)
        x, y = rng.choice(a_pairs), rng.choice(b_pairs)
        key = (a, x, b, y) if a < b else (b, y, a, x)
        if key in seen:
            continue
        seen.add(key)
        left = [(y if oid == x else oid, kind) for oid, kind in routes[a]['tasks']]
        right = [(x if oid == y else oid, kind) for oid, kind in routes[b]['tasks']]
        if not within_capacity(left) or not within_capacity(right):
            continue
        for index in (a, b):
            if index not in before:
                before[index] = route_distance(routes[index], routes[index]['tasks'])
        gain = before[a]+before[b]-route_distance(routes[a], left)-route_distance(routes[b], right)
        if gain > best_gain:
            best, best_gain = (a, b, left, right), gain
    if best is not None:
        a, b, left, right = best
        state['e2'][a]['tasks'], state['e2'][b]['tasks'] = left, right
    return state
