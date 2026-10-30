"""Bounded boundary/ownership/route moves from physical deadline events."""
from collections import Counter
import random
import time

from models.routing.model_8.alns.construction import _best_construction, _initial_cutoff_profiles
from models.routing.model_8.alns.polish import polish_routes
from models.routing.model_8.alns.batch_initialization import balanced_assignments


def deadline_calendar_seeds(variable):
    """Move all boundaries together to delivery/reuse deadline frontiers.

    Bounds here rank constructive proposals; only the full decoder establishes
    feasibility. No saved solution or case identifier participates.
    """
    p, parent = variable.operating_policy, variable.parent
    for assignment in balanced_assignments(variable, limit=16, width=64):
        latest, reuse = {}, {}
        for order in variable.orders:
            outward = (p.e1_load_time
                + variable.travel_bounds['outward'][variable.depot][order.satellite]
                + variable.service(order.satellite) + p.e2_dispatch_prep_time
                + variable.travel_bounds['outward'][order.satellite][order.customer])
            tail = (order.service_time
                + variable.travel_bounds['inward'][order.satellite][order.customer]
                + p.e2_return_unload_time + variable.service(order.satellite)
                + variable.travel_bounds['outward'][order.satellite][variable.depot]
                + p.depot_return_unload_time + p.cleaning_duration)
            bid = assignment[order.id]
            latest[bid] = min(latest.get(bid, p.cycle_end), order.delivery_window[1]-outward)
            reuse[bid] = max(reuse.get(bid, 0), outward+order.service_time+order.min_use_time+tail)
        boundaries = [p.cycle_start]+[p.cycle_end]*(variable.count-1)+[p.cycle_end]
        for index in range(variable.count-1, 0, -1):
            bid = p.batches[index].id
            boundaries[index] = min(latest[bid], boundaries[index+1]
                -max(2*variable.minimum_phase_ticks, reuse[bid]))
        try:
            boundaries = parent.proposed_boundaries(variable.count, boundaries)
        except ValueError:
            continue
        cuts = {}
        for index, batch in enumerate(p.batches):
            sites = {o.satellite for o in variable.orders if assignment[o.id] == batch.id}
            tail = max(variable.service(site)
                + variable.travel_bounds['outward'][site][variable.depot]
                + p.depot_return_unload_time for site in sites)
            cuts[batch.id] = boundaries[index+1]-p.cleaning_duration-tail
        yield boundaries, assignment, cuts


def polish_calendar(variable, context, boundaries, assignment, state, result, deadline, seed):
    best = (boundaries, assignment, state, result)
    score = result['info']['objective']
    p = variable.operating_policy
    minimum = 2*variable.minimum_phase_ticks
    batches = p.batches
    counts = Counter(assignment.values())
    proposals = []
    for order in variable.orders:
        old = assignment[order.id]
        if counts[old] <= 1:
            continue
        outbound = (p.e1_load_time
            + variable.travel_bounds['outward'][variable.depot][order.satellite]
            + variable.service(order.satellite) + p.e2_dispatch_prep_time
            + variable.travel_bounds['outward'][order.satellite][order.customer])
        for index, batch in enumerate(batches):
            if batch.id == old:
                continue
            changed = list(boundaries)
            latest = order.delivery_window[1]-outbound
            if index:
                low = max(changed[index-1]+minimum, order.release_time)
                high = min(changed[index+1]-minimum, latest)
                if low > high:
                    continue
                changed[index] = min(high, max(low, changed[index]))
            elif changed[0] < order.release_time or changed[0] > latest:
                continue
            changed = variable.parent.proposed_boundaries(variable.count, changed)
            proposed = {**assignment, order.id: batch.id}
            loads = Counter()
            for other in variable.orders:
                loads[proposed[other.id]] += other.count
            delta = sum(abs(a-b) for a, b in zip(changed, boundaries))
            visits = len({(other.satellite, proposed[other.id]) for other in variable.orders})
            proposals.append((max(loads.values()), visits, delta, order.id, batch.id, tuple(changed), proposed))
    rng = random.Random(seed)
    evaluations = improvements = 0
    remaining = min(12, len(proposals))
    for index, row in enumerate(sorted(proposals, key=lambda row: row[:5])[:12]):
        *_, changed, proposed = row
        if time.perf_counter() >= deadline:
            break
        child, evaluator = context(changed)
        if any(proposed[oid] not in domain for oid, domain in child.legal_batches.items()):
            continue
        cuts = {bid: min(hi, max(lo, state['cutoffs'][bid]))
                for bid, (lo, hi) in child.cutoff_bounds.items()}
        # A boundary brought forward may require the previous clean release
        # to move with it. These are proposals, never restrictions on the model.
        for batch in child.operating_policy.batches:
            tails = [trip['return_complete']-state['cutoffs'][batch.id]
                     for trip in result['e1_trips'] if trip['batch_id'] == batch.id]
            if tails:
                target = batch.end-p.cleaning_duration-max(tails)
                lo, hi = child.cutoff_bounds[batch.id]
                cuts[batch.id] = min(hi, max(lo, min(cuts[batch.id], target)))
        share = .7 if index == 0 else 1/max(1, remaining)
        local_deadline = min(deadline, time.perf_counter()+
                             max(0., deadline-time.perf_counter())*share)
        remaining -= 1
        default = _initial_cutoff_profiles(child)[0]
        # Reassignment can merge terminal pickup trips. Move one cutoff at
        # a time so repairing the last return does not erase early clean stock.
        profiles = [cuts]+[{**cuts, bid: default[bid]} for bid in reversed(cuts)]
        for profile in profiles:
            if time.perf_counter() >= local_deadline:
                break
            value, trial, candidate, _ = _best_construction(
                child, evaluator, proposed, rng, local_deadline, cutoffs=profile)
            evaluations += 1
            if candidate is None:
                continue
            trial, calls, _ = polish_routes(evaluator.fixed(proposed, trial['cutoffs']),
                                            evaluator, proposed, trial, local_deadline)
            evaluations += calls
            value, candidate, _ = evaluator(proposed, trial)
            if candidate is not None and value < score-1e-8:
                best, score = (changed, proposed, trial, candidate), value
                improvements += 1
    return best, evaluations, improvements
