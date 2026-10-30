"""Exact label dominance for the optimistic M8 single-trip route library.

This produces the SAME minimum distance for EVERY feasible task mask as the
permutation enumerator. It does not produce complete original M8 schedules.

For a fixed initially loaded delivery set, visited tasks determine the load.
At the same visited mask and last order, label A dominates B only if A has
no greater distance, clock, and effective pickup-ready time for EVERY pending
delivered order. Effective readiness is max(clock, delivery+service+min_use).
Any continuation of B is then feasible from A with no greater cost. A future
delivery is also reached no later, preserving this property inductively.
No route-cost cap, input-K restriction, approximate beam or label limit is used.
"""
from __future__ import annotations

import itertools
import time


def dominates(left, right):
    """Compare (clock, distance, pending-ready-tuple) at an identical state.

    Clock/readiness are integer ticks; distance comparison uses no tolerance
    that could discard a shorter route. Pending tuple positions are fixed by
    the state mask, not by the order in which tasks were visited.
    """
    return (left[0] <= right[0] and left[1] <= right[1]
            and all(a <= b for a, b in zip(left[2], right[2])))


def enumerate_trip_library(problem, *, deadline=None, on_progress=None, stats=None):
    """Return complete minimum-distance mask columns and visited-prefix count.

    ``deadline`` is an absolute perf_counter time. A timeout raises and never
    returns a partial library. ``stats`` receives counters even on timeout.
    """
    orders = list(problem.orders)
    n = len(orders)
    policy, fleet = problem.operating_policy, problem.fleet
    routes = {}
    prefixes = kept = pruned = removed = peak_labels = 0
    begun = last_report = time.perf_counter()
    completed_sites = []
    current_site = None

    def report(complete=False):
        info = dict(prefixes=prefixes, labels_kept=kept, labels_pruned=pruned,
                    labels_removed=removed, peak_labels_per_delivery_set=peak_labels,
                    columns=len(routes), satellite=current_site,
                    completed_satellites=list(completed_sites), complete=complete,
                    seconds=time.perf_counter()-begun, algorithm='exact_label_dominance')
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
            locations = [o.customer for o in local_orders] + [site]
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
            use = [o.service_time + o.min_use_time for o in local_orders]
            global_d = [1 << i for i in local]
            global_p = [1 << (n+i) for i in local]
            local_p = [1 << (size+i) for i in range(size)]
            all_pickups = (1 << size)-1
            ready = [0] * size
            path = []

            for count in range(size+1):
                for chosen in itertools.combinations(range(size), count):
                    weight0 = sum(dw[i] for i in chosen)
                    volume0 = sum(dv[i] for i in chosen)
                    if weight0 > fleet.e2_weight_cap+1e-8 or volume0 > fleet.e2_volume_cap+1e-8:
                        continue
                    dm = sum(1 << i for i in chosen)
                    global_dm = sum(global_d[i] for i in chosen)
                    frontiers = {}
                    live_labels = 0

                    def walk(at, clock, visited, remaining, weight, volume, length, global_mask):
                        nonlocal prefixes, kept, pruned, removed, peak_labels, live_labels, last_report
                        prefixes += 1
                        if prefixes % 4096 == 0:
                            now = time.perf_counter()
                            if now-last_report >= 5:
                                report()
                                last_report = now
                            if deadline is not None and now >= deadline:
                                raise TimeoutError('Incomplete trip enumeration; no route-master bound is valid')
                        pending = dm & ~remaining & ~(visited >> size)
                        resources = []
                        while pending:
                            bit = pending & -pending
                            i = bit.bit_length()-1
                            resources.append(max(clock, ready[i]))
                            pending ^= bit
                        label = (clock, length, tuple(resources))
                        # Mathematically load is determined by the task set.
                        # Keep its floating representation in the key too:
                        # fractional data can accumulate roundoff differently.
                        key = (visited, at, weight, volume)
                        frontier = frontiers.get(key)
                        if frontier is not None:
                            if any(dominates(old, label) for old in frontier):
                                pruned += 1
                                return
                            survivors = [old for old in frontier if not dominates(label, old)]
                            lost = len(frontier)-len(survivors)
                            removed += lost
                            live_labels -= lost
                            survivors.append(label)
                            frontiers[key] = survivors
                        else:
                            frontiers[key] = [label]
                        kept += 1
                        live_labels += 1
                        peak_labels = max(peak_labels, live_labels)
                        if not remaining and global_mask:
                            end = clock+travel[at][size]+policy.e2_return_unload_time
                            trip_length = length+distance[at][size]
                            if end+policy.e2_turnaround_time <= policy.cycle_end:
                                previous = routes.get(global_mask)
                                if previous is None or trip_length < previous['distance']:
                                    routes[global_mask] = dict(mask=global_mask, deliveries=global_dm,
                                        satellite=site, distance=trip_length, path=list(path))
                        todo = remaining
                        while todo:
                            bit = todo & -todo
                            i = bit.bit_length()-1
                            todo ^= bit
                            arrive = max(clock+travel[at][i], dlo[i])
                            if arrive > dhi[i]:
                                continue
                            prior = ready[i]
                            ready[i] = arrive+use[i]
                            path.append((local[i], 'd'))
                            walk(i, arrive+service[i], visited | bit, remaining ^ bit,
                                 weight-dw[i], volume-dv[i], length+distance[at][i], global_mask | global_d[i])
                            path.pop()
                            ready[i] = prior
                        todo = all_pickups & ~(visited >> size) & ~remaining
                        while todo:
                            bit = todo & -todo
                            i = bit.bit_length()-1
                            todo ^= bit
                            if weight+pw[i] > fleet.e2_weight_cap+1e-8 or volume+pv[i] > fleet.e2_volume_cap+1e-8:
                                continue
                            earliest = max(plo[i], ready[i] if dm & bit else dlo[i]+use[i])
                            arrive = max(clock+travel[at][i], earliest)
                            if arrive > phi[i]:
                                continue
                            path.append((local[i], 'p'))
                            walk(i, arrive+service[i], visited | local_p[i], remaining,
                                 weight+pw[i], volume+pv[i], length+distance[at][i], global_mask | global_p[i])
                            path.pop()

                    walk(size, policy.cycle_start, 0, dm, weight0, volume0, 0., 0)
            completed_sites.append(site)
            report()
        report(complete=True)
        return sorted(routes.values(), key=lambda r: r['mask']), prefixes
    except BaseException:
        report()
        raise
