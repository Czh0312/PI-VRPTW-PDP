"""Complete optimistic M8 trip library using layered Pareto labels.

For each initially loaded delivery subset, a state fixes visited tasks,
last order and the floating representation of load. A dominates B only if
distance, clock and EVERY pending pickup's effective ready time are no
greater. Effective readiness is max(clock, delivery+service+minimum use).
Every continuation of B can then be replayed from A no later or dearer.
Future deliveries also occur no later, so the invariant propagates.

Task count is a topological order. Finishing each layer before expansion
ensures dominated labels are removed BEFORE their descendants are searched.
All feasible masks retain their minimum distance. No beam, cost cap or K
restriction is used. A partial library is never returned on timeout.
"""
from __future__ import annotations

from functools import lru_cache
import itertools
import time


def dominates(left, right):
    """Resource dominance at an identical state; no distance tolerance."""
    return (left[0] <= right[0] and left[1] <= right[1]
            and all(a <= b for a, b in zip(left[2], right[2])))


def enumerate_trip_library(problem, *, deadline=None, on_progress=None, stats=None):
    """Return all feasible task masks at minimum relaxed trip distance.

    ``deadline`` is an absolute perf_counter time. The optional stats dict
    receives counters on success and timeout; it contains no partial routes.
    """
    orders = list(problem.orders)
    n = len(orders)
    policy, fleet = problem.operating_policy, problem.fleet
    routes = {}
    prefixes = kept = pruned = removed = expanded = peak = 0
    begun = last_report = time.perf_counter()
    completed_sites = []
    current_site = None

    def report(complete=False):
        info = dict(prefixes=prefixes, labels_kept=kept, labels_pruned=pruned,
            labels_removed=removed, expanded_labels=expanded, peak_layer_labels=peak,
            columns=len(routes), satellite=current_site,
            completed_satellites=list(completed_sites), complete=complete,
            seconds=time.perf_counter()-begun, algorithm='exact_layered_label_dominance')
        if stats is not None:
            stats.update(info)
        if on_progress is not None:
            on_progress(info)

    try:
        for site in problem.satellites:
            current_site = site
            if deadline is not None and time.perf_counter() >= deadline:
                raise TimeoutError('Incomplete trip enumeration; no route-master bound is valid')
            local = [i for i, o in enumerate(orders) if o.satellite == site]
            size = len(local)
            if not size:
                completed_sites.append(site)
                continue
            local_orders = [orders[i] for i in local]
            locations = [o.customer for o in local_orders]+[site]
            travel = [[problem.travel(a, b) for b in locations] for a in locations]
            distance = [[problem.distance(a, b) for b in locations] for a in locations]
            dw = [problem.task(o.id, 'd')[1] for o in local_orders]
            dv = [problem.task(o.id, 'd')[2] for o in local_orders]
            pw = [problem.task(o.id, 'p')[1] for o in local_orders]
            pv = [problem.task(o.id, 'p')[2] for o in local_orders]
            dlo = [o.delivery_window[0] for o in local_orders]
            dhi = [o.delivery_window[1] for o in local_orders]
            plo = [o.pickup_window[0] for o in local_orders]
            phi = [o.pickup_window[1] for o in local_orders]
            service = [o.service_time for o in local_orders]
            use = [o.service_time+o.min_use_time for o in local_orders]
            all_tasks = (1 << size)-1

            @lru_cache(None)
            def members(mask):
                result = []
                while mask:
                    bit = mask & -mask
                    result.append(bit.bit_length()-1)
                    mask ^= bit
                return tuple(result)

            @lru_cache(None)
            def global_bits(mask):
                return sum(1 << local[i] for i in members(mask))

            for count in range(size+1):
                for chosen in itertools.combinations(range(size), count):
                    weight0 = sum(dw[i] for i in chosen)
                    volume0 = sum(dv[i] for i in chosen)
                    if weight0 > fleet.e2_weight_cap+1e-8 or volume0 > fleet.e2_volume_cap+1e-8:
                        continue
                    dm = sum(1 << i for i in chosen)
                    global_dm = global_bits(dm)
                    layer = {(0, size, weight0, volume0): [(policy.cycle_start, 0., (), None)]}
                    prefixes += 1
                    kept += 1
                    while layer:
                        next_layer = {}
                        next_count = 0

                        def insert(visited, at, weight, volume, clock, length, resources, previous, event):
                            nonlocal prefixes, kept, pruned, removed, next_count, peak, last_report
                            prefixes += 1
                            if prefixes % 4096 == 0:
                                now = time.perf_counter()
                                if now-last_report >= 5:
                                    report()
                                    last_report = now
                                if deadline is not None and now >= deadline:
                                    raise TimeoutError('Incomplete trip enumeration; no route-master bound is valid')
                            # Include load's actual floating representation to
                            # preserve boundary behavior on fractional inputs.
                            key = (visited, at, weight, volume)
                            label = (clock, length, resources)
                            frontier = next_layer.get(key)
                            if frontier is not None:
                                if any(dominates(old, label) for old in frontier):
                                    pruned += 1
                                    return
                                survivors = [old for old in frontier if not dominates(label, old)]
                                lost = len(frontier)-len(survivors)
                                removed += lost
                                next_count -= lost
                            else:
                                survivors = []
                            node = (event[0], event[1], previous)
                            survivors.append((clock, length, resources, node))
                            next_layer[key] = survivors
                            kept += 1
                            next_count += 1
                            peak = max(peak, next_count)

                        for (visited, at, weight, volume), labels in layer.items():
                            delivered = visited & all_tasks
                            picked = visited >> size
                            remaining = dm & ~delivered
                            pending = delivered & ~picked
                            pending_indices = members(pending)
                            d_options = members(remaining)
                            p_options = members(all_tasks & ~picked & ~remaining)
                            for clock, length, resources, node in labels:
                                expanded += 1
                                ready = dict(zip(pending_indices, resources))
                                if not remaining and visited:
                                    end = clock+travel[at][size]+policy.e2_return_unload_time
                                    full_length = length+distance[at][size]
                                    if end+policy.e2_turnaround_time <= policy.cycle_end:
                                        mask = global_bits(delivered) | (global_bits(picked) << n)
                                        old = routes.get(mask)
                                        if old is None or full_length < old['distance']:
                                            sequence, current = [], node
                                            while current is not None:
                                                sequence.append((local[current[0]], current[1]))
                                                current = current[2]
                                            sequence.reverse()
                                            routes[mask] = dict(mask=mask, deliveries=global_dm,
                                                satellite=site, distance=full_length, path=sequence)
                                for i in d_options:
                                    bit = 1 << i
                                    arrive = max(clock+travel[at][i], dlo[i])
                                    if arrive > dhi[i]:
                                        continue
                                    new_clock = arrive+service[i]
                                    readiness = tuple(max(new_clock, arrive+use[i] if j == i else ready[j])
                                                      for j in members(pending | bit))
                                    insert(visited | bit, i, weight-dw[i], volume-dv[i], new_clock,
                                           length+distance[at][i], readiness, node, (i, 'd'))
                                for i in p_options:
                                    bit = 1 << i
                                    if weight+pw[i] > fleet.e2_weight_cap+1e-8 or volume+pv[i] > fleet.e2_volume_cap+1e-8:
                                        continue
                                    earliest = max(plo[i], ready[i] if dm & bit else dlo[i]+use[i])
                                    arrive = max(clock+travel[at][i], earliest)
                                    if arrive > phi[i]:
                                        continue
                                    new_clock = arrive+service[i]
                                    readiness = tuple(max(new_clock, ready[j]) for j in members(pending & ~bit))
                                    insert(visited | (bit << size), i, weight+pw[i], volume+pv[i], new_clock,
                                           length+distance[at][i], readiness, node, (i, 'p'))
                        layer = next_layer
            completed_sites.append(site)
            report()
        report(complete=True)
        return sorted(routes.values(), key=lambda r: r['mask']), prefixes
    except BaseException:
        report()
        raise
