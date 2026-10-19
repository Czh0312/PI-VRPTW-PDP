"""Audited earliest schedules and homogeneous vehicle labels for M1/M2 starts.

Reuse the existing B=1 timing dependency graph; no route, assignment, distance
or proof changes. Relabel E2 vehicles by trip count with stable original-ID
ties, to satisfy count symmetry without needless permutations of legal labels.
"""
from __future__ import annotations

from collections import defaultdict
import math
import time

from models.common.cycle.audit import audit_solution
from models.common.cycle.timing import normalize_schedule


def canonical_delivery_start(problem, result, *, deadline=None):
    """Re-audit an equivalent earliest start, or expire without mutating input.

    All original proof metadata is merely preserved; no new proof or bound is
    created. The explicit pure-delivery scope avoids any changed pickup rules.
    """
    if problem.model_number not in (1, 2) or problem.reusable or len(problem.operating_policy.batches) != 1:
        raise ValueError('Earliest delivery starts support only single-batch M1/M2')
    begun = time.perf_counter()
    stats = dict(status='TIME_LIMIT', proof_scope='equivalent_feasible_delivery_start',
        global_proof=False, vehicle_labels_changed=0, trip_slots_changed=0,
        departures_advanced=0, departure_advance_ticks=0, max_departure_advance=0, events_advanced=0)

    def check():
        if deadline is not None and time.perf_counter() >= deadline:
            raise TimeoutError('Delivery start normalization reached its shared budget')

    try:
        check()
        before = audit_solution(problem, result)
        if not before['valid']:
            raise ValueError('Delivery start must pass the independent audit')
        check()
        copied = normalize_schedule(problem, result)
        check()
        for layer in ('e1', 'e2'):
            originals = {t['trip_id']: t for t in result[layer+'_trips']}
            for trip in copied[layer+'_trips']:
                old = originals[trip['trip_id']]
                advance = old['departure']-trip['departure']
                if advance < 0 or any(new['start'] > prior['start'] for prior, new in zip(old['events'], trip['events'])):
                    raise RuntimeError('Delivery start transformation delayed an audited trip')
                stats['departures_advanced'] += advance > 0
                stats['departure_advance_ticks'] += advance
                stats['max_departure_advance'] = max(stats['max_departure_advance'], advance)
                stats['events_advanced'] += sum(new['start'] < prior['start'] for prior, new in zip(old['events'], trip['events']))
        inbound = sorted(copied['e1_trips'], key=lambda t: t['vehicle_id'])
        for vehicle, trip in enumerate(inbound):
            check()
            stats['vehicle_labels_changed'] += trip['vehicle_id'] != vehicle
            trip['vehicle_id'] = vehicle
        copied['e1_trips'] = inbound
        groups = defaultdict(lambda: defaultdict(list))
        for trip in copied['e2_trips']:
            groups[trip['satellite_id']][trip['vehicle_id']].append(trip)
        outbound = []
        for site, vehicles in sorted(groups.items()):
            for trips in vehicles.values():
                trips.sort(key=lambda t: t['trip_index'])
            order = sorted(vehicles, key=lambda v: (-len(vehicles[v]), v))
            for vehicle, original in enumerate(order):
                for slot, trip in enumerate(vehicles[original]):
                    check()
                    stats['vehicle_labels_changed'] += original != vehicle
                    stats['trip_slots_changed'] += trip['trip_index'] != slot
                    trip.update(vehicle_id=vehicle, trip_index=slot)
                    outbound.append(trip)
        copied['e2_trips'] = outbound
        check()
        after = audit_solution(problem, copied)
        if not after['valid'] or not math.isclose(before['objective'], after['objective'], abs_tol=1e-6, rel_tol=0):
            raise RuntimeError('Delivery start normalization changed feasibility or distance: '+str(after['errors']))
        copied.update(audit=after, cutoff_snapshots=after['cutoff_snapshots'], depot_releases=after['depot_releases'])
        stats.update(status='NORMALIZED', objective=after['objective'])
        return copied, {**stats, 'runtime': time.perf_counter()-begun}
    except TimeoutError:
        return None, {**stats, 'runtime': time.perf_counter()-begun}
