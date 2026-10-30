"""Research-only all-K lower bound with one shared E2 trip library.

This is a RELAXATION, never a replacement for M8's audited exact solver.
For one-box orders, retain productive batches, optimistic time/stock chains,
and same-batch delivery trips. Relax route synchronization, vehicle schedules,
cutoff-all-take and the integer time grids. All K share the same route columns.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.common.cycle import load_instance
from models.common.cycle.audit import audit_solution
from models.routing.model_8.exact.parameters import necessary_time_bounds
from models.routing.model_8.exact.preprocessing import temporal_assignment_bounds
from tools.configuration.loader import load_routing_case


def enumerate_trip_library(problem, *, deadline=None, on_progress=None):
    """Enumerate every relaxed task sequence; keep minimum distance per task set.

    Earliest forward propagation is exact for this single-trip feasibility
    test: all precedence/min-use constraints point forward. Delivery sets are
    fixed before walking, so initial capacity cannot be underestimated.
    Cross-trip pickup readiness uses only the order's delivery-window lower
    bound. No K, batch assignment or incumbent is used here.
    """
    orders = list(problem.orders)
    n = len(orders)
    p, fleet = problem.operating_policy, problem.fleet
    routes, prefixes = {}, 0
    last_report = time.perf_counter()
    for site in problem.satellites:
        local = [i for i, o in enumerate(orders) if o.satellite == site]
        for size in range(len(local) + 1):
            for chosen in itertools.combinations(local, size):
                weight0 = sum(problem.task(orders[i].id, 'd')[1] for i in chosen)
                volume0 = sum(problem.task(orders[i].id, 'd')[2] for i in chosen)
                if weight0 > fleet.e2_weight_cap + 1e-8 or volume0 > fleet.e2_volume_cap + 1e-8:
                    continue
                dm = sum(1 << i for i in chosen)
                delivered_at = {}

                def walk(at, clock, mask, remaining, weight, volume, distance, path):
                    nonlocal prefixes, last_report
                    prefixes += 1
                    if prefixes % 4096 == 0:
                        now = time.perf_counter()
                        if on_progress is not None and now - last_report >= 5:
                            on_progress(dict(prefixes=prefixes, columns=len(routes), satellite=site))
                            last_report = now
                        if deadline is not None and now >= deadline:
                            if on_progress is not None:
                                on_progress(dict(prefixes=prefixes, columns=len(routes), satellite=site))
                            raise TimeoutError("Incomplete trip enumeration; no route-master bound is valid")
                    if not remaining and mask:
                        end = clock + problem.travel(at, site) + p.e2_return_unload_time
                        length = distance + problem.distance(at, site)
                        if end + p.e2_turnaround_time <= p.cycle_end:
                            if mask not in routes or length < routes[mask]['distance']:
                                routes[mask] = dict(mask=mask, deliveries=dm, satellite=site,
                                                    distance=length, path=list(path))
                    for i in chosen:
                        if not remaining & (1 << i):
                            continue
                        o = orders[i]
                        arrive = max(clock + problem.travel(at, o.customer), o.delivery_window[0])
                        if arrive > o.delivery_window[1]:
                            continue
                        _, dw, dv = problem.task(o.id, 'd')
                        delivered_at[i] = arrive
                        walk(o.customer, arrive + o.service_time, mask | (1 << i),
                             remaining ^ (1 << i), weight - dw, volume - dv,
                             distance + problem.distance(at, o.customer), path + [(i, 'd')])
                        del delivered_at[i]
                    for i in local:
                        o, bit = orders[i], 1 << (n + i)
                        if mask & bit or remaining & (1 << i):
                            continue
                        _, pw, pv = problem.task(o.id, 'p')
                        if weight + pw > fleet.e2_weight_cap + 1e-8 or volume + pv > fleet.e2_volume_cap + 1e-8:
                            continue
                        earliest = max(o.pickup_window[0],
                                       delivered_at.get(i, o.delivery_window[0]) + o.service_time + o.min_use_time)
                        arrive = max(clock + problem.travel(at, o.customer), earliest)
                        if arrive > o.pickup_window[1]:
                            continue
                        walk(o.customer, arrive + o.service_time, mask | bit, remaining,
                             weight + pw, volume + pv, distance + problem.distance(at, o.customer),
                             path + [(i, 'p')])

                # Dropping dispatch preparation/supply only enlarges feasibility.
                walk(site, p.cycle_start, 0, dm, weight0, volume0, 0., [])
    return list(routes.values()), prefixes


def shortest_distance_closure(problem):
    nodes = list(problem.network.nodes)
    d = {(i, j): problem.distance(i, j) for i in nodes for j in nodes}
    for k in nodes:
        for i in nodes:
            for j in nodes:
                d[i, j] = min(d[i, j], d[i, k] + d[k, j])
    return d


def build_relaxation(problem, library):
    """One continuous-time shared-calendar master for the COMPLETE input K set."""
    import gurobipy as gp
    from gurobipy import GRB

    if problem.model_number != 8 or any(o.count != 1 for o in problem.orders):
        raise ValueError('Research relaxation currently requires M8 one-box orders')
    if len(problem.orders) > 20:
        raise ValueError('Exhaustive research probe is restricted to at most twenty orders')
    rates = problem.cost_profile.rates
    if any(v < 0 for v in rates.values() if isinstance(v, (int, float))):
        raise ValueError('Dropping cost terms requires nonnegative rates')
    orders, p = list(problem.orders), problem.operating_policy
    n = len(orders)
    counts = [k for k in problem.batch_count_candidates if k <= n]
    if not counts:
        raise ValueError('No productive K remains')
    B = max(counts)
    m = gp.Model('research_m8_shared_allK_lower_bound')
    m.Params.OutputFlag = 0
    m.Params.Threads = 1
    m.Params.Seed = 42
    m.Params.MIPGap = 0
    m.Params.MIPGapAbs = 0
    z = m.addVars(B, vtype=GRB.BINARY, name='active')
    selectors = m.addVars(counts, vtype=GRB.BINARY, name='choose_K')
    m.addConstr(selectors.sum() == 1)
    m.addConstr(z.sum() == gp.quicksum(k * selectors[k] for k in counts))
    for b in range(B - 1):
        m.addConstr(z[b] >= z[b + 1])
    t = m.addVars(B + 1, lb=p.cycle_start, ub=p.cycle_end, name='boundary')
    cut = m.addVars(B, lb=p.cycle_start, ub=p.cycle_end, name='cutoff')
    m.addConstr(t[0] == p.cycle_start)
    m.addConstr(t[B] == p.cycle_end)
    for b in range(B):
        m.addConstr(cut[b] >= t[b] + problem.minimum_phase_ticks * z[b])
        m.addConstr(cut[b] <= t[b + 1] - problem.minimum_phase_ticks * z[b])
        m.addConstr(t[b + 1] - t[b] <= problem.cycle_duration * z[b])
    bounds = necessary_time_bounds(problem)
    pool = m.addVar(lb=max(problem.pool_min, bounds['pool_lower_bound']), ub=problem.pool_max,
                    vtype=GRB.INTEGER, name='pool')
    y = m.addVars(n, B, vtype=GRB.BINARY, name='delivery_batch')
    w = m.addVars(n, B, vtype=GRB.BINARY, name='recovery_batch')
    clean = m.addVars(n, lb=p.cycle_start, ub=p.cycle_end, name='clean')
    delivery = {i: m.addVar(lb=o.delivery_window[0], ub=o.delivery_window[1], name=f'delivery_{i}')
                for i, o in enumerate(orders)}
    pickup = {i: m.addVar(lb=o.pickup_window[0], ub=o.pickup_window[1], name=f'pickup_{i}')
              for i, o in enumerate(orders)}
    latest = {r['order']: r['latest_loading'] for r in bounds['intervals']}
    outward, inward = problem.travel_bounds['outward'], problem.travel_bounds['inward']
    for i, o in enumerate(orders):
        site = o.satellite
        inbound = p.e1_load_time + outward[problem.depot][site] + problem.service(site)
        supply = inbound + p.e2_dispatch_prep_time + outward[site][o.customer]
        return_to_site = o.service_time + inward[site][o.customer] + p.e2_return_unload_time
        tail = problem.service(site) + outward[site][problem.depot] + p.depot_return_unload_time
        m.addConstr(y.sum(i, '*') == 1)
        m.addConstr(w.sum(i, '*') == 1)
        m.addConstr(pickup[i] >= delivery[i] + o.service_time + o.min_use_time)
        for b in range(B):
            m.addConstr(y[i, b] <= z[b])
            m.addConstr(w[i, b] <= z[b])
            m.addConstr(w[i, b] <= gp.quicksum(y[i, h] for h in range(b + 1)))
            m.addConstr((y[i, b] == 1) >> (t[b] >= o.release_time))
            m.addConstr((y[i, b] == 1) >> (t[b] <= latest[o.id]))
            m.addConstr((y[i, b] == 1) >> (delivery[i] >= t[b] + supply))
            m.addConstr((y[i, b] == 1) >> (cut[b] >= t[b] + inbound))
            m.addConstr((w[i, b] == 1) >> (cut[b] >= pickup[i] + return_to_site))
            m.addConstr((w[i, b] == 1) >> (clean[i] >= cut[b] + tail + p.cleaning_duration))
            m.addConstr((w[i, b] == 1) >> (clean[i] <= t[b + 1] + p.cleaning_duration))
    for row in bounds['forced_first_batch']:
        m.addConstr(y[next(i for i, o in enumerate(orders) if o.id == row['order']), 0] == 1)
    for b in range(B):
        demand = gp.quicksum(y[i, b] for i in range(n))
        m.addConstr(demand >= z[b])
        m.addConstr(demand <= pool)
        m.addConstr(gp.quicksum(w[i, b] for i in range(n)) >= z[b])
        ready = m.addVars(n, vtype=GRB.BINARY, name=f'clean_before_{b}')
        for i in range(n):
            m.addConstr(ready[i] <= gp.quicksum(w[i, h] for h in range(b)))
            m.addConstr((ready[i] == 1) >> (clean[i] <= t[b]))
        m.addConstr(gp.quicksum(y[i, h] for i in range(n) for h in range(b + 1)) <= pool + ready.sum())

    # Input-only certificates stay selectors=0; no K is silently discarded.
    screening = {}
    for k in counts:
        bd = temporal_assignment_bounds(problem.for_count(k))
        if bd['certificate'] is not None:
            m.addConstr(selectors[k] == 0)
            screening[k] = bd['certificate']['reason']
        m.addConstr(pool >= bd['pool_lower_bound'] * selectors[k])

    route = m.addVars(len(library), vtype=GRB.BINARY, name='trip_column')
    for task in range(2 * n):
        m.addConstr(gp.quicksum(route[r] for r, col in enumerate(library) if col['mask'] & (1 << task)) == 1)
    groups = sorted({col['deliveries'] for col in library if col['deliveries'].bit_count() > 1})
    same = m.addVars(groups, B, lb=0, ub=1, name='delivery_group')
    for group in groups:
        for b in range(B):
            for i in range(n):
                if group & (1 << i):
                    m.addConstr(same[group, b] <= y[i, b])
    for r, col in enumerate(library):
        if col['deliveries'] in groups:
            m.addConstr(route[r] <= gp.quicksum(same[col['deliveries'], b] for b in range(B)))

    # Concatenate actual E1 tours at the depot and shortcut in a shortest-path
    # metric: a visit-set tour lower-bounds their TOTAL distance, even for
    # nonmetric inputs. Delivery/recovery phase revisits are deliberately relaxed.
    d = shortest_distance_closure(problem)
    sites = list(problem.satellites)
    patterns = []
    for mask in range(1, 1 << len(sites)):
        subset = [s for i, s in enumerate(sites) if mask & (1 << i)]
        cost = min(sum(d[a, b] for a, b in zip((problem.depot,) + perm, perm + (problem.depot,)))
                   for perm in itertools.permutations(subset))
        patterns.append((mask, cost))
    alpha = m.addVars(B, len(patterns), vtype=GRB.BINARY, name='e1_visit_set')
    for b in range(B):
        m.addConstr(gp.quicksum(alpha[b, h] for h in range(len(patterns))) == z[b])
        for i, o in enumerate(orders):
            idx = sites.index(o.satellite)
            covered = gp.quicksum(alpha[b, h] for h, (mask, _) in enumerate(patterns) if mask & (1 << idx))
            m.addConstr(y[i, b] <= covered)
            m.addConstr(w[i, b] <= covered)
    e1_distance = gp.quicksum(alpha[b, h] * cost for b in range(B) for h, (_, cost) in enumerate(patterns))
    e2_distance = gp.quicksum(route[r] * col['distance'] for r, col in enumerate(library))
    constant = sum(problem.cost_profile.constant_cost(problem.total).values())
    m.setObjective(rates['container_pool_cost_per_period'] * pool + constant + rates['distance_scale_to_km'] *
                   (rates['e1_distance_cost_per_km'] * e1_distance + rates['e2_distance_cost_per_km'] * e2_distance))
    m.update()
    return m, dict(pool=pool, selectors=selectors, assignments=y, recovery=w, route=route,
                   e1_distance=e1_distance, e2_distance=e2_distance, screening=screening)


def run_case(case, seconds, out):
    case_path = ROOT / f'data/formal_benchmark27_uniform_7_20_50_v1/model_8/small/{case}/case.json'
    cfg, fleet = load_routing_case(case_path, use_pool_config=False)
    physical_paths = [case_path] + [ROOT / cfg[k] for k in ('data', 'orders', 'routing_config', 'operating_policy', 'economic_config') if isinstance(cfg.get(k), str)]
    input_hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in physical_paths}
    source_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    problem = load_instance(cfg, fleet)
    begun = time.perf_counter()
    library, prefixes = enumerate_trip_library(problem)
    enum_time = time.perf_counter() - begun
    model, ctx = build_relaxation(problem, library)
    build_time = time.perf_counter() - begun - enum_time
    try:
        model.Params.TimeLimit = seconds
        # Gurobi 11's Windows log path handling needs an ASCII relative path.
        model.Params.LogFile = str((out / f'{case}.log').relative_to(ROOT))
        model.optimize()
        row = dict(case=case, kind='LOWER_BOUND_RELAXATION_NOT_M8_SOLUTION',
                   input_K=list(problem.batch_count_candidates), status=model.Status,
                   bound=model.ObjBound if math.isfinite(model.ObjBound) else None,
                   relaxation_objective=model.ObjVal if model.SolCount else None,
                   variables=model.NumVars, binary=model.NumBinVars, linear_rows=model.NumConstrs,
                   indicators=model.NumGenConstrs, route_columns=len(library), enumerated_prefixes=prefixes,
                   enumeration_seconds=enum_time, build_seconds=build_time, optimize_seconds=model.Runtime,
                   work=model.Work, nodes=model.NodeCount, screened_counts=ctx['screening'],
                   relaxation_K=next((k for k, v in ctx['selectors'].items() if v.X > .5), None) if model.SolCount else None,
                   relaxation_pool=ctx['pool'].X if model.SolCount else None,
                   source_sha256=source_hash)
        witnesses = [ROOT / f'output/experiments/small7_m8_alns300_20261024_v1/runs/{case}_m8_alns_300s_s42/attempt_001/result.json']
        witnesses.append(ROOT / f'output/experiments/m8_small7_exact_alns_gap_20261024_v1/runs/exact/{case}/physical_time_result.json')
        witnesses.append(ROOT / f'output/experiments/m8_{case.lower()}_cold300_20261024_v1/run_frozen/physical_time_result.json')
        if case == 'S01':
            witnesses.append(ROOT / 'output/experiments/m8_stock_interval_probe_20261024/full_domain_certificate/audited_witness_with_certificate.json')
        audited = []
        for path in witnesses:
            if not path.exists():
                continue
            saved = json.loads(path.read_text(encoding='utf-8-sig'))
            audit = audit_solution(problem, saved)
            audited.append(dict(path=str(path.relative_to(ROOT)), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                valid=audit['valid'], periodic=audit.get('periodic_replay', {}).get('valid'),
                                objective=audit.get('objective')))
        row['witness_audits'] = audited
        valid = [a['objective'] for a in audited if a['valid'] and a['periodic'] and a['objective'] is not None]
        row['audited_upper_bound'] = min(valid) if valid else None
        if valid and row['bound'] is not None:
            assert row['bound'] <= min(valid) + 1e-5, row
            row['certified_gap_upper_percent'] = 100 * (min(valid) - row['bound']) / min(valid)
        assert input_hashes == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in physical_paths}
        assert source_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        row['input_hashes'] = input_hashes
        (out / f'{case}.json').write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
        (out / f'{case}_trip_library.json').write_text(json.dumps(library, ensure_ascii=False), encoding='utf-8')
        return row
    finally:
        model.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cases', nargs='+', default=[f'S{i:02d}' for i in range(1, 10)])
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--out', type=Path, default=ROOT / 'output/experiments/m8_shared_relaxation_research_20261026')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for case in args.cases:
        row = run_case(case, args.seconds, args.out)
        print(json.dumps({k: v for k, v in row.items() if k not in ('input_hashes', 'witness_audits')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
