"""Bounded beam repair across simultaneous clean-stock bottlenecks in M5.

A single early return can leave N unchanged when another loading has the same
stock deficit. Keep a few feasible intermediate schedules ranked by remaining
deficits, then return only a full-cost improvement. Every node uses the ordinary
decoder; the stock vector ranks candidates and never certifies feasibility.
"""
from collections import Counter, OrderedDict
import time

from models.routing.model_5.alns.state import clone_state, state_key
from models.routing.model_5.alns.pool_moves import release_waits


def stock_requirements(problem, decoded):
    """Conditional pool requirements at all loading events, from actual returns."""
    p = problem.operating_policy
    releases = [(trip['return_complete'] + p.cleaning_duration,
                 sum(e['quantity'] for e in trip['events'] if e['kind'] == 'p'))
                for trip in decoded['e1_trips']]
    issued, values = 0, []
    for batch in p.batches:
        issued += problem.batch_demands[batch.id]
        values.append(max(problem.pool_min,
                          issued - sum(q for t, q in releases if t <= batch.start)))
    return tuple(values)


class CriticalStockRepair:
    def __init__(self, problem, *, lower_bound, beam_width=2, max_evaluations=192,
                 max_depth=4, max_neighbors=32):
        if not getattr(problem, 'optimize_pool', False) or problem.model_number != 5:
            raise ValueError('Stock-chain repair requires M5 pool optimization')
        if min(beam_width, max_evaluations, max_depth, max_neighbors) < 1:
            raise ValueError('Stock-chain limits must be positive')
        self.problem, self.lower_bound = problem, max(problem.pool_min, lower_bound)
        self.beam_width, self.max_evaluations = beam_width, max_evaluations
        self.max_depth, self.max_neighbors = max_depth, max_neighbors
        self.seen_roots = OrderedDict()
        self.calls = self.evaluations = self.feasible_evaluations = 0
        self.seconds = 0.0
        self.best_pool = self.best_pool_cost = None
        self.last_run = None

    def _groups(self, state, decoded, loading_index):
        """Find late boxes and their delivery-site/recovery-wave dependencies."""
        p = self.problem.operating_policy
        load = p.batches[loading_index].start
        clean = {(e['site_id'], trip['batch_id']): trip['return_complete'] + p.cleaning_duration
                 for trip in decoded['e1_trips'] for e in trip['events'] if e['kind'] == 'p'}
        groups = OrderedDict()
        for route, trip in zip((r for r in state['e2'] if r['tasks']), decoded['e2_trips']):
            actual = next((b for b in p.batches if trip['return_complete'] <= b.cutoff), None)
            if actual is None or clean.get((route['satellite'], actual.id), p.cycle_end + 1) <= load:
                continue
            for oid, kind in route['tasks']:
                order = self.problem.order_map[oid]
                if kind != 'p' or self.problem.batch_index[order.batch] >= loading_index:
                    continue
                # Latest recovery cutoff before this loading; full decode will
                # decide whether E1 travel and cleaning can actually make it.
                target = p.batches[loading_index - 1]
                key = (order.satellite, order.batch, target.id)
                groups.setdefault(key, []).append(oid)
        return [(key, tuple(sorted(set(ids), key=lambda oid: (
                    self.problem.order_map[oid].pickup_window[0], oid))))
                for key, ids in groups.items()]

    def _paired(self, state, ids, vehicles):
        """Remove complete requests, then insert early dedicated paired trips."""
        problem = self.problem
        proposed = clone_state(state)
        selected = set(ids)
        for route in proposed['e2']:
            if any(oid in selected for oid, _ in route['tasks']):
                route['tasks'] = [(oid, kind) for oid, kind in route['tasks'] if oid not in selected]
                release_waits(route)
        proposed['e2'] = [r for r in proposed['e2'] if r['tasks']]
        # Reverse insertion preserves the chosen request order on a vehicle.
        for oid, vehicle in reversed(list(zip(ids, vehicles))):
            order = problem.order_map[oid]
            batch_index = problem.batch_index[order.batch]
            position = next((i for i, r in enumerate(proposed['e2'])
                if r['satellite'] == order.satellite and r['vehicle'] == vehicle
                and min(problem.batch_index[problem.order_map[o].batch] for o, _ in r['tasks'])
                >= batch_index), len(proposed['e2']))
            proposed['e2'].insert(position, dict(satellite=order.satellite, vehicle=vehicle,
                                                tasks=[(oid, 'd'), (oid, 'p')]))
        counts = Counter((r['satellite'], r['vehicle']) for r in proposed['e2'])
        if any(n > problem.operating_policy.e2_max_trips_per_vehicle for n in counts.values()):
            return None
        return proposed

    def _e1_variants(self, state, site, delivery_batch, recovery_batch):
        yield state, 'e2_only'
        for vehicle in range(self.problem.fleet.e1_count):
            proposed = clone_state(state)
            rows = proposed['deliveries'][delivery_batch]
            for row in rows:
                if site in row:
                    row.remove(site)
            rows[vehicle].insert(0, site)
            order = proposed['pickup_order'][recovery_batch]
            order[:] = [site] + [s for s in order if s != site]
            proposed['pickup_vehicle'][recovery_batch][site] = vehicle
            yield proposed, f'e1_vehicle_{vehicle}'

    def _neighbors(self, state, decoded, loading_index, deadline):
        seen, count = {state_key(state)}, 0
        for (site, delivery, recovery), all_ids in self._groups(state, decoded, loading_index):
            if time.perf_counter() >= deadline:
                return
            ids = all_ids[:3]
            # Whole blocked group first, then single requests. At most three
            # requests are rebuilt per site; larger groups can take more depth.
            selections = [ids] + [(oid,) for oid in ids if len(ids) > 1]
            cleared = clone_state(state)
            for route in cleared['e2']:
                if route['satellite'] == site:
                    release_waits(route)
            bases = [(cleared, 'clear_waits')]
            for selected in selections:
                vcount = self.problem.fleet.e2_per_sat_count
                assignments = list(dict.fromkeys(
                    [tuple((v + i) % vcount for i in range(len(selected))) for v in range(vcount)]
                    + [tuple(v for _ in selected) for v in range(vcount)]))
                for vehicles in assignments:
                    paired = self._paired(state, selected, vehicles)
                    if paired is not None:
                        bases.append((paired, f'pair_{len(selected)}'))
            # Round-robin E1 choices prevent the cap being consumed by one
            # truck assignment before E2 vehicle alternatives are considered.
            variants = [list(self._e1_variants(base, site, delivery, recovery)) for base, _ in bases]
            for e1_index in range(self.problem.fleet.e1_count + 1):
                for (base, kind), choices in zip(bases, variants):
                    if time.perf_counter() >= deadline or count >= self.max_neighbors:
                        return
                    proposed, mode = choices[e1_index]
                    key = state_key(proposed)
                    if key in seen:
                        continue
                    seen.add(key)
                    count += 1
                    yield proposed, dict(site=site, delivery_batch=delivery,
                                         recovery_batch=recovery, e2_move=kind, e1_move=mode)

    def propose(self, state, decoded, evaluator, deadline):
        begun = time.perf_counter()
        if begun >= deadline or decoded['container_pool_size'] <= self.lower_bound:
            return None
        root_key = state_key(state)
        if root_key in self.seen_roots:
            return None
        self.seen_roots[root_key] = True
        if len(self.seen_roots) > 64:
            self.seen_roots.popitem(last=False)
        self.calls += 1
        target = decoded['container_pool_size'] - 1
        root_cost = decoded['info']['objective']
        best_cost, best_state, best_path = root_cost, None, []
        root_requirements = stock_requirements(self.problem, decoded)
        frontier = [(state, decoded, root_requirements, [])]
        seen, evaluated, feasible, reached_depth = {root_key}, 0, 0, 0
        lowest_pool, lowest_pool_cost, lowest_path = decoded['container_pool_size'], root_cost, []
        stop = False
        for depth in range(1, self.max_depth + 1):
            next_nodes = []
            for parent, schedule, required, path in frontier:
                loading = next((i for i, value in enumerate(required) if value > target), None)
                if loading is None or loading == 0:
                    continue
                old_excess = sum(max(0, value - target) for value in required)
                for proposed, move in self._neighbors(parent, schedule, loading, deadline):
                    if time.perf_counter() >= deadline or evaluated >= self.max_evaluations:
                        stop = True
                        break
                    key = state_key(proposed)
                    if key in seen:
                        continue
                    seen.add(key)
                    score, result, issues = evaluator(proposed)
                    evaluated += 1
                    if issues:
                        continue
                    feasible += 1
                    reached_depth = max(reached_depth, depth)
                    requirements = stock_requirements(self.problem, result)
                    excess = sum(max(0, value - target) for value in requirements)
                    step = dict(**move, loading_index=loading, pool=result['container_pool_size'],
                                cost=score, remaining_excess=excess)
                    chain = path + [step]
                    if (result['container_pool_size'], score) < (lowest_pool, lowest_pool_cost):
                        lowest_pool, lowest_pool_cost, lowest_path = result['container_pool_size'], score, chain
                    if score < best_cost - 1e-9:
                        best_cost, best_state, best_path = score, proposed, chain
                    # Keep temporary cost increases only if they relieve a
                    # critical loading; never replace the incumbent by them.
                    if excess < old_excess or score < schedule['info']['objective'] - 1e-9:
                        next_nodes.append(((excess, max(requirements), score),
                                           proposed, result, requirements, chain))
                if stop:
                    break
            next_nodes.sort(key=lambda item: item[0])
            frontier = [(s, r, req, path) for _, s, r, req, path in next_nodes[:self.beam_width]]
            if stop or not frontier or all(max(req) <= target for _, _, req, _ in frontier):
                break
        elapsed = time.perf_counter() - begun
        self.seconds += elapsed
        self.evaluations += evaluated
        self.feasible_evaluations += feasible
        if self.best_pool is None or (lowest_pool, lowest_pool_cost) < (self.best_pool, self.best_pool_cost):
            self.best_pool, self.best_pool_cost = lowest_pool, lowest_pool_cost
        self.last_run = dict(target_pool=target, root_pool=decoded['container_pool_size'],
            root_cost=root_cost, root_requirements=list(root_requirements),
            evaluations=evaluated, feasible_evaluations=feasible, reached_depth=reached_depth,
            seconds=elapsed, returned_improvement=best_state is not None,
            best_cost=best_cost, best_path=best_path,
            lowest_pool=lowest_pool, lowest_pool_cost=lowest_pool_cost, lowest_pool_path=lowest_path)
        return best_state
