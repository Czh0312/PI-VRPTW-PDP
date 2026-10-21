"""Bounded pool-reuse proposals, complementary to distance descent.

A trip returning after a cutoff can delay all its empty boxes by a batch.
Splitting an early pickup prefix may save boxes even when distance increases.
Ranking is heuristic; every proposal needs the full cost decoder and audit.
"""
from collections import Counter
import time

from models.routing.model_5.alns.state import clone_state, state_key
from models.routing.model_5.alns.intensification import task_distance
from models.routing.model_5.alns.neighborhoods import _goods_ready, _route_score


def release_waits(route):
    route.pop('depart_after', None)
    route.pop('return_after', None)


class PoolReuseDescent:
    def __init__(self, problem, capacity=64):
        self.problem, self.capacity = problem, capacity
        self.key, self.queue = None, []
        self.generated = 0

    def _candidates(self, state, decoded, deadline):
        p = self.problem.operating_policy
        rate = self.problem.cost_profile.rates
        spare = decoded['container_pool_size'] - self.problem.pool_min
        if spare <= 0 or rate['container_pool_cost_per_period'] == 0:
            return []
        options = []
        if any('depart_after' in r or 'return_after' in r for r in state['e2']):
            options.append((float('-inf'), 'waits', None))
        slots = Counter((r['satellite'], r['vehicle']) for r in state['e2'] if r['tasks'])
        trips = iter(decoded['e2_trips'])
        goods = _goods_ready(self.problem, state)
        ready = {}
        for i, route in enumerate(state['e2']):
            if time.perf_counter() >= deadline:
                break
            tasks = route['tasks']
            if not tasks:
                continue
            trip = next(trips)
            vehicle = route['satellite'], route['vehicle']
            before = ready.get(vehicle, p.cycle_start)
            ready[vehicle] = trip['return_complete'] + p.e2_turnaround_time
            if (slots[route['satellite'], route['vehicle']] >= p.e2_max_trips_per_vehicle
                    or not any(b.cutoff < trip['return_complete'] for b in p.batches[:-1])):
                continue
            original = task_distance(self.problem, route['satellite'], tasks)
            quantity = 0
            for split in range(1, len(tasks)):
                if time.perf_counter() >= deadline:
                    break
                oid, kind = tasks[split - 1]
                if kind != 'p':
                    continue
                quantity += self.problem.order_map[oid].count
                prefix = {**route, 'tasks': tasks[:split]}
                release_waits(prefix)
                _, returned = _route_score(self.problem, prefix, goods, before)
                # A useful split must reach an earlier recovery cutoff and
                # could, even with relaxed E1 timing, clean before a later
                # loading. Actual competition and pool savings are decoded.
                clean_trip = (self.problem.service(route['satellite'])
                              + self.problem.travel(route['satellite'], self.problem.depot)
                              + p.depot_return_unload_time + p.cleaning_duration)
                if not any(returned <= b.cutoff < trip['return_complete']
                           and any(later.start >= b.cutoff + clean_trip for later in p.batches)
                           for b in p.batches[:-1]):
                    continue
                distance = (task_distance(self.problem, route['satellite'], tasks[:split])
                            + task_distance(self.problem, route['satellite'], tasks[split:]) - original)
                estimate = (distance * rate['distance_scale_to_km'] * rate['e2_distance_cost_per_km']
                            + rate['e2_trip_cost']
                            - min(spare, quantity) * rate['container_pool_cost_per_period'])
                options.append((estimate, 'split', (i, split)))
        options.sort(key=lambda item: (item[0], item[1], item[2] or ()))
        return list(reversed(options[:self.capacity]))

    def propose(self, state, decoded, deadline):
        if time.perf_counter() >= deadline:
            return None
        key = state_key(state)
        if key != self.key:
            self.queue = self._candidates(state, decoded, deadline)
            self.key = key
        if not self.queue or time.perf_counter() >= deadline:
            return None
        _, kind, detail = self.queue.pop()
        proposed = clone_state(state)
        if kind == 'waits':
            for route in proposed['e2']:
                release_waits(route)
        else:
            i, split = detail
            route = proposed['e2'][i]
            prefix = {**route, 'tasks': route['tasks'][:split]}
            suffix = {**route, 'tasks': route['tasks'][split:]}
            release_waits(prefix)
            release_waits(suffix)
            proposed['e2'][i:i + 1] = [prefix, suffix]
        self.generated += 1
        return proposed
