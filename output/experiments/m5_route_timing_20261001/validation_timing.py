"""Independent full-prefix frontier oracle using forward symbolic service times.

Production uses a backward upper-time recurrence. This oracle rebuilds
loads and a forward expression max(departure + offset, required_constant)
from scratch for EVERY proposed prefix. It imports no production pricing,
column evaluation, enumeration, or latest-departure routine.
"""
from collections import defaultdict
import gzip
import hashlib
import json
import math
import time

from validation_enumeration import independent_context

CACHE, TIMING_CHECKS = {}, []


def oracle(problem, site):
    context = independent_context(problem, site)
    p, f, nodes = problem.operating_policy, problem.fleet, context['nodes']
    locations = [n['location'] for n in nodes]+[site]
    times = [[problem.travel(a, b) for b in locations] for a in locations]
    costs = [[problem.distance(a, b) for b in locations] for a in locations]
    signature = hashlib.sha256(json.dumps(dict(context=context, times=times, costs=costs,
        capacity=(f.e2_weight_cap, f.e2_volume_cap), cycle=(p.cycle_start, p.cycle_end),
        cutoffs=[(b.id, b.cutoff) for b in p.batches], unload=p.e2_return_unload_time,
        turn=p.e2_turnaround_time), sort_keys=True).encode()).hexdigest()
    if signature in CACHE: return CACHE[signature]
    begun, explored, groups = time.perf_counter(), 0, []
    for category in context['categories']:
        allowed = [i for i, n in enumerate(nodes) if n['key'][1] == 'p' or n['batch'] == category]
        start = p.cycle_start if category is None else context['release'][category]
        best = defaultdict(list)
        def prefix_state(sequence):
            w = math.fsum(nodes[j]['w'] for j in sequence if nodes[j]['key'][1] == 'd')
            v = math.fsum(nodes[j]['v'] for j in sequence if nodes[j]['key'][1] == 'd')
            if w > f.e2_weight_cap+1e-6 or v > f.e2_volume_cap+1e-6: return None
            picked, delivered, previous, cost = set(), {}, len(nodes), 0.0
            offset, required, slack = 0, -math.inf, math.inf
            for j in sequence:
                node, kind = nodes[j], nodes[j]['key'][1]
                oid = node['key'][0]
                if node['low'] > node['high'] or kind == 'd' and oid in picked: return None
                offset += times[previous][j]
                required = max(required+times[previous][j], node['low'])
                if kind == 'p' and oid in delivered:
                    d_offset, d_required = delivered[oid]
                    lag = node['service']+node['use']
                    offset, required = max(offset, d_offset+lag), max(required, d_required+lag)
                if max(start+offset, required) > node['high']: return None
                slack = min(slack, node['high']-offset)
                if kind == 'd':
                    delivered[oid] = offset, required
                    w, v = w-node['w'], v-node['v']
                else:
                    picked.add(oid)
                    w, v = w+node['w'], v+node['v']
                if not (-1e-6 <= w <= f.e2_weight_cap+1e-6 and -1e-6 <= v <= f.e2_volume_cap+1e-6): return None
                offset += node['service']; required += node['service']
                cost += costs[previous][j]; previous = j
            now = max(start+offset, required)
            limit = min(p.cycle_end-p.e2_turnaround_time, p.batches[-1].cutoff) if picked else p.cycle_end-p.e2_turnaround_time
            if now+nodes[previous]['min_return'] > limit: return None
            returned = now+times[previous][-1]+p.e2_return_unload_time
            return (returned <= limit and (bool(delivered) or category is None),
                    cost+costs[previous][-1], returned, bool(delivered), bool(picked),
                    slack, offset+times[previous][-1]+p.e2_return_unload_time)
        def search(sequence, mask):
            nonlocal explored
            explored += 1
            for j in allowed:
                if mask & (1 << j): continue
                proposed = sequence+(j,)
                state = prefix_state(proposed)
                if state is None: continue
                next_mask = mask | (1 << j)
                terminal, cost, returned, has_d, has_p, slack, return_offset = state
                if terminal:
                    cutoffs = [(b.id, min(b.cutoff, p.cycle_end-p.e2_turnaround_time))
                        for b in p.batches if returned <= b.cutoff] if has_p else [(None, p.cycle_end-p.e2_turnaround_time)]
                    for bid, horizon in cutoffs:
                        latest = int(min(slack, horizon-return_offset)) if has_d else None
                        point, rows = (cost, latest), best[next_mask, bid]
                        if any(a <= cost and (latest is None or b >= latest) for a, b in rows): continue
                        rows[:] = [(a, b) for a, b in rows if not (cost <= a and (latest is None or latest >= b))]
                        rows.append(point)
                search(proposed, next_mask)
        search((), 0)
        groups.append(dict(category=category, frontier={
            (frozenset(nodes[i]['key'] for i in range(len(nodes)) if mask & (1 << i)), bid): set(rows)
            for (mask, bid), rows in best.items()}))
    record = dict(signature=signature, satellite=site, states=explored,
                  frontier_columns=sum(len(points) for g in groups for points in g['frontier'].values()),
                  runtime=time.perf_counter()-begun)
    CACHE[signature] = groups
    TIMING_CHECKS.append(record)
    print('independent timing frontier '+json.dumps(record), flush=True)
    return groups


