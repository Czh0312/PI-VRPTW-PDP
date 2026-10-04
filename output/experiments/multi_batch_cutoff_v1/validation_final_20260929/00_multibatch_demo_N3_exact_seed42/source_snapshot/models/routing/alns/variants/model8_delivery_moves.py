"""Same-satellite delivery moves inside the joint M8 search.

Other operators can change E1 routes; every configured E2 vehicle is available.
Full task coverage,
initial delivery load, native/received box inventory and joint timing remain
mandatory. Neighborhood imports are local to avoid a module import cycle.
"""
from copy import deepcopy
import time

from models.routing.variants.model_8.state import TOL

DELIVERY_OPERATORS = ['delivery_relocate', 'delivery_swap', 'customer_bundle_relocate']


def delivery_move(e, current, rng, name, deadline, ceiling, allow_transfers,
                  beam, max_events, max_receivers, receiver_candidates, telemetry):
    from models.routing.variants.model_8.neighborhoods import (
        _remove_group, _insert_group, _repair_handoff, _repair_split_handoff)
    sats = [s for s, paths in current['e2_routes'].items()
            if any(n in e.tasks and e.tasks[n].kind == 'd' for r in paths for n in r)]
    if not sats:
        return None, None
    sat = rng.choice(sats)
    paths = current['e2_routes'][sat]
    owners = {n: k for k, r in enumerate(paths) for n in r if n in e.tasks}
    deliveries = [n for n in owners if e.tasks[n].kind == 'd']
    anchor = rng.choice(deliveries)
    donor = owners[anchor]
    nearby = sorted(range(len(paths)),
                    key=lambda k: min(e.distance(anchor, n) for n in paths[k]))
    others = [k for k in nearby if k != donor]
    receiver = rng.choice(others[:3]) if others and rng.random() < .8 else donor
    meta = {'satellite': sat, 'donor': donor, 'receiver': receiver, 'tasks': [anchor]}
    telemetry['delivery_move_attempts'] += 1
    proposals = []
    if name == 'delivery_swap':
        eligible = [n for n in deliveries if owners[n] != donor]
        if not eligible:
            return None, meta
        eligible.sort(key=lambda n: e.distance(anchor, n))
        other = rng.choice(eligible[:min(6, len(eligible))])
        receiver = owners[other]
        meta.update(receiver=receiver, tasks=[anchor, other])
        removed = _remove_group(current, sat, donor, {anchor})
        removed = _remove_group(removed, sat, receiver, {other})
        left = _insert_group(e, removed['e2_routes'][sat][donor], [other], beam)
        right = _insert_group(e, removed['e2_routes'][sat][receiver], [anchor], beam)
        for ra in left:
            for rb in right:
                trial = deepcopy(removed)
                trial['e2_routes'][sat][donor] = ra
                trial['e2_routes'][sat][receiver] = rb
                proposals.append(trial)
    else:
        group = [anchor]
        pickup = e.tasks[anchor].cust + 'p'
        if name == 'customer_bundle_relocate' and pickup in owners:
            group.append(pickup)
        meta['tasks'] = group
        removed = current
        for vehicle in dict.fromkeys(owners[n] for n in group):
            removed = _remove_group(removed, sat, vehicle, {n for n in group if owners[n] == vehicle})
        for route in _insert_group(e, removed['e2_routes'][sat][receiver], group, beam):
            trial = deepcopy(removed)
            trial['e2_routes'][sat][receiver] = route
            proposals.append(trial)
    best = None
    # These are finite neighborhoods; no claim of exact local optimization.
    for trial in sorted(proposals, key=e.cost)[:max(beam, 8)]:
        if time.perf_counter() >= deadline:
            break
        sent, received = e.transfer_maps(trial)
        invalid = []
        for vehicle, route in enumerate(trial['e2_routes'][sat]):
            profile, valid, carried = e.profile(route, sat, vehicle, sent, received)
            if not valid:
                initial_overload = (profile[0]['weight'] > e.problem.e2_weight_cap + TOL or
                                    profile[0]['volume'] > e.problem.e2_volume_cap + TOL)
                invalid.append((vehicle, carried, initial_overload))
        if not invalid:
            try:
                telemetry['full_audits'] += 1
                audit = e.audit(trial)
            except ValueError:
                continue
            if best is None or audit['objective'] < best[1]['objective'] - TOL:
                best = trial, audit
            continue
        if not allow_transfers or len(invalid) != 1 or not invalid[0][1] or invalid[0][2]:
            continue
        telemetry['capacity_blocked_candidates'] += 1
        target = min(ceiling, best[1]['objective'] if best else float('inf'))
        if e.cost(trial) >= target - TOL:
            continue
        sender = invalid[0][0]
        if len(paths) > 2:
            candidate, checks = _repair_split_handoff(
                e, trial, sat, sender, deadline, target, max_events,
                max_receivers, receiver_candidates, telemetry=telemetry)
            telemetry['split_repair_audits'] += checks
        else:
            others = [k for k in range(len(paths)) if k != sender]
            candidate, checks = (None, 0) if not others else _repair_handoff(
                e, trial, sat, sender, others[0], deadline, target, max_events)
        telemetry['relay_audits'] += checks
        if candidate:
            best = candidate
            telemetry['feasible_relay_repairs'] += 1
            telemetry['delivery_relay_repairs'] += 1
    return best, meta
