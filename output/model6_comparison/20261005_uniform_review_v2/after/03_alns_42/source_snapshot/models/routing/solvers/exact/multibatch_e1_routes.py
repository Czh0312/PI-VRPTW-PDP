"""Complete E1 route enumeration for small-site M5 lower bounds.

Unlike a restricted route pool, this enumerates every elementary delivery
sequence followed by every elementary pickup sequence. Quantities remain
integer variables. Full E1 routes project at identical raw distance cost,
with earlier optimistic service/clean times. Cross-batch vehicle turnaround
is relaxed; the finite number of vehicles per batch is retained.
"""
from __future__ import annotations

from itertools import permutations
import time

from models.routing.common.cycle.precheck import travel_lower_bounds


def enumerate_e1_routes(problem, batch, check=lambda: None):
    p, f = problem.operating_policy, problem.fleet
    ds = [s for s in problem.satellites if problem.demands[s, batch.id]]
    ps = [s for s in problem.satellites if any(o.satellite == s for o in problem.orders)]
    def sequences(sites):
        for length in range(len(sites)+1):
            yield from permutations(sites, length)
    patterns = []
    for delivery in sequences(ds):
        check()
        if (sum(problem.weights[s, batch.id] for s in delivery) > f.e1_weight_cap+1e-6
                or sum(problem.demands[s, batch.id]*f.container_volume for s in delivery) > f.e1_volume_cap+1e-6):
            continue
        for pickup in sequences(ps):
            check()
            if not delivery and not pickup:
                continue
            # Every visited pickup site has a positive integer quantity.
            if (len(pickup)*f.container_weight > f.e1_weight_cap+1e-6
                    or len(pickup)*f.container_volume > f.e1_volume_cap+1e-6):
                continue
            start = batch.start+p.e1_load_time if delivery else batch.cutoff
            current, previous, cost, completions, valid = start, problem.depot, 0.0, {}, True
            for site in delivery:
                current += problem.travel(previous, site)+problem.service(site)
                cost += problem.distance(previous, site)
                previous = site
                completions[site] = current
                if current > batch.cutoff:
                    valid = False
                    break
            if not valid:
                continue
            if pickup:
                current = max(current, batch.cutoff)
            for site in pickup:
                current += problem.travel(previous, site)+problem.service(site)
                cost += problem.distance(previous, site)
                previous = site
            returned = current+problem.travel(previous, problem.depot)+p.depot_return_unload_time
            cost += problem.distance(previous, problem.depot)
            if returned > batch.end or returned+p.e1_turnaround_time > p.cycle_end:
                continue
            if pickup and returned+p.cleaning_duration > p.cycle_end:
                continue
            patterns.append(dict(batch=batch.id, delivery=delivery, pickup=pickup,
                cost=cost, departure=start, return_complete=returned, supply=completions))
    return patterns