def verify_timing(problem, site, path, replay_column):
    with gzip.open(path, 'rt') as stream: universe = json.load(stream)
    assert universe['complete'] and all(g['complete'] for g in universe['timing_groups'])
    expected = oracle(problem, site)
    assert [g['category'] for g in universe['timing_groups']] == [g['category'] for g in expected]
    count = 0
    for group, independent in zip(universe['timing_groups'], expected):
        observed = defaultdict(set)
        for record in group['columns']:
            column = record['column']
            replay_column(problem, site, column)
            has_d = any(kind == 'd' for _, kind in column['tasks'])
            has_p = any(kind == 'p' for _, kind in column['tasks'])
            if has_p:
                assert record['cutoff'] is not None
                assert column['return_complete'] <= problem.batch_map[record['cutoff']].cutoff
            else: assert record['cutoff'] is None
            assert (record['latest_departure'] is not None) == has_d
            if has_d:
                assert record['latest_departure'] >= column['departure']
            key = frozenset(tuple(k) for k in column['tasks']), record['cutoff']
            observed[key].add((column['cost'], record['latest_departure']))
            count += 1
        assert set(observed) == set(independent['frontier'])
        for key, points in observed.items():
            assert points == independent['frontier'][key], (site, key, points, independent['frontier'][key])
        assert len(group['columns']) == sum(len(points) for points in observed.values())
    assert count == universe['timing_columns']
    return dict(path=str(path), complete_frontier_columns=count, independently_reenumerated=True,
                forward_symbolic_latest_departure=True)


def pattern_supplies(problem, patterns):
    """Reconstruct early E1 supply clocks from raw arcs independently."""
    result = []
    for pattern in patterns:
        batch = problem.batch_map[pattern['batch']]
        now, previous, supplies = batch.start+problem.operating_policy.e1_load_time, problem.depot, {}
        for site in pattern['delivery']:
            now += problem.travel(previous, site)+problem.service(site)
            supplies[site] = now+problem.operating_policy.e2_dispatch_prep_time
            previous = site
        result.append(supplies)
    return result


def independent_supply_compression(records, nodes, site, patterns, supplies):
    """Keep the cheapest compatible path for EVERY discrete E1 supply value."""
    levels = {node['key'][0]: sorted({supplies[i][site] for i, pattern in enumerate(patterns)
        if pattern['batch'] == node['batch'] and site in pattern['delivery']})
        for node in nodes if node['key'][1] == 'd'}
    grouped, candidates = defaultdict(list), []
    for index, record in enumerate(records):
        row = dict(record, source_index=index)
        if record['latest_departure'] is not None:
            oid = next(oid for oid, kind in record['column']['tasks'] if kind == 'd')
            allowed = [value for value in levels[oid] if value <= record['latest_departure']]
            if not allowed: continue
            row['latest_departure'] = max(allowed)
        grouped[frozenset(tuple(k) for k in record['column']['tasks']), record['cutoff']].append(row)
        candidates.append(row)
    selected = set()
    for (keys, cutoff), rows in grouped.items():
        delivery = next((oid for oid, kind in keys if kind == 'd'), None)
        values = levels[delivery] if delivery is not None else [None]
        for value in values:
            allowed = [r for r in rows if value is None or r['latest_departure'] >= value]
            if allowed:
                winner = min(allowed, key=lambda r: (r['column']['cost'], -(r['latest_departure'] or 0), r['source_index']))
                selected.add(winner['source_index'])
    return [r for r in candidates if r['source_index'] in selected], levels
