"""Request removal and greedy time/capacity-aware reinsertion for cycle ALNS.

Local scores rank proposals only. The complete decoder and independent audit
remain the authorities; no local estimate proves infeasibility or optimality.
"""
from collections import Counter, OrderedDict
import time


def clone_state(state):
    return dict(deliveries={b: [list(r) for r in rows] for b, rows in state['deliveries'].items()},
                pickup_order={b: list(row) for b, row in state['pickup_order'].items()},
                pickup_vehicle={b: dict(row) for b, row in state['pickup_vehicle'].items()},
                e2=[{**r, 'tasks': list(r['tasks'])} for r in state['e2']])


def state_key(state):
    return (tuple((b, tuple(map(tuple, rows))) for b, rows in state['deliveries'].items()),
            tuple((b, tuple(row)) for b, row in state['pickup_order'].items()),
            tuple((b, tuple(sorted(row.items()))) for b, row in state['pickup_vehicle'].items()),
            tuple((r['satellite'], r['vehicle'], tuple(r['tasks']),
                   r.get('depart_after'), r.get('return_after')) for r in state['e2']))


class EvaluationCache:
    def __init__(self, problem, evaluator, capacity=256):
        self.problem, self.evaluator, self.capacity = problem, evaluator, capacity
        self.entries = OrderedDict()
        self.hits = self.misses = 0

    def __call__(self, state):
        key = state_key(state)
        if key in self.entries:
            self.hits += 1
            self.entries.move_to_end(key)
            return self.entries[key]
        self.misses += 1
        value = self.evaluator(self.problem, state)
        self.entries[key] = value
        if len(self.entries) > self.capacity:
            self.entries.popitem(last=False)
        return value


def _goods_ready(problem, state):
    ready = {}
    for b in problem.operating_policy.batches:
        for sites in state['deliveries'][b.id]:
            current, previous = b.start+problem.operating_policy.e1_load_time, problem.depot
            for site in sites:
                current += problem.travel(previous, site)+problem.service(site)
                ready[site, b.id] = current
                previous = site
    return ready


def _route_score(problem, route, goods, vehicle_ready):
    p, f = problem.operating_policy, problem.fleet
    tasks, site = route['tasks'], route['satellite']
    if not tasks:
        return 0.0, vehicle_ready
    dbatches = {problem.order_map[oid].batch for oid, kind in tasks if kind == 'd'}
    current = max(p.cycle_start, vehicle_ready, route.get('depart_after', p.cycle_start))
    if dbatches:
        current = max(current, *(goods[site, b] for b in dbatches))+p.e2_dispatch_prep_time
    weight = sum(problem.task(oid, kind)[1] for oid, kind in tasks if kind == 'd')
    volume = sum(problem.task(oid, kind)[2] for oid, kind in tasks if kind == 'd')
    load_penalty = max(0, weight-f.e2_weight_cap)/max(1, f.e2_weight_cap)
    load_penalty += max(0, volume-f.e2_volume_cap)/max(1, f.e2_volume_cap)
    distance, late, delivered, previous = 0.0, 0, {}, site
    for oid, kind in tasks:
        order, w, vol = problem.task(oid, kind)
        window = order.delivery_window if kind == 'd' else order.pickup_window
        current = max(current+problem.travel(previous, order.customer), window[0])
        if kind == 'p':
            current = max(current, delivered.get(oid, order.delivery_window[0]+order.service_time)+order.min_use_time)
        due = window[1]
        if kind == 'd' and len(p.batches) == 1:
            due = min(due, p.batches[0].cutoff-order.service_time)
        late += max(0, current-due)
        current += order.service_time
        if kind == 'd':
            delivered[oid] = current
        distance += problem.distance(previous, order.customer)
        previous = order.customer
        weight += w if kind == 'p' else -w
        volume += vol if kind == 'p' else -vol
        load_penalty += max(0, weight-f.e2_weight_cap)/max(1, f.e2_weight_cap)
        load_penalty += max(0, volume-f.e2_volume_cap)/max(1, f.e2_volume_cap)
    completed = max(current+problem.travel(previous, site)+p.e2_return_unload_time,
                    route.get('return_after', p.cycle_start))
    late += max(0, completed+p.e2_turnaround_time-p.cycle_end)
    if any(k == 'p' for _, k in tasks):
        late += max(0, completed-p.batches[-1].cutoff)
    distance += problem.distance(previous, site)
    return distance+1e6*(load_penalty+max(0, len(dbatches)-1))+late*p.time_tick_seconds/60*1e4, completed