def build_e1_routes(problem, *, deadline=None, inventory=True, verbose=False, travel_bounds=None,
                    distance_bounds=None):
    import gurobipy as gp
    from gurobipy import GRB
    from .multibatch_e1_inventory import add_order_returns
    if (problem.model_number != 5 or problem.free_assignment or not problem.reusable
            or len(problem.operating_policy.batches) <= 1):
        raise ValueError('E1 route certificates require fixed reusable multibatch M5')
    p, f = problem.operating_policy, problem.fleet
    def check():
        if deadline is not None and time.perf_counter() >= deadline:
            raise TimeoutError('Complete E1 enumeration reached its total budget')
    # Finish the complete domain before constructing or reporting a bound.
    patterns = [r for b in p.batches for r in enumerate_e1_routes(problem, b, check)]
    travel = travel_bounds or travel_lower_bounds(problem)
    m = gp.Model('M5_complete_E1_route_inventory_relaxation')
    m.Params.OutputFlag = int(verbose)
    m.Params.Threads = 1
    m.Params.MIPGap = m.Params.MIPGapAbs = 0
    m.Params.DualReductions = 0
    try:
        chosen = m.addVars(len(patterns), vtype=GRB.BINARY,
                           obj={i: r['cost'] for i, r in enumerate(patterns)}, name='route')
        counts = {s: sum(o.count for o in problem.orders if o.satellite == s) for s in problem.satellites}
        quantities = {(i, site): m.addVar(vtype=GRB.INTEGER, ub=counts[site], name=f'quantity_{i}_{site}')
                      for i, r in enumerate(patterns) for site in r['pickup']}
        for i, r in enumerate(patterns):
            check()
            for site in r['pickup']:
                m.addConstr(quantities[i, site] >= chosen[i])
                m.addConstr(quantities[i, site] <= counts[site]*chosen[i])
            q = gp.quicksum(quantities[i, site] for site in r['pickup'])
            m.addConstr(q*f.container_weight <= (f.e1_weight_cap+1e-6)*chosen[i])
            m.addConstr(q*f.container_volume <= (f.e1_volume_cap+1e-6)*chosen[i])
        supplies = {}
        for b in p.batches:
            indices = [i for i, r in enumerate(patterns) if r['batch'] == b.id]
            m.addConstr(gp.quicksum(chosen[i] for i in indices) <= f.e1_count)
            for site in problem.satellites:
                delivering = [i for i in indices if site in patterns[i]['delivery']]
                if problem.demands[site, b.id]:
                    m.addConstr(gp.quicksum(chosen[i] for i in delivering) == 1)
                    supply = m.addVar(vtype=GRB.INTEGER, ub=p.cycle_end, name=f'supply_{site}_{b.id}')
                    m.addConstr(supply == gp.quicksum(patterns[i]['supply'][site]*chosen[i] for i in delivering))
                    supplies[site, b.id] = supply
                collecting = [i for i in indices if site in patterns[i]['pickup']]
                m.addConstr(gp.quicksum(chosen[i] for i in collecting) <= 1)
        returns, allocation, order_times = add_order_returns(problem, m, supplies, travel, check)
        for b in p.batches:
            for site in problem.satellites:
                m.addConstr(gp.quicksum(q for (i, s), q in quantities.items()
                    if s == site and patterns[i]['batch'] == b.id)
                    == gp.quicksum(o.count*allocation[o.id, b.id] for o in problem.orders if o.satellite == site))
        if inventory:
            cumulative = 0
            for b in p.batches:
                cumulative += problem.batch_demands[b.id]
                released = gp.quicksum(q for (i, _), q in quantities.items()
                                      if patterns[i]['return_complete']+p.cleaning_duration <= b.start)
                m.addConstr(p.container_pool_size+released >= cumulative, f'clean_stock_{b.id}')
        for row in (distance_bounds or {}).get('components', []):
            if row['echelon'] == 'E1':
                lower = max(0, row['bound']-1e-7*max(1, row['bound']))
                m.addConstr(gp.quicksum(r['cost']*chosen[i] for i, r in enumerate(patterns)
                                       if r['batch'] == row['batch']) >= lower)
        m.update()
        check()
        return m, dict(patterns=patterns, chosen=chosen, quantities=quantities,
                       returns=returns, allocation=allocation, supplies=supplies, inventory=inventory,
                       enumeration_complete=True, order_times=order_times)
    except Exception:
        m.dispose()
        raise


def set_route_projection(problem, context, initial_result, *, fix=False):
    def put(var, value):
        var.Start = value
        if fix:
            var.LB = var.UB = value
    lookup = {(r['batch'], r['delivery'], r['pickup']): i for i, r in enumerate(context['patterns'])}
    selected, amounts = set(), {}
    for trip in initial_result['e1_trips']:
        delivery = tuple(e['site_id'] for e in trip['events'] if e['kind'] == 'd')
        pickup = tuple(e['site_id'] for e in trip['events'] if e['kind'] == 'p')
        i = lookup[trip['batch_id'], delivery, pickup]
        selected.add(i)
        amounts.update({(i, e['site_id']): e['quantity'] for e in trip['events'] if e['kind'] == 'p'})
    for i, var in context['chosen'].items():
        put(var, int(i in selected))
    for key, var in context['quantities'].items():
        put(var, amounts.get(key, 0))
    returned = {e['order_id']: trip['return_complete'] for trip in initial_result['e2_trips']
                for e in trip['events'] if e['kind'] == 'p'}
    for oid, var in context['returns'].items():
        put(var, returned[oid])
        first = next(b.id for b in problem.operating_policy.batches if returned[oid] <= b.cutoff)
        for b in problem.operating_policy.batches:
            put(context['allocation'][oid, b.id], int(first == b.id))
