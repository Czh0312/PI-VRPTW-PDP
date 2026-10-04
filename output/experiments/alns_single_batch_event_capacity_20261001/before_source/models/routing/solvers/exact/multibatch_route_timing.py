"""Complete E2 cost/latest-dispatch frontiers for joint E1 supply bounds.

For every task set and return cutoff retain all nondominated pairs of raw
cost and latest departure. This never applies cost-only dominance across
different feasible supply times. The ordinary cost-only universe is also
returned for independent LP and integer certificates.
"""
from __future__ import annotations

import time


def write_compressed_model(model, destination):
    """Preserve the exact MPS with fast level-one compression and bounded RAM."""
    import gzip
    from pathlib import Path
    import shutil
    import tempfile
    destination = Path(destination)
    with tempfile.TemporaryDirectory(prefix='m5-mps-', dir=str(destination.parent)) as directory:
        source = Path(directory)/'model.mps'
        model.write(str(source))
        with source.open('rb') as incoming, gzip.open(destination, 'wb', compresslevel=1) as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=1024*1024)


def enumerate_timed_route_universe(problem, context, *, deadline=None, max_states=2000000):
    if not isinstance(max_states, int) or isinstance(max_states, bool) or max_states < 1:
        raise ValueError('max_states must be a positive integer')
    from models.routing.common.cycle.precheck import travel_lower_bounds
    begun = time.perf_counter()
    p, f, site = problem.operating_policy, problem.fleet, context['satellite']
    travel = travel_lower_bounds(problem)
    nodes = context['nodes']
    categories = [None]+[b.id for b in p.batches if any(n['key'][1] == 'd' and n['order'].batch == b.id for n in nodes)]
    groups, timing_groups, states, completed = [], [], 0, True
    class Interrupted(Exception):
        pass
    for category in categories:
        selected = [n for n in nodes if n['key'][1] == 'p' or n['order'].batch == category]
        n = len(selected)
        keys = [node['key'] for node in selected]
        orders = [node['order'] for node in selected]
        positions = [o.customer for o in orders]
        is_delivery = [key[1] == 'd' for key in keys]
        weights = [node['weight'] for node in selected]
        volumes = [node['volume'] for node in selected]
        low = [node['window'][0] for node in selected]
        high = [node['window'][1] for node in selected]
        services = [o.service_time for o in orders]
        by_key = {key: i for i, key in enumerate(keys)}
        mates = [by_key.get((oid, 'p' if kind == 'd' else 'd'), -1) for oid, kind in keys]
        duration = [[problem.travel(a, b) for b in positions] for a in positions+[site]]
        distance = [[problem.distance(a, b) for b in positions] for a in positions+[site]]
        back_time = [problem.travel(a, site)+p.e2_return_unload_time for a in positions]
        back_cost = [problem.distance(a, site) for a in positions]
        min_back = [travel['inward'][site][a]+p.e2_return_unload_time for a in positions]
        start = p.cycle_start if category is None else context['release'][category]
        cap_w, cap_v = f.e2_weight_cap+1e-6, f.e2_volume_cap+1e-6
        best, frontier, path, starts, delivered = {}, {}, [], [], [-1]*n
        group_begun, group_states, complete = time.perf_counter(), states, True
        def latest(limit):
            upper, following = {}, None
            for j in reversed(path):
                value = high[j]
                if following is None:
                    value = min(value, limit-back_time[j]-services[j])
                else:
                    value = min(value, upper[following]-services[j]-duration[j][following])
                mate = mates[j]
                if is_delivery[j] and mate in upper:
                    value = min(value, upper[mate]-services[j]-orders[j].min_use_time)
                upper[j] = value
                following = j
            return int(upper[path[0]]-duration[n][path[0]])

        def retain(mask, full_cost, end, has_d, has_p):
            cutoffs = [(b.id, min(b.cutoff, p.cycle_end-p.e2_turnaround_time))
                       for b in p.batches if end <= b.cutoff] if has_p else [(None, p.cycle_end-p.e2_turnaround_time)]
            for bid, limit in cutoffs:
                allowed_start = latest(limit) if has_d else None
                rows = frontier.setdefault((mask, bid), [])
                if any(r['column']['cost'] <= full_cost and
                       (allowed_start is None or r['latest_departure'] >= allowed_start) for r in rows):
                    continue
                rows[:] = [r for r in rows if not (full_cost <= r['column']['cost'] and
                          (allowed_start is None or allowed_start >= r['latest_departure']))]
                column = dict(tasks=tuple(keys[j] for j in path), cost=full_cost,
                    departure=int(start), starts=list(starts), return_complete=int(end))
                rows.append(dict(column=column, cutoff=bid, latest_departure=allowed_start))

        def dfs(mask, last, clock, cost, dw, dv, pw, pv, net_w, net_v, peak_w, peak_v, has_d, has_p):
            nonlocal states
            states += 1
            if states > max_states or (states % 256 == 0 and deadline is not None and time.perf_counter() >= deadline):
                raise Interrupted
            if mask:
                end = clock+back_time[last]
                if (has_d or category is None) and end+p.e2_turnaround_time <= p.cycle_end and (not has_p or end <= p.batches[-1].cutoff):
                    full_cost = cost+back_cost[last]
                    retain(mask, full_cost, end, has_d, has_p)
                    if mask not in best or full_cost < best[mask]['cost']:
                        best[mask] = dict(tasks=tuple(keys[j] for j in path), cost=full_cost,
                            departure=int(start), starts=list(starts), return_complete=int(end))
            for j in range(n):
                bit = 1 << j
                if mask & bit or low[j] > high[j]:
                    continue
                mate = mates[j]
                if is_delivery[j] and mate >= 0 and mask & (1 << mate):
                    continue
                at = max(clock+duration[last][j], low[j])
                if not is_delivery[j] and mate >= 0 and delivered[mate] >= 0:
                    at = max(at, delivered[mate]+services[mate]+orders[j].min_use_time)
                if at > high[j]:
                    continue
                d_w, d_v = dw+(weights[j] if is_delivery[j] else 0), dv+(volumes[j] if is_delivery[j] else 0)
                p_w, p_v = pw+(0 if is_delivery[j] else weights[j]), pv+(0 if is_delivery[j] else volumes[j])
                delta_w, delta_v = net_w+weights[j]*(-1 if is_delivery[j] else 1), net_v+volumes[j]*(-1 if is_delivery[j] else 1)
                peak_w_new, peak_v_new = max(peak_w, delta_w), max(peak_v, delta_v)
                # Initial load is ALL deliveries selected anywhere on the
                # path. Future deliveries increase every earlier load, so
                # these partial-path violations can never be repaired.
                if d_w+peak_w_new > cap_w or d_v+peak_v_new > cap_v or p_w > cap_w or p_v > cap_v:
                    continue
                has_pickup = has_p or not is_delivery[j]
                limit = min(p.cycle_end-p.e2_turnaround_time, p.batches[-1].cutoff) if has_pickup else p.cycle_end-p.e2_turnaround_time
                # Direct return may be slower than a detour on nonmetric
                # inputs. Only the shortest-time lower bound can prune.
                if at+services[j]+min_back[j] > limit:
                    continue
                path.append(j)
                starts.append(int(at))
                if is_delivery[j]:
                    delivered[j] = at
                dfs(mask | bit, j, at+services[j], cost+distance[last][j],
                    d_w, d_v, p_w, p_v, delta_w, delta_v, peak_w_new, peak_v_new,
                    has_d or is_delivery[j], has_pickup)
                if is_delivery[j]:
                    delivered[j] = -1
                path.pop()
                starts.pop()
        try:
            if deadline is not None and time.perf_counter() >= deadline:
                raise Interrupted
            dfs(0, n, start, 0.0, 0, 0, 0, 0, 0, 0, 0, 0, False, False)
        except Interrupted:
            completed, complete = False, False
        groups.append(dict(category=category, complete=complete, states=states-group_states,
            columns=list(best.values()), runtime=time.perf_counter()-group_begun))
        timing_groups.append(dict(category=category, complete=complete,
            columns=[r for rows in frontier.values() for r in rows]))
        if not complete:
            break
    return dict(complete=completed and len(groups) == len(categories), groups=groups, timing_groups=timing_groups,
                timing_columns=sum(len(g['columns']) for g in timing_groups), states=states, columns=sum(len(g['columns']) for g in groups), runtime=time.perf_counter()-begun)
