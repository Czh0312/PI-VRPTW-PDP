"""Prepared local scoring for one fixed B=1 context, preserving score arithmetic.

This is a proposal-ranking score, never a feasibility certificate. Preparation
and caches belong to one fixed problem within a solve. New solves and changed
assignments get fresh scorers; ready times remain part of each score's inputs.
"""
from functools import lru_cache


class SingleBatchRouteScorer:
    def __init__(self, problem):
        if len(problem.operating_policy.batches) != 1:
            raise ValueError('Single-batch scoring requires B=1')
        self.problem = problem
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
        # Most proposals fit capacity. Skip zero divisions and additions,
        # preserving the original order of every positive penalty term.
        load_penalty = 0.0
        if weight > weight_cap:
            load_penalty += (weight-weight_cap)/weight_scale
        if volume > volume_cap:
            load_penalty += (volume-volume_cap)/volume_scale
        distance, late, delivered, previous = 0.0, 0, {}, site
        distance_to, travel_to = self.distance, self.travel
        for order, w, vol, kind, ready, due in tasks:
            current = max(current+travel_to(previous, order.customer), ready)
            if kind == 'p':
                current = max(current, delivered.get(order.id, order.delivery_window[0]+order.service_time)+order.min_use_time)
            if current > due:
                late += current-due
            current += order.service_time
            if kind == 'd':
                delivered[order.id] = current
            distance += distance_to(previous, order.customer)
            previous = order.customer
            weight += w if kind == 'p' else -w
            volume += vol if kind == 'p' else -vol
            if weight > weight_cap:
                load_penalty += (weight-weight_cap)/weight_scale
            if volume > volume_cap:
                load_penalty += (volume-volume_cap)/volume_scale
        completed = max(current+travel_to(previous, site)+p.e2_return_unload_time,
                        route.get('return_after', p.cycle_start))
        if completed+p.e2_turnaround_time > p.cycle_end:
            late += completed+p.e2_turnaround_time-p.cycle_end
        if any(t[3] == 'p' for t in tasks):
            if completed > self.batch.cutoff:
                late += completed-self.batch.cutoff
        distance += distance_to(previous, site)
        return distance+1e6*(load_penalty+0)+late*p.time_tick_seconds/60*1e4, completed