def destroy_repair(problem, original, rng, mode, deadline, *, scorer=None):
    if not problem.orders:
        return clone_state(original)
    orders = list(problem.orders)
    count = min(len(orders), max(2, min(5, len(orders)//5)))
    if mode == 'related':
        anchor = rng.choice(orders)
        orders.sort(key=lambda o: (o.satellite != anchor.satellite,
                                  problem.distance(anchor.customer, o.customer)+
                                  abs(o.delivery_window[0]-anchor.delivery_window[0])*.01))
        removed = orders[:count]
    else:
        removed = rng.sample(orders, count)
    repaired = repair_orders(problem, original, removed, deadline, scorer=scorer,
                             order_rng=rng if mode == 'shuffled' else None)
    return repaired if repaired is not None else clone_state(original)


def repair_orders(problem, original, removed, deadline, *, screen=True, cache_scores=True,
                  fast_scores=True, scorer=None, order_rng=None):
    """Reinsert complete requests; interruption never exposes a partial state.

    The distance-only bound safely screens this *local ranking score* because
    all its time/capacity penalties are nonnegative, including nonmetric arcs.
    It does not screen the global feasible set or certify infeasibility.
    """
    state = clone_state(original)
    ids = {o.id for o in removed}
    for route in state['e2']:
        route['tasks'] = [t for t in route['tasks'] if t[0] not in ids]
    state['e2'] = [r for r in state['e2'] if r['tasks']]
    goods = _goods_ready(problem, state)
    p, f = problem.operating_policy, problem.fleet
    # Deliveries are inserted first; each pickup may then join the same route
    # at a later position or a different vehicle/trip.
    tasks = []
    for kind in problem.task_kinds:
        ordered = sorted(removed, key=lambda o: (o.delivery_window if kind == 'd' else o.pickup_window)[1])
        if order_rng is not None:
            order_rng.shuffle(ordered)
        tasks.extend((o.id, kind) for o in ordered)
    score_route = _route_score
    if tasks and fast_scores and len(p.batches) == 1:
        from .single_batch_scoring import SingleBatchRouteScorer
        score_route = scorer if scorer is not None else SingleBatchRouteScorer(problem)
    # Scoped to this repair: goods-ready times and the problem stay fixed.
    # Keep the route object alive with its score to prevent object-id reuse.
    route_scores = {}
    for task in tasks:
        if time.perf_counter() >= deadline:
            return None
        oid, kind = task
        order, routes = problem.order_map[oid], state['e2']
        ready, before, scores, distances = {}, {}, {}, {}
        counts = Counter((r['satellite'], r['vehicle']) for r in routes)
        for index, route in enumerate(routes):
            # Other satellites cannot share this physical vehicle or supply.
            if route['satellite'] != order.satellite:
                continue
            key = route['satellite'], route['vehicle']
            before[index] = ready.get(key, p.cycle_start)
            saved = route_scores.get(id(route)) if cache_scores else None
            if saved is not None and saved[0] is route and saved[1] == before[index]:
                _, _, scores[index], end, distances[index] = saved
            else:
                scores[index], end = score_route(problem, route, goods, before[index])
                locations = [route['satellite']]+[problem.order_map[o].customer for o, _ in route['tasks']]+[route['satellite']]
                distances[index] = sum(problem.distance(a, b) for a, b in zip(locations, locations[1:]))
                if cache_scores:
                    route_scores[id(route)] = (route, before[index], scores[index], end, distances[index])
            ready[key] = end+p.e2_turnaround_time
        best = None
        for index, route in enumerate(routes):
            if route['satellite'] != order.satellite:
                continue
            existing = route['tasks']
            if kind == 'd' and any(k == 'd' and problem.order_map[i].batch != order.batch for i, k in existing):
                continue
            for pos in range(len(existing)+1):
                if time.perf_counter() >= deadline:
                    return None
                if kind == 'd' and (oid, 'p') in existing[:pos]:
                    continue
                if kind == 'p' and (oid, 'd') in existing[pos:]:
                    continue
                if screen and best is not None:
                    left = route['satellite'] if pos == 0 else problem.order_map[existing[pos-1][0]].customer
                    right = route['satellite'] if pos == len(existing) else problem.order_map[existing[pos][0]].customer
                    bound = (distances[index]+problem.distance(left, order.customer)
                             +problem.distance(order.customer, right)-problem.distance(left, right)-scores[index])
                    tolerance = 1e-9*max(1, abs(bound), abs(best[0]), abs(scores[index]))
                    if bound > best[0]+tolerance:
                        continue
                candidate = {**route, 'tasks': existing[:pos]+[task]+existing[pos:]}
                score, _ = score_route(problem, candidate, goods, before[index])
                option = (score-scores[index], index, candidate, False)
                if best is None or option[0] < best[0]:
                    best = option
        for vehicle in range(f.e2_per_sat_count):
            if counts[order.satellite, vehicle] >= p.e2_max_trips_per_vehicle:
                continue
            candidate = dict(satellite=order.satellite, vehicle=vehicle, tasks=[task])
            window = order.delivery_window if kind == 'd' else order.pickup_window
            pos = next((i for i, route in enumerate(routes)
                        if route['satellite'] == order.satellite and route['vehicle'] == vehicle
                        and min((problem.order_map[o].delivery_window if k == 'd' else
                                 problem.order_map[o].pickup_window)[0] for o, k in route['tasks']) > window[0]), len(routes))
            begin = before[pos] if pos < len(routes) else ready.get((order.satellite, vehicle), p.cycle_start)
            score, _ = score_route(problem, candidate, goods, begin)
            if best is None or score < best[0]:
                best = (score, pos, candidate, True)
        if best is None:
            return None
        _, index, candidate, new_route = best
        if new_route:
            routes.insert(index, candidate)
        else:
            routes[index] = candidate
    return state


def state_from_result(problem, result):
    state = dict(deliveries={b.id: [[] for _ in range(problem.fleet.e1_count)] for b in problem.operating_policy.batches},
                 pickup_order={b.id: [] for b in problem.operating_policy.batches},
                 pickup_vehicle={b.id: {} for b in problem.operating_policy.batches}, e2=[])
    for trip in result['e1_trips']:
        batch, vehicle = trip['batch_id'], trip['vehicle_id']
        for event in trip['events']:
            site = event['site_id']
            if event['kind'] == 'd':
                state['deliveries'][batch][vehicle].append(site)
            else:
                state['pickup_order'][batch].append(site)
                state['pickup_vehicle'][batch][site] = vehicle
    for sites in state['pickup_order'].values():
        sites.extend(s for s in problem.satellites if s not in sites)
    for trip in sorted(result['e2_trips'], key=lambda r: (r['trip_index'], r['satellite_id'], r['vehicle_id'])):
        state['e2'].append(dict(satellite=trip['satellite_id'], vehicle=trip['vehicle_id'],
                               tasks=[(e['order_id'], e['kind']) for e in trip['events']]))
        if len(problem.operating_policy.batches) > 1:
            state['e2'][-1].update(depart_after=trip['departure'], return_after=trip['return_complete'])
    return state
