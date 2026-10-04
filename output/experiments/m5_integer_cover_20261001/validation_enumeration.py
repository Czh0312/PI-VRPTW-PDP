"""Independent prefix-replay oracle and full exported route-cover LP checks.

The oracle does not call production enumeration, context, or column evaluation.
It recomputes every proposed prefix's initial load and entire clock from scratch.
Shortest travel uses Floyd-Warshall rather than the production Dijkstra routine.
"""
from collections import defaultdict
import gzip
import hashlib
import json
import math
import time

CONTEXTS, ORACLES = {}, {}
ORACLE_CHECKS = []


def close(a, b):
    assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6), (a, b)


def independent_context(problem, site):
    if (problem, site) in CONTEXTS:
        return CONTEXTS[problem, site]
    places = list(problem.network.nodes)
    index = {s: i for i, s in enumerate(places)}
    shortest = [[problem.travel(a, b) for b in places] for a in places]
    for k in range(len(places)):
        for i in range(len(places)):
            for j in range(len(places)):
                shortest[i][j] = min(shortest[i][j], shortest[i][k]+shortest[k][j])
    p = problem.operating_policy
    release = {b.id: b.start+p.e1_load_time+shortest[index[problem.depot]][index[site]]
               +problem.service(site)+p.e2_dispatch_prep_time for b in p.batches}
    nodes = []
    for o in problem.orders:
        if o.satellite != site:
            continue
        inward = shortest[index[o.customer]][index[site]]
        delivered = max(o.delivery_window[0], release[o.batch]+shortest[index[site]][index[o.customer]])
        picked = max(o.pickup_window[0], delivered+o.service_time+o.min_use_time)
        for kind in ('d', 'p'):
            _, w, v = problem.task(o.id, kind)
            low, high = ((delivered, o.delivery_window[1]) if kind == 'd' else
                         (picked, min(o.pickup_window[1], p.batches[-1].cutoff-o.service_time-inward-p.e2_return_unload_time)))
            nodes.append(dict(key=(o.id, kind), batch=o.batch, location=o.customer,
                              w=w, v=v, low=low, high=high, service=o.service_time, use=o.min_use_time,
                              min_return=inward+p.e2_return_unload_time))
    context = dict(nodes=nodes, release=release,
        categories=[None]+[b.id for b in p.batches if any(n['key'][1] == 'd' and n['batch'] == b.id for n in nodes)])
    CONTEXTS[problem, site] = context
    return context


def oracle(problem, site):
    """Exhaust all prefixes; return cheapest cost per set, not a chosen route."""
    context = independent_context(problem, site)
    p, f, nodes = problem.operating_policy, problem.fleet, context['nodes']
    locations = [n['location'] for n in nodes]+[site]
    times = [[problem.travel(a, b) for b in locations] for a in locations]
    costs = [[problem.distance(a, b) for b in locations] for a in locations]
    signature = hashlib.sha256(json.dumps(dict(context=context, times=times, costs=costs,
        capacity=(f.e2_weight_cap, f.e2_volume_cap), cycle=(p.cycle_start, p.cycle_end, p.batches[-1].cutoff),
        unload=p.e2_return_unload_time, turn=p.e2_turnaround_time), sort_keys=True).encode()).hexdigest()
    if signature in ORACLES:
        return ORACLES[signature]
    begun, explored = time.perf_counter(), 0
    all_rows = []
    node_index = {n['key']: i for i, n in enumerate(nodes)}
    for category in context['categories']:
        allowed = [i for i, n in enumerate(nodes) if n['key'][1] == 'p' or n['batch'] == category]
        start = p.cycle_start if category is None else context['release'][category]
        best = {}
        def prefix_state(sequence):
            # Reconstruct ALL selected delivery loads at the depot. Do not
            # reuse the production net-load/max-prefix incremental formula.
            w = math.fsum(nodes[j]['w'] for j in sequence if nodes[j]['key'][1] == 'd')
            v = math.fsum(nodes[j]['v'] for j in sequence if nodes[j]['key'][1] == 'd')
            if w > f.e2_weight_cap+1e-6 or v > f.e2_volume_cap+1e-6:
                return None
            picked, delivered, now, previous, cost = set(), {}, start, len(nodes), 0.0
            for j in sequence:
                n, kind = nodes[j], nodes[j]['key'][1]
                oid = n['key'][0]
                if n['low'] > n['high'] or kind == 'd' and oid in picked:
                    return None
                now = max(now+times[previous][j], n['low'])
                if kind == 'p' and oid in delivered:
                    now = max(now, delivered[oid]+n['service']+n['use'])
                if now > n['high']:
                    return None
                if kind == 'd':
                    delivered[oid] = now
                    w, v = w-n['w'], v-n['v']
                else:
                    picked.add(oid)
                    w, v = w+n['w'], v+n['v']
                if not (-1e-6 <= w <= f.e2_weight_cap+1e-6 and -1e-6 <= v <= f.e2_volume_cap+1e-6):
                    return None
                now += n['service']
                cost += costs[previous][j]
                previous = j
            limit = p.cycle_end-p.e2_turnaround_time
            if picked:
                limit = min(limit, p.batches[-1].cutoff)
            if now+nodes[previous]['min_return'] > limit:
                return None
            terminal = now+times[previous][-1]+p.e2_return_unload_time <= limit
            return terminal and (bool(delivered) or category is None), cost+costs[previous][-1]
        def search(sequence, mask):
            nonlocal explored
            explored += 1
            for j in allowed:
                if mask & (1 << j):
                    continue
                proposed = sequence+(j,)
                state = prefix_state(proposed)
                if state is None:
                    continue
                next_mask = mask | (1 << j)
                if state[0]:
                    best[next_mask] = min(best.get(next_mask, math.inf), state[1])
                search(proposed, next_mask)
        search((), 0)
        all_rows.append(dict(category=category, costs=best))
    record = dict(signature=signature, satellite=site, states=explored,
                  columns=sum(len(g['costs']) for g in all_rows), runtime=time.perf_counter()-begun)
    ORACLE_CHECKS.append(record)
    ORACLES[signature] = all_rows
    print('independent universe '+json.dumps(record), flush=True)
    return all_rows


