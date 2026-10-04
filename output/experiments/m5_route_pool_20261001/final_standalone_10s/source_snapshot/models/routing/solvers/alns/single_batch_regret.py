"""Repair scarce insertion choices first in a fixed B=1 context.

Regret compares the best insertion on distinct routes, including new trips.
All costs are local heuristic rankings; the complete decoder and independent
incumbent audit remain responsible for feasibility.
"""
from collections import Counter
import time

from .neighborhoods import clone_state, _goods_ready
from .single_batch_scoring import SingleBatchRouteScorer


ENABLED_MODELS = frozenset((2,))


def enabled_for(model, order_count):
    return model in ENABLED_MODELS and order_count >= 20


class SingleBatchRegret:
    def __init__(self, problem, *, scorer=None, cache_options=True, screen=True):
        if len(problem.operating_policy.batches) != 1:
            raise ValueError('Single-batch regret requires B=1')
        self.problem = problem
        self.scorer = scorer if scorer is not None else SingleBatchRouteScorer(problem)
        self.cache_options, self.screen = cache_options, screen
        self.completed = self.reordered = 0

    def __call__(self, original, rng, deadline):
        orders = list(self.problem.orders)
        if not orders:
            return clone_state(original)
        anchor = rng.choice(orders)
        orders.sort(key=lambda o: (o.satellite != anchor.satellite,
            self.problem.distance(anchor.customer, o.customer)
            +abs(o.delivery_window[0]-anchor.delivery_window[0])*.01))
        count = min(len(orders), max(2, min(5, len(orders)//5)))
        result = self.repair(original, orders[:count], deadline)
        if result is None:
            return clone_state(original)
        self.completed += 1
        return result

    def repair(self, original, removed, deadline):
        problem, p = self.problem, self.problem.operating_policy
        state = clone_state(original)
        ids = {o.id for o in removed}
        for route in state['e2']:
            route['tasks'] = [t for t in route['tasks'] if t[0] not in ids]
        state['e2'] = [r for r in state['e2'] if r['tasks']]
        goods = _goods_ready(problem, state)
        # Repair-local caches include readiness and retain their source objects
        # to prevent object-id reuse. Goods and problem stay fixed here.
        baselines, route_options = {}, {}
        for kind in problem.task_kinds:
            pending = sorted(removed, key=lambda o:
                (o.delivery_window if kind == 'd' else o.pickup_window)[1])
            while pending:
                if time.perf_counter() >= deadline:
                    return None
                routes = state['e2']
                ready, before, scores, distances = {}, {}, {}, {}
                counts = Counter((r['satellite'], r['vehicle']) for r in routes)
                sites = {o.satellite for o in pending}
                for i, route in enumerate(routes):
                    if time.perf_counter() >= deadline:
                        return None
                    if route['satellite'] not in sites:
                        continue
                    vehicle = route['satellite'], route['vehicle']
                    before[i] = ready.get(vehicle, p.cycle_start)
                    saved = baselines.get((id(route), before[i]))
                    if saved is None:
                        cost, end = self.scorer(problem, route, goods, before[i])
                        nodes = [route['satellite']]+[problem.order_map[o].customer for o, _ in route['tasks']]+[route['satellite']]
                        distance = sum(problem.distance(a, b) for a, b in zip(nodes, nodes[1:]))
                        saved = route, cost, end, distance
                        baselines[id(route), before[i]] = saved
                    _, scores[i], end, distances[i] = saved
                    ready[vehicle] = end+p.e2_turnaround_time
                choices = []
                # Preserve due-date pickup ordering; compare regret for the
                # delivery phase where a scarce outbound capacity slot matters.
                ranked = pending if kind == 'd' else pending[:1]
                for pending_index, order in enumerate(ranked):
                    task, options = (order.id, kind), []
                    for i, route in enumerate(routes):
                        if route['satellite'] != order.satellite:
                            continue
                        existing = route['tasks']
                        key = task, id(route), before[i]
                        saved = route_options.get(key) if self.cache_options else None
                        if saved is not None:
                            best = saved[1]
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
                            if self.screen and best is not None:
                                left = route['satellite'] if pos == 0 else problem.order_map[existing[pos-1][0]].customer
                                right = route['satellite'] if pos == len(existing) else problem.order_map[existing[pos][0]].customer
                                bound = (distances[i]+problem.distance(left, order.customer)
                                    +problem.distance(order.customer, right)-problem.distance(left, right)-scores[i])
                                tolerance = 1e-9*max(1, abs(bound), abs(best[0]), abs(scores[i]))
                                if bound > best[0]+tolerance:
                                    continue
                            candidate = {**route, 'tasks': existing[:pos]+[task]+existing[pos:]}
                            cost, _ = self.scorer(problem, candidate, goods, before[i])
                            option = cost-scores[i], i, candidate, False
                            if best is None or option[0] < best[0]:
                                best = option
                        if self.cache_options:
                            route_options[key] = route, best
                        if best is not None:
                            options.append(best)
                    for vehicle in range(problem.fleet.e2_per_sat_count):
                        if time.perf_counter() >= deadline:
                            return None
                        if counts[order.satellite, vehicle] >= p.e2_max_trips_per_vehicle:
                            continue
                        candidate = dict(satellite=order.satellite, vehicle=vehicle, tasks=[task])
                        window = order.delivery_window if kind == 'd' else order.pickup_window
                        pos = next((i for i, route in enumerate(routes)
                            if route['satellite'] == order.satellite and route['vehicle'] == vehicle
                            and min((problem.order_map[o].delivery_window if k == 'd' else
                                     problem.order_map[o].pickup_window)[0] for o, k in route['tasks']) > window[0]), len(routes))
                        begin = before[pos] if pos < len(routes) else ready.get((order.satellite, vehicle), p.cycle_start)
                        cost, _ = self.scorer(problem, candidate, goods, begin)
                        options.append((cost, pos, candidate, True))
                    if not options:
                        return None
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
