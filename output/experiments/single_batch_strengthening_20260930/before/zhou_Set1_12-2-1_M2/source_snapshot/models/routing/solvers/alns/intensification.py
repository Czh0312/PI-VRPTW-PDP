"""Bounded, deterministic distance descent for multi-batch cycle schedules.

Local route checks only rank/filter heuristic proposals. Each proposal still
passes the full cycle decoder and the independent incumbent audit. Directed,
nonmetric distances are supported; no triangle inequality is assumed.
"""
from collections import defaultdict
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


class CycleDescent:
    """Reuse a bounded candidate list until the incumbent changes."""
    def __init__(self, problem, capacity=256):
        self.problem, self.capacity = problem, capacity
        self.key, self.queue = None, []
        self.e2_key, self.e2_options = None, []
        self.generated = self.filtered = self.exhausted = 0
        self.enumerations = 0

    def reset(self):
        self.key, self.queue = None, []

    def _e2_candidates(self, state, deadline):
        self.enumerations += 1
        problem = self.problem
        routes = state['e2']
        heap, serial, seen = [], 0, set()
        distances = [task_distance(problem, r['satellite'], r['tasks']) for r in routes]
        batches = [{problem.order_map[o].batch for o, k in r['tasks'] if k == 'd'} for r in routes]

        def retain(changes, delta=None):
            nonlocal serial
            if delta is None:
                delta = sum(task_distance(problem, routes[i]['satellite'], tasks)-distances[i]
                            for i, tasks in changes)
            if delta >= -1e-8:
                return
            if len(heap)==self.capacity and -delta <= heap[0][0]:
                return
            signature = tuple((i,tuple(tasks)) for i,tasks in changes)
            if signature in seen:
                return
            seen.add(signature)
            if any(not ordered_pairs(tasks) for _, tasks in changes):
                return
            serial += 1
            item = (-delta, -serial, changes)
            if len(heap) < self.capacity:
                heapq.heappush(heap, item)
            elif item[:2] > heap[0][:2]:
                heapq.heapreplace(heap, item)

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
                    removal = (problem.distance(left,right)-problem.distance(left,node)-problem.distance(node,right)
                               if shortened else -distances[i])
                    for b in range(len(target)+1):
                        left = route['satellite'] if b==0 else problem.order_map[target[b-1][0]].customer
                        right = route['satellite'] if b==len(target) else problem.order_map[target[b][0]].customer
                        delta = removal+problem.distance(left,node)+problem.distance(node,right)-problem.distance(left,right)
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
                    removal = task_distance(problem,route['satellite'],shortened)-distances[i]
                    for dpos in range(len(target)+1):
                        left = route['satellite'] if dpos==0 else problem.order_map[target[dpos-1][0]].customer
                        right = route['satellite'] if dpos==len(target) else problem.order_map[target[dpos][0]].customer
                        delivery_delta = problem.distance(left,node)+problem.distance(node,right)-problem.distance(left,right)
                        delivered = target[:dpos]+[(oid,'d')]+target[dpos:]
                        for ppos in range(dpos+1,len(delivered)+1):
                            left = problem.order_map[delivered[ppos-1][0]].customer
                            right = route['satellite'] if ppos==len(delivered) else problem.order_map[delivered[ppos][0]].customer
                            delta = removal+delivery_delta+problem.distance(left,node)+problem.distance(node,right)-problem.distance(left,right)
                            if delta < -1e-8:
                                merged = delivered[:ppos]+[(oid,'p')]+delivered[ppos:]
                                retain(((i,shortened),(j,merged)),delta)
                # Paired exchange changes request ownership between routes
                # without creating extra trips or changing vehicle order.
                if j > i:
                    left = sorted({o for o,k in tasks if k=='d' and (o,'p') in tasks})
                    right = sorted({o for o,k in target if k=='d' and (o,'p') in target})
                    for a in left:
                        for b in right:
                            if problem.order_map[a].batch != problem.order_map[b].batch:
                                continue
                            retain(((i, [(b if o==a else o,k) for o,k in tasks]),
                                    (j, [(a if o==b else o,k) for o,k in target])))
        e2 = [('e2', changes) for _,_,changes in sorted(heap, key=lambda x:(-x[0],-x[1]))]
        self.e2_key, self.e2_options = state_key(state)[3], e2
        return e2

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

    def propose(self, state, decoded, deadline):
        key = state_key(state)
        if key != self.key:
            self.queue = self._candidates(state, deadline)
            self.key = key
        goods = _goods_ready(self.problem, state)
        p = self.problem.operating_policy
        ready, before = {}, {}
        for i, trip in enumerate(decoded['e2_trips']):
            vehicle = trip['satellite_id'], trip['vehicle_id']
            before[i] = ready.get(vehicle, p.cycle_start)
            ready[vehicle] = trip['return_complete']+p.e2_turnaround_time
        while self.queue and time.perf_counter() < deadline:
            kind, detail = self.queue.pop()
            proposed = clone_state(state)
            if kind == 'e2':
                keep = True
                for i, tasks in detail:
                    route = proposed['e2'][i]
                    route['tasks'] = list(tasks)
                    score, _ = _route_score(self.problem, route, goods, before.get(i,p.cycle_start))
                    if score > task_distance(self.problem,route['satellite'],tasks)+1e-6:
                        keep = False
                        break
                if not keep:
                    self.filtered += 1
                    continue
                proposed['e2'] = [r for r in proposed['e2'] if r['tasks']]
            elif kind == 'delivery':
                batch, a, pos, b, dest = detail
                rows = proposed['deliveries'][batch]
                site = rows[a].pop(pos)
                rows[b].insert(dest,site)
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
