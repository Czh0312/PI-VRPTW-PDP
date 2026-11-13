"""Experimental M5 beam balancing complete cost and clean-stock requirements.

Requirement/cost labels are heuristic diversity summaries, not an exact
dominance rule for future schedules. Every candidate uses the full decoder.
"""
from dataclasses import dataclass
import time

from models.routing.model_5.alns.state import clone_state, state_key
from models.routing.model_5.alns.stock_chain import CriticalStockRepair, stock_requirements


@dataclass
class _Node:
    state: dict
    result: dict
    required: tuple
    path: list

    @property
    def cost(self):
        return self.result['info']['objective']

    @property
    def nonpool(self):
        return self.cost - self.result['cost_components']['container_pool']


def _select_frontier(nodes, target, width):
    """Reserve a cost lane and a stock-progress lane among diverse labels."""
    # Identical labels retain the first route, deterministically. This is
    # bounded heuristic pruning: different routes can have different futures.
    labels = {}
    for node in nodes:
        label = (node.required, node.nonpool)
        labels.setdefault(label, node)
    values = list(labels.values())
    nondominated = [a for a in values if not any(
        b.nonpool <= a.nonpool and all(x <= y for x, y in zip(b.required, a.required))
        and (b.nonpool < a.nonpool or b.required != a.required)
        for b in values if b is not a)]
    if not nondominated:
        return []
    excess = lambda n: sum(max(0, v - target) for v in n.required)
    rankings = [sorted(nondominated, key=lambda n: (n.cost, excess(n))),
                sorted(nondominated, key=lambda n: (excess(n), n.cost)),
                sorted(nondominated, key=lambda n: (n.nonpool, n.cost))]
    selected, seen = [], set()
    for index in range(len(nondominated)):
        for ranking in rankings:
            node = ranking[index]
            if id(node) not in seen:
                selected.append(node)
                seen.add(id(node))
                if len(selected) >= width:
                    return selected
    return selected


