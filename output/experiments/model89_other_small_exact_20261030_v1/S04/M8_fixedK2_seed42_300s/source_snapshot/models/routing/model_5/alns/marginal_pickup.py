"""Experimental E1 pickup reassignment, ranked by marginal transport cost.

Decoded route deltas only rank proposals: the unchanged full decoder may choose
different insertion positions and must recompute clean releases and pool cost.
"""
import time

from models.routing.model_5.alns.cost_chain import CostAwareStockRepair
from models.routing.model_5.alns.evaluator import _e1_trip, route_distance
from models.routing.model_5.alns.state import clone_state, state_key


def _route_cost(problem, batch, trip, used_elsewhere):
    if trip is None:
        return 0.0
    rates = problem.cost_profile.rates
    begin = (batch.start if any(e['kind'] == 'd' for e in trip['events'])
             else trip['departure'])
    return (route_distance(problem, trip, 'e1') * rates['distance_scale_to_km']
            * rates['e1_distance_cost_per_km']
            + problem.operating_policy.minutes(trip['return_complete'] - begin)
            * rates['e1_work_cost_per_minute'] + rates['e1_trip_cost']
            + (0 if used_elsewhere else rates['e1_vehicle_cost_per_cycle']))


def marginal_pickup_neighbors(problem, state, decoded, deadline):
    """Move one positive pickup to another truck without changing E2/deliveries.

Only locally feasible, transport-saving choices are proposed. This is a limited
heuristic neighborhood, not a safe pruning rule for the full problem. Future
vehicle reuse, changed pickup insertion order and N require full evaluation.
    """
    policy, fleet = problem.operating_policy, problem.fleet
    by_batch = {b.id: {} for b in policy.batches}
    for trip in decoded['e1_trips']:
        by_batch[trip['batch_id']][trip['vehicle_id']] = trip
    ready = [policy.cycle_start] * fleet.e1_count
    moves = []
    for batch in policy.batches:
        if time.perf_counter() >= deadline:
            break
        trips = by_batch[batch.id]
        pickups = {k: [e['site_id'] for e in t['events'] if e['kind'] == 'p']
                   for k, t in trips.items()}
        quantities = {e['site_id']: e['quantity'] for t in trips.values()
                      for e in t['events'] if e['kind'] == 'p'}
        actual = {site: k for k, sites in pickups.items() for site in sites}
        elsewhere = {k for bid, rows in by_batch.items() if bid != batch.id for k in rows}
        costs = {k: _route_cost(problem, batch, trips.get(k), k in elsewhere)
                 for k in range(fleet.e1_count)}
        for site, source in actual.items():
            shortened = [s for s in pickups[source] if s != site]
            source_trip, source_issues = _e1_trip(problem, batch, source,
                state['deliveries'][batch.id][source], shortened, quantities, ready[source])
            if source_issues:
                continue
            source_delta = (_route_cost(problem, batch, source_trip, source in elsewhere)
                            - costs[source])
            for destination in range(fleet.e1_count):
                if source == destination:
                    continue
                other = pickups.get(destination, [])
                best = None
                for position in range(len(other) + 1):
                    if time.perf_counter() >= deadline:
                        return
                    merged = other[:position] + [site] + other[position:]
                    trip, issues = _e1_trip(problem, batch, destination,
                        state['deliveries'][batch.id][destination], merged, quantities,
                        ready[destination])
                    if issues:
                        continue
                    delta = (source_delta + _route_cost(problem, batch, trip,
                                                      destination in elsewhere) - costs[destination])
                    if best is None or delta < best[0]:
                        best = (delta, position)
                if best is None or best[0] >= -1e-9:
                    continue
                proposed = clone_state(state)
                # Anchor other positive pickups to their actual decoded trucks;
                # stale/missing hints must not silently move unrelated pickups.
                proposed['pickup_vehicle'][batch.id].update(actual)
                proposed['pickup_vehicle'][batch.id][site] = destination
                moves.append((best[0], proposed, dict(kind='e1_marginal_pickup',
                    batch=batch.id, site=site, source=source, destination=destination,
                    predicted_transport_delta=best[0], ranked_insertion=best[1])))
        for k, trip in trips.items():
            ready[k] = trip['return_complete'] + policy.e1_turnaround_time
    moves.sort(key=lambda item: item[0])
    for _, candidate, move in moves:
        if time.perf_counter() >= deadline:
            return
        yield candidate, move


class MarginalPickupStockRepair(CostAwareStockRepair):
    """Add one candidate stream within the existing width/time/evaluation caps."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.marginal_generated = self.marginal_evaluations = self.marginal_feasible = 0
        self._evaluating_marginal = False

    def _mixed_neighbors(self, node, target, deadline):
        streams = [marginal_pickup_neighbors(self.problem, node.state, node.result, deadline),
                   super()._mixed_neighbors(node, target, deadline)]
        seen, count = {state_key(node.state)}, 0
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
                self._evaluating_marginal = move.get('kind') == 'e1_marginal_pickup'
                self.marginal_generated += int(self._evaluating_marginal)
                yield candidate, move
                if count >= self.max_neighbors:
                    return

    def propose(self, state, decoded, evaluator, deadline):
        def recorded(candidate):
            value = evaluator(candidate)
            if self._evaluating_marginal:
                self.marginal_evaluations += 1
                self.marginal_feasible += int(not value[2])
            return value

        result = super().propose(state, decoded, recorded, deadline)
        if self.last_run is not None:
            self.last_run['strategy'] = 'cost_aware_marginal'
        return result
