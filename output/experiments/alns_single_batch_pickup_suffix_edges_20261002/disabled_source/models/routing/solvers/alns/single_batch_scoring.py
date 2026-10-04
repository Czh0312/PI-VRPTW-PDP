"""Prepared local scoring for one fixed B=1 context, preserving score arithmetic.

This is a proposal-ranking score, never a feasibility certificate. Preparation
and caches belong to one fixed problem within a solve. New solves and changed
assignments get fresh scorers; ready times remain part of each score's inputs.
"""
from functools import lru_cache
from itertools import chain


class SingleBatchRouteScorer:
    def __init__(self, problem, *, cache_pickup_edges=False):
        if len(problem.operating_policy.batches) != 1:
            raise ValueError('Single-batch scoring requires B=1')
        self.problem = problem
        self.cache_pickup_edges = cache_pickup_edges
        self.policy, self.fleet = problem.operating_policy, problem.fleet
        self.batch = self.policy.batches[0]
        self.tasks = {}
        for order in problem.orders:
            for kind in problem.task_kinds:
                _, weight, volume = problem.task(order.id, kind)
                window = order.delivery_window if kind == 'd' else order.pickup_window
                due = min(window[1], self.batch.cutoff-order.service_time) if kind == 'd' else window[1]
                self.tasks[order.id, kind] = (order, weight, volume, kind, window[0], due)
        # Bounded and context-local. Distances still come through the problem's
        # API, supporting directed/nonmetric matrices and custom travel data.
        self.distance = lru_cache(maxsize=4096)(problem.distance)
        self.travel = lru_cache(maxsize=4096)(problem.travel)

    def prepare_pickup_insertions(self, problem, route, goods, vehicle_ready):
        if problem is not self.problem:
            raise ValueError('Prepared scorer belongs to one problem instance')
        return _PickupInsertions(self, route, goods, vehicle_ready)

    def __call__(self, problem, route, goods, vehicle_ready):
        if problem is not self.problem:
            raise ValueError('Prepared scorer belongs to one problem instance')
        p, f = self.policy, self.fleet
        if not route['tasks']:
            return 0.0, vehicle_ready
        tasks = [self.tasks[t] for t in route['tasks']]
        site = route['satellite']
        delivery = any(t[3] == 'd' for t in tasks)
        current = max(p.cycle_start, vehicle_ready, route.get('depart_after', p.cycle_start))
        if delivery:
            current = max(current, goods[site, self.batch.id])+p.e2_dispatch_prep_time
        weight = sum(t[1] for t in tasks if t[3] == 'd')
        volume = sum(t[2] for t in tasks if t[3] == 'd')
        weight_cap, volume_cap = f.e2_weight_cap, f.e2_volume_cap
        weight_scale, volume_scale = max(1, weight_cap), max(1, volume_cap)
        load_penalty = max(0, weight-weight_cap)/weight_scale
        load_penalty += max(0, volume-volume_cap)/volume_scale
        distance, late, delivered, previous = 0.0, 0, {}, site
        distance_to, travel_to = self.distance, self.travel
        for order, w, vol, kind, ready, due in tasks:
            current = max(current+travel_to(previous, order.customer), ready)
            if kind == 'p':
                current = max(current, delivered.get(order.id, order.delivery_window[0]+order.service_time)+order.min_use_time)
            late += max(0, current-due)
            current += order.service_time
            if kind == 'd':
                delivered[order.id] = current
            distance += distance_to(previous, order.customer)
            previous = order.customer
            weight += w if kind == 'p' else -w
            volume += vol if kind == 'p' else -vol
            load_penalty += max(0, weight-weight_cap)/weight_scale
            load_penalty += max(0, volume-volume_cap)/volume_scale
        completed = max(current+travel_to(previous, site)+p.e2_return_unload_time,
                        route.get('return_after', p.cycle_start))
        late += max(0, completed+p.e2_turnaround_time-p.cycle_end)
        if any(t[3] == 'p' for t in tasks):
            late += max(0, completed-self.batch.cutoff)
        distance += distance_to(previous, site)
        return distance+1e6*(load_penalty+0)+late*p.time_tick_seconds/60*1e4, completed


