"""Bounded regret repair for M5; local costs never certify cycle feasibility.

Delivery tasks precede pickup tasks. Within each phase, insert the task whose
second-best *route* is most expensive relative to its best route. The decoder
and independent incumbent audit still check the resulting complete schedule.
"""
from collections import Counter
import time

from .neighborhoods import clone_state, _goods_ready, _route_score, destroy_repair


class RegretRepair:
    def __init__(self, problem, *, related_only=True, cache_options=True, regret_pickups=True):
        if len(problem.operating_policy.batches) < 2:
            raise ValueError('RegretRepair is a multi-batch operator')
        self.problem, self.related_only = problem, related_only
        self.cache_options = cache_options
        self.regret_pickups = regret_pickups
        self.calls = self.completed = self.interrupted = self.reordered = 0

    def __call__(self, problem, original, rng, mode, deadline):
        if self.related_only and mode != 'related':
            return destroy_repair(problem, original, rng, mode, deadline)
        self.calls += 1
        orders = list(problem.orders)
        if not orders:
            return clone_state(original)
        count = min(len(orders), max(2, min(5, len(orders)//5)))
        if mode == 'related':
            anchor = rng.choice(orders)
            orders.sort(key=lambda o: (o.satellite != anchor.satellite,
                problem.distance(anchor.customer, o.customer)
                +abs(o.delivery_window[0]-anchor.delivery_window[0])*.01))
            removed = orders[:count]
        else:
            removed = rng.sample(orders, count)
        repaired = self.repair(original, removed, deadline)
        if repaired is None:
            self.interrupted += 1
            return clone_state(original)
        self.completed += 1
        return repaired

    def repair(self, original, removed, deadline):
        problem, p = self.problem, self.problem.operating_policy
        state = clone_state(original)
        ids = {o.id for o in removed}
        for route in state['e2']:
            route['tasks'] = [t for t in route['tasks'] if t[0] not in ids]
        state['e2'] = [r for r in state['e2'] if r['tasks']]
        goods = _goods_ready(problem, state)
        # Scores are reused only with the complete route and readiness bound.
        # Goods and problem remain fixed for the duration of this repair.
        scores = {}
        route_options = {}

        def score(route, ready):
            key = (route['satellite'], route['vehicle'], tuple(route['tasks']),
                   route.get('depart_after'), route.get('return_after'), ready)
            if key not in scores:
                scores[key] = _route_score(problem, route, goods, ready)
            return scores[key]

        for kind in problem.task_kinds:
            pending = sorted(removed, key=lambda o:
                (o.delivery_window if kind == 'd' else o.pickup_window)[1])
            while pending:
                if time.perf_counter() >= deadline:
                    return None
                routes = state['e2']
                ready, before, old_scores = {}, {}, {}
                counts = Counter((r['satellite'], r['vehicle']) for r in routes)
                sites = {o.satellite for o in pending}
                for i, route in enumerate(routes):
                    if route['satellite'] not in sites:
                        continue
                    vehicle = route['satellite'], route['vehicle']
                    before[i] = ready.get(vehicle, p.cycle_start)
                    old_scores[i], end = score(route, before[i])
                    ready[vehicle] = end+p.e2_turnaround_time
                choices = []
                ranked = pending if kind == 'd' or self.regret_pickups else pending[:1]
                for pending_index, order in enumerate(ranked):
                    task = order.id, kind
                    options = []
                    for i, route in enumerate(routes):
                        if route['satellite'] != order.satellite:
                            continue
                        existing = route['tasks']
                        if kind == 'd' and any(k == 'd' and problem.order_map[oid].batch != order.batch
                                               for oid, k in existing):
                            continue
                        option_key = task, id(route), before[i]
                        saved = route_options.get(option_key) if self.cache_options else None
                        if saved is not None and saved[0] is route:
                            best = saved[1]
                            # Earlier new trips can shift the global route index.
                            if best is not None:
                                options.append((best[0], i, best[2], False))
                            continue
                        best = None
                        for pos in range(len(existing)+1):
                            if time.perf_counter() >= deadline:
                                return None
                            if kind == 'd' and (order.id, 'p') in existing[:pos]:
                                continue
                            if kind == 'p' and (order.id, 'd') in existing[pos:]:
                                continue
                            candidate = {**route, 'tasks': existing[:pos]+[task]+existing[pos:]}
                            cost, _ = score(candidate, before[i])
                            option = (cost-old_scores[i], i, candidate, False)
                            if best is None or option[0] < best[0]:
                                best = option
                        if self.cache_options:
                            # Hold the source route alive: an object id alone is
                            # unsafe after a route is replaced during this repair.
                            route_options[option_key] = route, best
                        if best is not None:
                            options.append(best)
                    for vehicle in range(problem.fleet.e2_per_sat_count):
                        if counts[order.satellite, vehicle] >= p.e2_max_trips_per_vehicle:
                            continue
                        candidate = dict(satellite=order.satellite, vehicle=vehicle, tasks=[task])
                        window = order.delivery_window if kind == 'd' else order.pickup_window
                        pos = next((i for i, route in enumerate(routes)
                            if route['satellite'] == order.satellite and route['vehicle'] == vehicle
                            and min((problem.order_map[oid].delivery_window if k == 'd' else
                                     problem.order_map[oid].pickup_window)[0]
                                    for oid, k in route['tasks']) > window[0]), len(routes))
                        begin = before[pos] if pos < len(routes) else ready.get(
                            (order.satellite, vehicle), p.cycle_start)
                        cost, _ = score(candidate, begin)
                        options.append((cost, pos, candidate, True))
                    if not options:
                        return None
                    # Different positions on one route are not separate fallback
                    # assignments. Stable ties retain the original due-date order.
                    options.sort(key=lambda option: option[0])
                    regret = options[1][0]-options[0][0] if len(options)>1 else float('inf')
                    choices.append((regret, pending_index, options[0]))
                _, chosen, best = max(choices, key=lambda choice: (choice[0], -choice[1]))
                self.reordered += int(chosen != 0)
                pending.pop(chosen)
                _, index, candidate, new_route = best
                if new_route:
                    routes.insert(index, candidate)
                else:
                    routes[index] = candidate
        return state
