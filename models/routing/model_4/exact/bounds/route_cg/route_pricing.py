"""Elementary delivery-trip pricing for the experimental B=1 route bound.

Only M1/M2 are supported. Task identities, directed/nonmetric arcs, integer
time windows, departure capacity and terminal unloading are retained. Actual
E1 supply and physical E2 vehicle sequencing are relaxed by the caller.
"""
from __future__ import annotations

import heapq
import math
import time

from models.routing.model_4.exact.bounds.route_cg.time_budget import arc_work, node_work, reduced_route_cost, validate_work_prices


def delivery_context(problem, satellite, domains, windows, travel):
    p, f = problem.operating_policy, problem.fleet
    batch = p.batches[0]
    nodes = []
    for o in problem.orders:
        if satellite in domains[o.customer]:
            _, weight, volume = problem.task(o.id, 'd')
            nodes.append(dict(key=o.id, order=o, weight=weight, volume=volume,
                              window=windows[satellite, o.id, 'd']))
    return dict(satellite=satellite, nodes=nodes,
                lookup={n['key']: i for i, n in enumerate(nodes)},
                release=max(p.cycle_start, batch.start+p.e1_load_time
                    +travel['outward'][problem.depot][satellite]+problem.service(satellite)
                    +p.e2_dispatch_prep_time),
                end_limit=p.cycle_end-p.e2_turnaround_time,
                maximum_trips=min(len(nodes), f.e2_per_sat_count*p.e2_max_trips_per_vehicle))


def evaluate_delivery_column(problem, context, sequence):
    """Replay raw route resources independently of pricing labels/duals."""
    sequence = tuple(sequence)
    if not sequence or len(set(sequence)) != len(sequence):
        return None
    if any(key not in context['lookup'] for key in sequence):
        return None
    nodes = [context['nodes'][context['lookup'][key]] for key in sequence]
    f, p, site = problem.fleet, problem.operating_policy, context['satellite']
    if (math.fsum(n['weight'] for n in nodes) > f.e2_weight_cap+1e-6 or
            math.fsum(n['volume'] for n in nodes) > f.e2_volume_cap+1e-6):
        return None
    current, previous, costs, starts = context['release'], site, [], []
    for n in nodes:
        customer = n['order'].customer
        current = max(current+problem.travel(previous, customer), n['window'][0])
        if current > n['window'][1]:
            return None
        starts.append(current)
        costs.append(problem.distance(previous, customer))
        current += n['order'].service_time
        previous = customer
    returned = current+problem.travel(previous, site)+p.e2_return_unload_time
    if returned > context['end_limit']:
        return None
    costs.append(problem.distance(previous, site))
    return dict(tasks=sequence, cost=math.fsum(costs), starts=starts,
                departure=context['release'], return_complete=returned)


def insertion_columns(problem, context, prices, known=(), *, phase_one=False, limit=12, deadline=None, work_prices=()):
    """Cheap column discovery, without any no-negative-column certificate."""
    def score(c):
        return reduced_route_cost(problem, context['satellite'], c, prices, phase_one=phase_one, work_prices=work_prices)
    singles = [c for n in context['nodes']
               for c in [evaluate_delivery_column(problem, context, (n['key'],))] if c]
    seeds = sorted(list(known)+singles, key=lambda c: (score(c), c['tasks']))[:8]
    candidates = {}
    for current in seeds:
        while len(current['tasks']) < len(context['nodes']):
            best = None
            for n in context['nodes']:
                key = n['key']
                if key in current['tasks']:
                    continue
                for position in range(len(current['tasks'])+1):
                    if deadline is not None and time.perf_counter() >= deadline:
                        return list(candidates.values())
                    c = evaluate_delivery_column(problem, context,
                        current['tasks'][:position]+(key,)+current['tasks'][position:])
                    if c is not None and score(c) < score(current)-1e-8:
                        if best is None or (score(c), c['tasks']) < (score(best), best['tasks']):
                            best = c
            if best is None:
                break
            current = best
            if score(current) < -1e-8:
                candidates[current['tasks']] = current
    return sorted(candidates.values(), key=lambda c: (score(c), c['tasks']))[:limit]