class CostAwareStockRepair(CriticalStockRepair):
    """Same limits as the stock-first pilot; retain cheaper higher-N branches."""

    def _transport_neighbors(self, state, deadline):
        problem = self.problem

        def distance(row):
            if not row:
                return 0.0
            nodes = [problem.depot] + row + [problem.depot]
            return sum(problem.distance(a, b) for a, b in zip(nodes, nodes[1:]))

        moves, resets = [], []
        for batch, rows in state['deliveries'].items():
            if time.perf_counter() >= deadline:
                return
            if state['pickup_vehicle'][batch]:
                reset = clone_state(state)
                reset['pickup_vehicle'][batch].clear()
                resets.append((reset, dict(kind='clear_pickup_hints', batch=batch)))
            for source, row in enumerate(rows):
                for position, site in enumerate(row):
                    shortened = row[:position] + row[position + 1:]
                    for destination, other in enumerate(rows):
                        if source == destination or not other:
                            continue
                        for insert in range(len(other) + 1):
                            if time.perf_counter() >= deadline:
                                return
                            merged = other[:insert] + [site] + other[insert:]
                            delta = distance(shortened) + distance(merged) - distance(row) - distance(other)
                            # Only a distance ranking, never a feasibility or
                            # complete-cost bound (directed matrices supported).
                            if delta >= -1e-9:
                                continue
                            proposed = clone_state(state)
                            proposed['deliveries'][batch][source] = shortened
                            proposed['deliveries'][batch][destination] = merged
                            for pickup_mode in ('join_source', 'keep', 'clear'):
                                candidate = clone_state(proposed)
                                hints = candidate['pickup_vehicle'][batch]
                                if pickup_mode == 'clear':
                                    hints.clear()
                                elif pickup_mode == 'join_source':
                                    for pickup_site, vehicle in list(hints.items()):
                                        if vehicle == source:
                                            hints[pickup_site] = destination
                                    hints[site] = destination
                                moves.append((delta, candidate, dict(kind='e1_consolidate',
                                    batch=batch, site=site, source=source, destination=destination,
                                    insert=insert, pickup_mode=pickup_mode)))
        moves.sort(key=lambda item: item[0])
        # Interleave hint release and consolidation instead of allowing all
        # pickup-hint combinations to consume the per-parent candidate cap.
        for i in range(max(len(moves), len(resets))):
            if time.perf_counter() >= deadline:
                return
            if i < len(moves):
                _, candidate, move = moves[i]
                yield candidate, move
            if i < len(resets):
                yield resets[i]

    def _mixed_neighbors(self, node, target, deadline):
        loading = next((i for i, v in enumerate(node.required) if i and v > target), None)
        stock = (iter(()) if loading is None else
                 super()._neighbors(node.state, node.result, loading, deadline))
        transport = self._transport_neighbors(node.state, deadline)
        streams, seen, count = [transport, stock], {state_key(node.state)}, 0
        while streams and count < self.max_neighbors and time.perf_counter() < deadline:
            for stream in list(streams):
                try:
                    candidate, move = next(stream)
                except StopIteration:
                    streams.remove(stream)
                    continue
                key = state_key(candidate)
                if key in seen:
                    continue
                seen.add(key)
                count += 1
                yield candidate, dict(move, loading_index=loading if stream is stock else None)
                if count >= self.max_neighbors:
                    return

    def propose(self, state, decoded, evaluator, deadline):
        begun = time.perf_counter()
        root_key = state_key(state)
        if begun >= deadline or root_key in self.seen_roots:
            return None
        self.seen_roots[root_key] = True
        if len(self.seen_roots) > 64:
            self.seen_roots.popitem(last=False)
        self.calls += 1
        # At the pool lower bound, transport moves still have value, including
        # moves which increase N but reduce the complete objective.
        target = max(self.lower_bound, decoded['container_pool_size'] - 1)
        root = _Node(state, decoded, stock_requirements(self.problem, decoded), [])
        best, lowest, frontier = root, root, [root]
        seen, evaluated, feasible, depth_reached = {root_key}, 0, 0, 0
        frontier_trace = []
        for depth in range(1, self.max_depth + 1):
            children = []
            for parent in frontier:
                for candidate, move in self._mixed_neighbors(parent, target, deadline):
                    if time.perf_counter() >= deadline or evaluated >= self.max_evaluations:
                        break
                    key = state_key(candidate)
                    if key in seen:
                        continue
                    seen.add(key)
                    score, result, issues = evaluator(candidate)
                    evaluated += 1
                    if issues:
                        continue
                    feasible += 1
                    depth_reached = max(depth_reached, depth)
                    requirements = stock_requirements(self.problem, result)
                    step = dict(move, pool=result['container_pool_size'], cost=score,
                        remaining_excess=sum(max(0, v - target) for v in requirements))
                    node = _Node(candidate, result, requirements, parent.path + [step])
                    if (max(requirements), score) < (max(lowest.required), lowest.cost):
                        lowest = node
                    if score < best.cost - 1e-9:
                        best = node
                    if (node.nonpool < parent.nonpool - 1e-9 or score < parent.cost - 1e-9
                            or any(a < b for a, b in zip(requirements, parent.required))):
                        children.append(node)
                if time.perf_counter() >= deadline or evaluated >= self.max_evaluations:
                    break
            frontier = _select_frontier(children, target, self.beam_width)
            frontier_trace.append([dict(requirements=list(n.required), cost=n.cost,
                                        nonpool_cost=n.nonpool) for n in frontier])
            if (not frontier or time.perf_counter() >= deadline
                    or evaluated >= self.max_evaluations):
                break
        elapsed = time.perf_counter() - begun
        self.seconds += elapsed
        self.evaluations += evaluated
        self.feasible_evaluations += feasible
        if self.best_pool is None or (max(lowest.required), lowest.cost) < (self.best_pool, self.best_pool_cost):
            self.best_pool, self.best_pool_cost = max(lowest.required), lowest.cost
        self.last_run = dict(strategy='cost_aware', target_pool=target,
            root_pool=decoded['container_pool_size'], root_cost=root.cost,
            root_requirements=list(root.required), evaluations=evaluated,
            feasible_evaluations=feasible, reached_depth=depth_reached, seconds=elapsed,
            returned_improvement=best is not root, best_cost=best.cost, best_path=best.path,
            lowest_pool=max(lowest.required), lowest_pool_cost=lowest.cost,
            lowest_pool_path=lowest.path, frontiers=frontier_trace)
        return best.state if best is not root else None