class _PickupInsertions:
    """Lazy route prefixes for one unchanged route and readiness context.

    Inserting a pickup preserves the ordered initial delivery sums and every
    event before its position. Prefix maps are copied before scoring a suffix;
    no readiness, penalty or feasibility result is reused across repairs.
    """
    def __init__(self, scorer, route, goods, vehicle_ready):
        self.scorer, self.site = scorer, route['satellite']
        self.tasks = [scorer.tasks[t] for t in route['tasks']]
        self.edge_tasks = None
        p, f = scorer.policy, scorer.fleet
        self.return_after = route.get('return_after', p.cycle_start)
        self.weight_cap, self.volume_cap = f.e2_weight_cap, f.e2_volume_cap
        self.weight_scale, self.volume_scale = max(1, f.e2_weight_cap), max(1, f.e2_volume_cap)
        current = max(p.cycle_start, vehicle_ready, route.get('depart_after', p.cycle_start))
        if any(t[3] == 'd' for t in self.tasks):
            current = max(current, goods[self.site, scorer.batch.id])+p.e2_dispatch_prep_time
        weight = sum(t[1] for t in self.tasks if t[3] == 'd')
        volume = sum(t[2] for t in self.tasks if t[3] == 'd')
        load_penalty = max(0, weight-self.weight_cap)/self.weight_scale
        load_penalty += max(0, volume-self.volume_cap)/self.volume_scale
        distance, late, delivered, previous = 0.0, 0, {}, self.site
        self.prefixes = [(current, weight, volume, load_penalty, distance, late, previous, {})]

    def _extend(self, position):
        if len(self.prefixes) > position:
            return
        scorer = self.scorer
        weight_cap, volume_cap = self.weight_cap, self.volume_cap
        weight_scale, volume_scale = self.weight_scale, self.volume_scale
        distance_to, travel_to = scorer.distance, scorer.travel
        current, weight, volume, load_penalty, distance, late, previous, delivered = self.prefixes[-1]
        delivered = dict(delivered)
        for order, w, vol, kind, ready, due in self.tasks[len(self.prefixes)-1:position]:
            current = max(current+travel_to(previous, order.customer), ready)
            if kind == 'p':
                current = max(current, delivered.get(order.id, order.delivery_window[0]+order.service_time)+order.min_use_time)
            late += max(0, current-due)
            current += order.service_time
            if kind == 'd':
                delivered[order.id] = current
            distance += distance_to(previous, order.customer)
            previous = order.customer
            weight += w if kind == 'p' else -w
            volume += vol if kind == 'p' else -vol
            load_penalty += max(0, weight-weight_cap)/weight_scale
            load_penalty += max(0, volume-volume_cap)/volume_scale
            self.prefixes.append((current, weight, volume, load_penalty, distance, late, previous, dict(delivered)))

    def __call__(self, oid, position):
        if not self.scorer.cache_pickup_edges:
            return self._reference_score(oid, position)
        self._extend(position)
        scorer, p = self.scorer, self.scorer.policy
        weight_cap, volume_cap = self.weight_cap, self.volume_cap
        weight_scale, volume_scale = self.weight_scale, self.volume_scale
        distance_to, travel_to = scorer.distance, scorer.travel
        current, weight, volume, load_penalty, distance, late, previous, delivered = self.prefixes[position]
        delivered = dict(delivered)
        inserted = scorer.tasks[oid, 'p']
        customer = inserted[0].customer
        inserted = inserted+(travel_to(previous, customer), distance_to(previous, customer))
        if position < len(self.tasks):
            if self.edge_tasks is None:
                # Static adjacent legs, requested only by a suffix score. The
                # first original task always follows the inserted task, so its
                # incoming leg cannot be reused from the unchanged route.
                self.edge_tasks = [self.tasks[0]+(None, None)]
                for before, task in zip(self.tasks, self.tasks[1:]):
                    a, b = before[0].customer, task[0].customer
                    self.edge_tasks.append(task+(travel_to(a, b), distance_to(a, b)))
            first = self.tasks[position]
            first = first+(travel_to(customer, first[0].customer), distance_to(customer, first[0].customer))
            tasks = chain((inserted, first), self.edge_tasks[position+1:])
        else:
            tasks = (inserted,)
        for order, w, vol, kind, ready, due, travel, leg_distance in tasks:
            current = max(current+travel, ready)
            if kind == 'p':
                current = max(current, delivered.get(order.id, order.delivery_window[0]+order.service_time)+order.min_use_time)
            late += max(0, current-due)
            current += order.service_time
            if kind == 'd':
                delivered[order.id] = current
            distance += leg_distance
            previous = order.customer
            weight += w if kind == 'p' else -w
            volume += vol if kind == 'p' else -vol
            load_penalty += max(0, weight-weight_cap)/weight_scale
            load_penalty += max(0, volume-volume_cap)/volume_scale
        completed = max(current+travel_to(previous, self.site)+p.e2_return_unload_time, self.return_after)
        late += max(0, completed+p.e2_turnaround_time-p.cycle_end)
        late += max(0, completed-scorer.batch.cutoff)
        distance += distance_to(previous, self.site)
        return distance+1e6*(load_penalty+0)+late*p.time_tick_seconds/60*1e4, completed

    def _reference_score(self, oid, position):
        self._extend(position)
        scorer, p = self.scorer, self.scorer.policy
        weight_cap, volume_cap = self.weight_cap, self.volume_cap
        weight_scale, volume_scale = self.weight_scale, self.volume_scale
        distance_to, travel_to = scorer.distance, scorer.travel
        current, weight, volume, load_penalty, distance, late, previous, delivered = self.prefixes[position]
        delivered = dict(delivered)
        for order, w, vol, kind, ready, due in chain((scorer.tasks[oid, 'p'],), self.tasks[position:]):
            current = max(current+travel_to(previous, order.customer), ready)
            if kind == 'p':
                current = max(current, delivered.get(order.id, order.delivery_window[0]+order.service_time)+order.min_use_time)
            late += max(0, current-due)
            current += order.service_time
            if kind == 'd':
                delivered[order.id] = current
            distance += distance_to(previous, order.customer)
            previous = order.customer
            weight += w if kind == 'p' else -w
            volume += vol if kind == 'p' else -vol
            load_penalty += max(0, weight-weight_cap)/weight_scale
            load_penalty += max(0, volume-volume_cap)/volume_scale
        completed = max(current+travel_to(previous, self.site)+p.e2_return_unload_time, self.return_after)
        late += max(0, completed+p.e2_turnaround_time-p.cycle_end)
        late += max(0, completed-scorer.batch.cutoff)
        distance += distance_to(previous, self.site)
        return distance+1e6*(load_penalty+0)+late*p.time_tick_seconds/60*1e4, completed