def verify_native(problem, site, universe_path, master_path, replay_column):
    import gurobipy as gp
    from gurobipy import GRB
    with gzip.open(universe_path, 'rt', encoding='utf-8') as stream:
        universe = json.load(stream)
    assert universe['complete'] and all(g['complete'] for g in universe['groups'])
    context = independent_context(problem, site['satellite'])
    keys = [n['key'] for n in context['nodes']]
    positions = {key: i for i, key in enumerate(keys)}
    assert [g['category'] for g in universe['groups']] == context['categories']
    expected = oracle(problem, site['satellite'])
    columns = []
    matrix_rows = defaultdict(set)
    witness = site['iterations'][0]
    pi = {tuple(key): value for key, value in witness['duals']}
    nu = witness['fleet_dual']
    margin = 1e-7*max(1, math.fsum(abs(x) for x in pi.values()), abs(nu))
    minima = []
    for group, independent, priced in zip(universe['groups'], expected, witness['pricing']['groups']):
        assert group['category'] == independent['category'] == priced['category']
        observed, reduced = {}, []
        for column in group['columns']:
            replay_column(problem, site['satellite'], column)
            task_keys = [tuple(k) for k in column['tasks']]
            batches = {problem.order_map[oid].batch for oid, kind in task_keys if kind == 'd'}
            assert batches == ({group['category']} if group['category'] is not None else set())
            mask = sum(1 << positions[k] for k in task_keys)
            assert mask not in observed
            observed[mask] = column['cost']
            for k in task_keys:
                matrix_rows[positions[k]].add(len(columns))
            columns.append(column)
            reduced.append(column['cost']-math.fsum(pi[k] for k in task_keys)-nu)
        assert observed.keys() == independent['costs'].keys(), (site['satellite'], group['category'], len(observed), len(independent['costs']))
        for mask, cost in observed.items():
            close(cost, independent['costs'][mask])
        assert priced['columns'] == len(observed) and priced['optimal'] and priced['method'] == 'complete_enumeration'
        if reduced:
            minimum = min(reduced)
            minima.append(minimum)
            close(priced['raw_lower_bound'], minimum)
            close(priced['lower_bound'], minimum-margin)
        else:
            assert priced['infeasible']
        close(priced['margin'], margin)
    close(witness['pricing']['raw_lower_bound'], min(minima, default=0))
    assert universe['columns'] == site['columns_generated'] == site['variables'] == len(columns)
    assert sum(g['states'] for g in universe['groups']) == universe['states'] == site['enumeration']['states']
    cover, total, used = defaultdict(float), 0, 0
    for column in site['columns']:
        value = column['lp_value']
        assert value > 0
        used += value
        total += value*column['cost']
        for key in column['tasks']:
            cover[tuple(key)] += value
    assert set(cover) == set(keys) and used <= witness['maximum_trips']+1e-6
    for value in cover.values():
        close(value, 1)
    close(total, witness['master_objective'])
    model = gp.read(str(master_path))
    try:
        assert model.NumVars == len(columns) and model.NumConstrs == len(keys)+1
        variables = model.getVars()
        for i, (var, column) in enumerate(zip(variables, columns)):
            assert var.VarName == f'route_{i}' and var.VType == GRB.CONTINUOUS and var.LB == 0 and var.UB >= GRB.INFINITY
            close(var.Obj, column['cost'])
        for i in range(len(keys)):
            row = model.getConstrByName(f'cover_{i}')
            assert row.Sense == '=' and row.RHS == 1
            expr = model.getRow(row)
            indices = set()
            for j in range(expr.size()):
                assert expr.getCoeff(j) == 1
                indices.add(expr.getVar(j).index)
            assert indices == matrix_rows[i]
        fleet = model.getConstrByName('aggregate_trip_limit')
        assert fleet.Sense == '<' and fleet.RHS == witness['maximum_trips']
        expr = model.getRow(fleet)
        assert expr.size() == len(columns)
        assert {expr.getVar(j).index for j in range(expr.size())} == set(range(len(columns)))
        assert all(expr.getCoeff(j) == 1 for j in range(expr.size()))
        model.Params.OutputFlag = 0
        model.Params.Threads = 1
        model.Params.Method = 1
        model.Params.TimeLimit = 30
        model.optimize()
        assert model.Status == GRB.OPTIMAL
        close(model.ObjVal, witness['master_objective'])
        assert site['lp_bound'] <= model.ObjVal+1e-6
        return dict(universe=str(universe_path), mps=str(master_path), full_columns=len(columns),
            reoptimized_lp_objective=model.ObjVal, retained_lp_trips=used,
            independent_complete_universe=True, full_matrix_checked=True)
    finally:
        model.dispose()
