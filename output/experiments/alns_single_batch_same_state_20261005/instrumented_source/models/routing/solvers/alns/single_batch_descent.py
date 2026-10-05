"""Bounded, deterministic distance descent for single-batch schedules.

Local route checks only rank/filter heuristic proposals. Each proposal still
passes the full cycle decoder and the independent incumbent audit. Directed,
nonmetric distances are supported; no triangle inequality is assumed.
"""
from collections import defaultdict, deque, OrderedDict
from functools import lru_cache
import heapq
import time

from .neighborhoods import clone_state, state_key, _goods_ready, _route_score


def task_distance(problem, site, tasks):
    nodes = [site]+[problem.order_map[oid].customer for oid, _ in tasks]+[site]
    return sum(problem.distance(a, b) for a, b in zip(nodes, nodes[1:])) if tasks else 0.0


def ordered_pairs(tasks):
    """A pickup on the same route must follow its own delivery."""
    positions = {task: i for i, task in enumerate(tasks)}
    return all((oid, 'd') not in positions or positions[oid, 'd'] < i
               for (oid, kind), i in positions.items() if kind == 'p')


class SingleBatchDescent:
    """Reuse a bounded candidate list until the incumbent changes."""
    def __init__(self, problem, capacity=256, *, cache_sites=True, score_route=None,
                 cache_distances=False, diversify=False):
        if len(problem.operating_policy.batches) != 1:
            raise ValueError('Single-batch descent requires B=1')
        self.problem, self.capacity = problem, capacity
        self.cache_sites = cache_sites
        self.score_route = score_route or _route_score
        self.cache_distances = cache_distances
        self.diversify = diversify
        self.order_customers = {o.id: o.customer for o in problem.orders} if cache_distances else None
        # Geometry stays fixed within this problem context. Bound the cache and
        # create a fresh one for each new context/solve; directed arcs stay distinct.
        self.distance = (lru_cache(maxsize=4096)(problem.distance)
                         if cache_distances else problem.distance)
        self.key, self.queue = None, []
        self.deferred = deque()
        self.context_key = self.bounds_key = None
        self.goods, self.actual_before, self.before = {}, {}, {}
        self.e2_key, self.e2_options = None, []
        self.site_options = OrderedDict()
        self.site_cache_hits = 0
        self.generated = self.filtered = self.exhausted = 0
        self.enumerations = 0
        self.deferred_count = self.recovered = 0

    def reset(self):
        self.key, self.queue = None, []
        self.deferred.clear()
        self.context_key = self.bounds_key = None

    def task_distance(self, site, tasks):
        if not self.cache_distances:
            return task_distance(self.problem, site, tasks)
        nodes = [site]+[self.order_customers[oid] for oid, _ in tasks]+[site]
        return sum(self.distance(a, b) for a, b in zip(nodes, nodes[1:])) if tasks else 0.0

    def _e2_candidates(self, state, deadline):
        if not self.cache_sites:
            local = self._enumerate_routes(state['e2'], deadline)
            e2 = [('e2', changes) for _, _, changes in local]
            self.e2_key, self.e2_options = state_key(state)[3], e2
            return e2
        # Distance moves never cross satellites. A change at one satellite
        # must not force enumeration at all the others. Cache local indices;
        # global route indices can shift when a trip elsewhere is removed.
        groups = defaultdict(list)
        for i, route in enumerate(state['e2']):
            groups[route['satellite']].append(i)
        options = []
        for site, indices in groups.items():
            if time.perf_counter() >= deadline:
                break
            routes = [state['e2'][i] for i in indices]
            key = (site, tuple(tuple(r['tasks']) for r in routes))
            if key in self.site_options:
                self.site_cache_hits += 1
                self.site_options.move_to_end(key)
                local = self.site_options[key]
            else:
                local = self._enumerate_routes(routes, deadline)
                if time.perf_counter() < deadline:
                    self.site_options[key] = local
                    if len(self.site_options) > 64:
                        self.site_options.popitem(last=False)
            for gain, _, changes in local:
                options.append((gain, tuple((indices[i], tasks) for i, tasks in changes)))
        options.sort(key=lambda option: -option[0])
        e2 = [('e2', changes) for _, changes in options[:self.capacity]]
        self.e2_key, self.e2_options = state_key(state)[3], e2
        return e2

    def _enumerate_routes(self, routes, deadline):
        self.enumerations += 1
        problem = self.problem
        distance, distance_of = self.distance, self.task_distance
        heap, serial, seen = [], 0, set()
        route_heaps = {} if self.diversify else None
        distances = [distance_of(r['satellite'], r['tasks']) for r in routes]
        batches = [{problem.order_map[o].batch for o, k in r['tasks'] if k == 'd'} for r in routes]

        def retain(changes, delta=None):
            nonlocal serial
            if delta is None:
                delta = sum(distance_of(routes[i]['satellite'], tasks)-distances[i]
                            for i, tasks in changes)
            if delta >= -1e-8:
                return
            target_heap = (route_heaps.setdefault(tuple(sorted(i for i, _ in changes)), [])
                           if route_heaps is not None else heap)
            if len(target_heap)==self.capacity and -delta <= target_heap[0][0]:
                return
            signature = tuple((i,tuple(tasks)) for i,tasks in changes)
            if signature in seen:
                return
            seen.add(signature)
            if any(not ordered_pairs(tasks) for _, tasks in changes):
                return
            serial += 1
            item = (-delta, -serial, changes)
            if len(target_heap) < self.capacity:
                heapq.heappush(target_heap, item)
            elif item[:2] > target_heap[0][:2]:
                heapq.heapreplace(target_heap, item)

        groups = defaultdict(list)
        for i, r in enumerate(routes):
            groups[r['satellite']].append(i)
        for i, route in enumerate(routes):
            if time.perf_counter() >= deadline:
                break
            tasks = route['tasks']
            # Intra-route relocation and reversal use the complete directed
            # route distance, including every reversed internal arc.
            for a in range(len(tasks)):
                if time.perf_counter() >= deadline:
                    break
                shortened = tasks[:a]+tasks[a+1:]
                for b in range(len(tasks)):
                    if a != b:
                        retain(((i, shortened[:b]+[tasks[a]]+shortened[b:]),))
                for b in range(a+2, len(tasks)):
                    retain(((i, tasks[:a]+list(reversed(tasks[a:b+1]))+tasks[b+1:]),))
            for j in groups[route['satellite']]:
                if i == j:
                    continue
                if time.perf_counter() >= deadline:
                    break
                target = routes[j]['tasks']
                for a, task in enumerate(tasks):
                    if time.perf_counter() >= deadline:
                        break
                    oid, kind = task
                    if kind == 'd' and batches[j]-{problem.order_map[oid].batch}:
                        continue
                    shortened = tasks[:a]+tasks[a+1:]
                    node = problem.order_map[oid].customer
                    left = route['satellite'] if a==0 else problem.order_map[tasks[a-1][0]].customer
                    right = route['satellite'] if a==len(tasks)-1 else problem.order_map[tasks[a+1][0]].customer
                    removal = (distance(left,right)-distance(left,node)-distance(node,right)
                               if shortened else -distances[i])
                    for b in range(len(target)+1):
                        left = route['satellite'] if b==0 else problem.order_map[target[b-1][0]].customer
                        right = route['satellite'] if b==len(target) else problem.order_map[target[b][0]].customer
                        delta = removal+distance(left,node)+distance(node,right)-distance(left,right)
                        if delta < -1e-8:
                            retain(((i, shortened), (j, target[:b]+[task]+target[b:])),delta)
                # Relocate an entire delivery/pickup request. Two individually
                # unattractive single-task moves can jointly eliminate a trip.
                paired = sorted({o for o,k in tasks if k=='d' and (o,'p') in tasks})
                for oid in paired:
                    if time.perf_counter() >= deadline:
                        break
                    if batches[j]-{problem.order_map[oid].batch}:
                        continue
                    shortened = [t for t in tasks if t[0]!=oid]
                    node = problem.order_map[oid].customer
                    removal = distance_of(route['satellite'],shortened)-distances[i]
                    for dpos in range(len(target)+1):
                        left = route['satellite'] if dpos==0 else problem.order_map[target[dpos-1][0]].customer
                        right = route['satellite'] if dpos==len(target) else problem.order_map[target[dpos][0]].customer
                        delivery_delta = distance(left,node)+distance(node,right)-distance(left,right)
                        delivered = target[:dpos]+[(oid,'d')]+target[dpos:]
                        for ppos in range(dpos+1,len(delivered)+1):
                            left = problem.order_map[delivered[ppos-1][0]].customer
                            right = route['satellite'] if ppos==len(delivered) else problem.order_map[delivered[ppos][0]].customer
                            delta = removal+delivery_delta+distance(left,node)+distance(node,right)-distance(left,right)
                            if delta < -1e-8:
                                merged = delivered[:ppos]+[(oid,'p')]+delivered[ppos:]
                                retain(((i,shortened),(j,merged)),delta)
                # Paired exchange changes request ownership between routes
                # without creating extra trips or changing vehicle order.
                if j > i:
                    left = sorted({o for o,k in tasks if k=='d' and
                                   (not problem.reusable or (o,'p') in tasks)})
                    right = sorted({o for o,k in target if k=='d' and
                                    (not problem.reusable or (o,'p') in target)})
                    for a in left:
                        for b in right:
                            if problem.order_map[a].batch != problem.order_map[b].batch:
                                continue
                            retain(((i, [(b if o==a else o,k) for o,k in tasks]),
                                    (j, [(a if o==b else o,k) for o,k in target])))
        if route_heaps is None:
            return sorted(heap, key=lambda x:(-x[0],-x[1]))
        # One turn per affected route set prevents a single family of large
        # geometric moves from filling every slot. Feasibility remains fresh.
        queues = [deque(sorted(h, key=lambda x:(-x[0],-x[1])))
                  for h in route_heaps.values() if h]
        queues.sort(key=lambda q:(-q[0][0],-q[0][1]))
        chosen = []
        while queues and len(chosen) < self.capacity:
            remaining = []
            for queue in queues:
                if len(chosen) >= self.capacity:
                    break
                chosen.append(queue.popleft())
                if queue:
                    remaining.append(queue)
            queues = remaining
        return chosen

    def _candidates(self, state, deadline):
        problem = self.problem
        # E1-only changes do not alter E2 distance deltas. Reuse that expensive
        # enumeration, but recheck candidate times against the new E1 schedule.
        e2 = (self.e2_options if state_key(state)[3]==self.e2_key else
              self._e2_candidates(state,deadline))
        e1 = []
        for batch in problem.operating_policy.batches:
            rows = state['deliveries'][batch.id]
            for a, row in enumerate(rows):
                for pos, site in enumerate(row):
                    for b, other in enumerate(rows):
                        shortened = row[:pos]+row[pos+1:]
                        target = shortened if a==b else other
                        for dest in range(len(target)+1):
                            if a==b and pos==dest:
                                continue
                            e1.append(('delivery', (batch.id, a, pos, b, dest)))
                    # Exchanging sites can cross a capacity barrier that no
                    # feasible one-site relocation can cross by itself.
                    for b in range(a+1,len(rows)):
                        for other_pos in range(len(rows[b])):
                            e1.append(('delivery_swap',(batch.id,a,pos,b,other_pos)))
            if not problem.reusable:
                continue
            for site in problem.satellites:
                for vehicle in range(problem.fleet.e1_count):
                    if state['pickup_vehicle'][batch.id].get(site) != vehicle:
                        e1.append(('pickup', (batch.id, site, vehicle)))
            if state['pickup_vehicle'][batch.id]:
                e1.append(('clear_hints', batch.id))
            order = state['pickup_order'][batch.id]
            for a in range(len(order)):
                for b in range(a+1,len(order)):
                    e1.append(('pickup_order', (batch.id,a,b)))
        # Interleave both echelons so a long list of infeasible E2 candidates
        # cannot starve replenishment and pickup improvements.
        queue = []
        for i in range(max(len(e1),len(e2))):
            if i < len(e2): queue.append(e2[i])
            if i < len(e1): queue.append(e1[i])
        self.generated += len(queue)
        return list(reversed(queue))

    def _optimistic_score(self, route, goods, ready):
        p = self.problem.operating_policy
        screen_route = route
        if p.e2_dispatch_prep_time and 'depart_after' in route and any(k == 'd' for _, k in route['tasks']):
            # The decoder treats the hint as an absolute departure bound;
            # preparation applies to goods/vehicle readiness, not the hint.
            screen_route = {**route, 'depart_after': route['depart_after']-p.e2_dispatch_prep_time}
        return self.score_route(self.problem, screen_route, goods, ready)

    def _prepare_context(self, state, decoded, key):
        if key == self.context_key:
            return
        self.goods = _goods_ready(self.problem, state)
        p = self.problem.operating_policy
        ready, actual = {}, {}
        indices = (i for i, route in enumerate(state['e2']) if route['tasks'])
        for i, trip in zip(indices, decoded['e2_trips']):
            vehicle = trip['satellite_id'], trip['vehicle_id']
            actual[i] = ready.get(vehicle, p.cycle_start)
            ready[vehicle] = trip['return_complete']+p.e2_turnaround_time
        self.actual_before, self.context_key = actual, key

    def _prepare_bounds(self, state, key, deadline):
        if key == self.bounds_key:
            return True
        ready, before = {}, {}
        p = self.problem.operating_policy
        for i, route in enumerate(state['e2']):
            if time.perf_counter() >= deadline:
                return False
            vehicle = route['satellite'], route['vehicle']
            before[i] = ready.get(vehicle, p.cycle_start)
            if route['tasks']:
                _, complete = self._optimistic_score(route, self.goods, before[i])
                ready[vehicle] = complete+p.e2_turnaround_time
        self.bounds_key, self.before = key, before
        return True

    def propose(self, state, decoded, deadline):
        if time.perf_counter() >= deadline:
            return None
        key = state_key(state)
        if key != self.key:
            self.queue = self._candidates(state, deadline)
            self.deferred.clear()
            self.key = key
        self._prepare_context(state, decoded, key)
        goods = self.goods
        p = self.problem.operating_policy
        while (self.queue or self.deferred) and time.perf_counter() < deadline:
            fresh = not self.queue
            kind, detail = ('e2', self.deferred.popleft()) if fresh else self.queue.pop()
            proposed = clone_state(state)
            if kind == 'e2':
                affected, last, changed = {}, {}, set()
                for i, tasks in detail:
                    route = proposed['e2'][i]
                    route['tasks'] = list(tasks)
                    vehicle = route['satellite'], route['vehicle']
                    affected[vehicle] = min(affected.get(vehicle, i), i)
                    last[vehicle] = max(last.get(vehicle, i), i)
                    changed.add(i)
                pure = len(detail) == 1 and set(detail[0][1]) == set(state['e2'][detail[0][0]]['tasks'])
                if not fresh:
                    keep = all(self._optimistic_score(proposed['e2'][i], goods,
                               self.actual_before.get(i, p.cycle_start))[0] <=
                               self.task_distance(proposed['e2'][i]['satellite'], tasks)+1e-6
                               for i, tasks in detail)
                    if not keep:
                        if pure:
                            # A permutation preserves cross-route ownership.
                            # In a feasible DAG, preceding trips cannot depend
                            # on this route, so their decoded times remain valid.
                            self.filtered += 1
                        else:
                            self.deferred.append(detail)
                            self.deferred_count += 1
                        continue
                    proposed['e2'] = [r for r in proposed['e2'] if r['tasks']]
                    return proposed
                if not self._prepare_bounds(state, key, deadline):
                    self.deferred.appendleft(detail)
                    return None
                ready = {v: self.before[i] for v, i in affected.items()}
                keep = True
                for i, route in enumerate(proposed['e2']):
                    if time.perf_counter() >= deadline:
                        self.deferred.appendleft(detail)
                        return None
                    vehicle = route['satellite'], route['vehicle']
                    if vehicle not in affected or not affected[vehicle] <= i <= last[vehicle] or not route['tasks']:
                        continue
                    # Deleted/shortened trips no longer retain their incumbent
                    # delays. These are local lower bounds; customer edges and
                    # unchanged suffix trips are still checked by the decoder.
                    score, complete = self._optimistic_score(route, goods, ready[vehicle])
                    if i in changed and score > self.task_distance(route['satellite'],route['tasks'])+1e-6:
                        keep = False
                        break
                    ready[vehicle] = complete+p.e2_turnaround_time
                if not keep:
                    self.filtered += 1
                    continue
                self.recovered += 1
                proposed['e2'] = [r for r in proposed['e2'] if r['tasks']]
            elif kind == 'delivery':
                batch, a, pos, b, dest = detail
                rows = proposed['deliveries'][batch]
                site = rows[a].pop(pos)
                rows[b].insert(dest,site)
            elif kind == 'delivery_swap':
                batch,a,pos,b,other_pos = detail
                rows = proposed['deliveries'][batch]
                rows[a][pos],rows[b][other_pos] = rows[b][other_pos],rows[a][pos]
            elif kind == 'pickup':
                batch, site, vehicle = detail
                proposed['pickup_vehicle'][batch][site] = vehicle
            elif kind == 'clear_hints':
                proposed['pickup_vehicle'][detail] = {}
            else:
                batch,a,b = detail
                order = proposed['pickup_order'][batch]
                order[a],order[b] = order[b],order[a]
            return proposed
        self.exhausted += 1
        return None