def price_delivery_routes(problem, context, prices, fleet_price=0.0, *, phase_one=False,
                          deadline=None, label_limit=200000, column_limit=16,
                          stop_after_columns=False, work_prices=(), bound_pruning=False):
    """Best-first Pareto labeling with a valid bound even when interrupted.

    For a (visited-task mask, last task), retain nondominated (time, reduced
    partial cost) labels. Future reward is bounded by fractional knapsacks
    for weight and volume separately. The frontier therefore provides a
    lower bound on ALL unprocessed completions. A direct infeasible return
    never prevents extending a prefix: matrices need not be metric.
    """
    begun = time.perf_counter()
    if not isinstance(bound_pruning,bool):
        raise ValueError('Label bound pruning must be boolean')
    if label_limit < 1 or column_limit < 1:
        raise ValueError('Pricing limits must be positive')
    nodes, site = context['nodes'], context['satellite']
    n, f = len(nodes), problem.fleet
    work_prices = validate_work_prices(work_prices)
    rewards = [float(prices.get(node['key'], 0))-math.fsum(v*node_work(problem, node['order'], b)
               for b, v in work_prices) for node in nodes]
    positive = [max(0.0, r) for r in rewards]
    offset = -min(0.0, float(fleet_price))
    resources = [[node[key] for node in nodes] for key in ('weight', 'volume')]
    capacities = [f.e2_weight_cap+1e-6, f.e2_volume_cap+1e-6]
    ratios = [sorted(range(n), key=lambda i: (
        -positive[i]/amounts[i] if amounts[i] else -math.inf, i)) for amounts in resources]
    locations = [node['order'].customer for node in nodes]
    cost = [[(0.0 if phase_one else problem.distance(a, b))+math.fsum(
        v*arc_work(problem, site, nodes[i]['order'], nodes[j]['order'], d) for d, v in work_prices)
        for j, b in enumerate(locations)] for i, a in enumerate(locations)]
    duration = [[problem.travel(a, b) for b in locations] for a in locations]
    outward = [(0.0 if phase_one else problem.distance(site, a))+math.fsum(
        v*arc_work(problem, site, None, nodes[i]['order'], b) for b, v in work_prices) for i, a in enumerate(locations)]
    inward = [(0.0 if phase_one else problem.distance(a, site))+math.fsum(
        v*arc_work(problem, site, nodes[i]['order'], None, b) for b, v in work_prices) for i, a in enumerate(locations)]
    travel_out = [problem.travel(site, a) for a in locations]
    travel_in = [problem.travel(a, site) for a in locations]
    return_floor,return_floor_complete = [0.0]*n,False
    if bound_pruning:
        from models.routing.model_4.exact.bounds.route_cg.label_bound import completion_cost_floor
        return_floor,return_floor_complete = completion_cost_floor(cost,inward,deadline=deadline)
    subsets = {0: (0.0, 0.0, sum(positive))}
    optimistic_cache = {}

    def optimistic(mask):
        if mask in optimistic_cache:
            return optimistic_cache[mask]
        used_w, used_v, reward = subsets[mask]
        for amounts, cap, used, order in zip(resources, capacities, (used_w, used_v), ratios):
            left, relaxation = max(0.0, cap-used), 0.0
            for i in order:
                if mask & (1 << i) or not positive[i]:
                    continue
                amount = amounts[i]
                if amount <= left:
                    relaxation += positive[i]
                    left -= amount
                elif amount:
                    relaxation += positive[i]*left/amount
                    break
            reward = min(reward, relaxation)
        # Round toward a larger reward so the completion bound is conservative.
        optimistic_cache[mask] = reward+1e-10*max(1.0, sum(positive))
        return optimistic_cache[mask]

    heap, frontiers, serial = [], {}, 0
    generated, expanded, candidates = 0, 0, {}
    best, incomplete_current, reason = math.inf, math.inf, None
    bound_closed = False

    def push(mask, last, clock, partial, sequence):
        nonlocal serial, generated
        key = mask, last
        labels = frontiers.setdefault(key, [])
        if any(t <= clock and c <= partial for t, c, _ in labels):
            return
        labels[:] = [(t, c, seq) for t, c, seq in labels if not (clock <= t and partial <= c)]
        labels.append((clock, partial, sequence))
        heapq.heappush(heap, (partial-optimistic(mask)+offset+return_floor[last], serial, mask, last, clock, partial, sequence))
        serial += 1
        generated += 1

    for i, node in enumerate(nodes):
        mask = 1 << i
        w, v = resources[0][i], resources[1][i]
        if w > capacities[0] or v > capacities[1]:
            continue
        start = max(context['release']+travel_out[i], node['window'][0])
        if start <= node['window'][1]:
            subsets[mask] = (w, v, math.fsum(positive[j] for j in range(n) if j != i))
            push(mask, i, start+node['order'].service_time, outward[i]-rewards[i], (i,))
    while heap:
        if deadline is not None and time.perf_counter() >= deadline:
            reason = 'TIME_LIMIT'
            break
        if generated >= label_limit:
            reason = 'LABEL_LIMIT'
            break
        closure_target = max(best,0.0) if bound_pruning and stop_after_columns else best
        if bound_pruning and math.isfinite(best) and heap[0][0]>=closure_target:
            # All current frontier keys lower-bound every unseen completion.
            # A stale dominated label only weakens this minimum, so it cannot
            # cause false closure. The last popped label was fully extended.
            # Discovery should keep possible additional negative columns up
            # to its original batch cap; proving just one best column early
            # can otherwise force more master/pricing iterations.
            bound_closed = True
            break
        bound, _, mask, last, clock, partial, sequence = heapq.heappop(heap)
        if (clock, partial, sequence) not in frontiers[mask, last]:
            continue
        incomplete_current = bound
        expanded += 1
        if clock+travel_in[last]+problem.operating_policy.e2_return_unload_time <= context['end_limit']:
            reduced = partial+inward[last]+offset
            best = min(best, reduced)
            if reduced < -1e-8:
                raw = evaluate_delivery_column(problem, context, [nodes[i]['key'] for i in sequence])
                if raw is None:
                    raise RuntimeError('Pricing label failed independent delivery-trip replay')
                actual = reduced_route_cost(problem, site, raw, prices, fleet_price,
                    phase_one=phase_one, work_prices=work_prices)
                if not math.isclose(actual, reduced, rel_tol=1e-8, abs_tol=1e-6):
                    raise RuntimeError('Pricing reduced cost differs from replay')
                candidates[raw['tasks']] = (actual, raw)
                if stop_after_columns and len(candidates) >= column_limit:
                    reason = 'COLUMNS_FOUND'
                    break
        for j, node in enumerate(nodes):
            if j % 8 == 0 and deadline is not None and time.perf_counter() >= deadline:
                reason = 'TIME_LIMIT'
                break
            if mask & (1 << j):
                continue
            next_mask = mask | (1 << j)
            if next_mask not in subsets:
                # fsum prevents capacity drift from extension order.
                indices = [i for i in range(n) if next_mask & (1 << i)]
                subsets[next_mask] = (math.fsum(resources[0][i] for i in indices),
                    math.fsum(resources[1][i] for i in indices),
                    math.fsum(positive[i] for i in range(n) if not next_mask & (1 << i)))
            w, v, _ = subsets[next_mask]
            if w > capacities[0] or v > capacities[1]:
                continue
            start = max(clock+duration[last][j], node['window'][0])
            if start <= node['window'][1]:
                push(next_mask, j, start+node['order'].service_time,
                     partial+cost[last][j]-rewards[j], sequence+(j,))
        if reason:
            break
        incomplete_current = math.inf
    complete = reason is None
    analytic = -math.fsum(positive)+offset
    raw_lower = best if complete else min(best, incomplete_current, heap[0][0] if heap else math.inf)
    margin = 1e-7*max(1.0, math.fsum(abs(r) for r in rewards), abs(offset),
        math.fsum(abs(prices.get(n['key'], 0)) for n in nodes),
        math.fsum(v*max(1, problem.operating_policy.cycle_end-problem.operating_policy.cycle_start) for b, v in work_prices),
        abs(best) if math.isfinite(best) else 0)
    lower = max(analytic, raw_lower-margin) if math.isfinite(raw_lower) else analytic
    columns = [c for _, c in sorted(candidates.values(), key=lambda row: (row[0], row[1]['tasks']))[:column_limit]]
    return dict(complete=complete, status=reason or ('INFEASIBLE' if math.isinf(best) else 'OPTIMAL'),
                infeasible=complete and math.isinf(best), minimum_reduced_cost=best if math.isfinite(best) else None,
                lower_bound=lower, margin=margin, columns=columns,
                generated_labels=generated, expanded_labels=expanded,
                bound_pruning=bound_pruning, bound_closed=bound_closed,
                bound_closure_target='negative_columns' if stop_after_columns else 'minimum',
                return_floor_complete=return_floor_complete,
                runtime=time.perf_counter()-begun)
