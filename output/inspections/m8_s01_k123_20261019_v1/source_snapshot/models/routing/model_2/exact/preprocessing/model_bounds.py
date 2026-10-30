"""Proven B=1 bounds and sparse route geometry, independent of Gurobi.

All path bounds use the relaxed shortest-time network, including for directed
nonmetric inputs. Physical capacities, customer windows, and trip limits stay
unchanged. These are bounds of the declared MILP, not a search algorithm.
"""
import math
import time


# Engineering crossover, not a restriction on routes or assignments. Tiny
# models often solve faster without the extra route-identity linking rows.
COMPRESS_ROUTE_MIN_INTERNAL_ARCS = 10000


def trip_slot_limits(problem, task_count):
    p, f = problem.operating_policy, problem.fleet
    vehicles = min(f.e2_per_sat_count, task_count)
    if len(p.batches) != 1:
        return [min(p.e2_max_trips_per_vehicle, task_count)]*vehicles
    # Sort identical vehicles by nonincreasing nonempty trip count. The first
    # v+1 vehicles then use >= (v+1)*trips[v] distinct tasks in total.
    return [min(p.e2_max_trips_per_vehicle, task_count//(v+1)) for v in range(vehicles)]


def route_arcs(problem, nodes, base, layer, batch=None, check_budget=lambda: None):
    p = problem.operating_policy
    end = min(p.cycle_end-p.e1_turnaround_time, batch.end) if layer == 'e1' else p.cycle_end-p.e2_turnaround_time
    unload = p.depot_return_unload_time if layer == 'e1' else p.e2_return_unload_time
    arcs = [(-1, j) for j, n in enumerate(nodes)
            if p.cycle_start+problem.travel(base, n['site']) <= n['window'][1]]
    arcs += [(i, -2) for i, n in enumerate(nodes)
             if n['window'][0]+n['service']+problem.travel(n['site'], base)+unload <= end]
    for i, left in enumerate(nodes):
        check_budget()
        for j, right in enumerate(nodes):
            if i == j or (layer == 'e1' and left['kind'] == 'p' and right['kind'] == 'd'):
                continue
            if (layer == 'e2' and left['kind'] == 'p' and right['kind'] == 'd'
                    and left['order'].id == right['order'].id):
                continue
            if left['window'][0]+left['service']+problem.travel(left['site'], right['site']) <= right['window'][1]:
                arcs.append((i, j))
    return arcs




def single_batch_bounds(problem, travel, domains=None):
    """Return supply/event windows and attainable post-service load bounds."""
    p, f = problem.operating_policy, problem.fleet
    if len(p.batches) != 1:
        raise ValueError('Single-batch bounds require B=1')
    batch = p.batches[0]
    supply, dues, visits, quantities = {}, {}, {}, {}
    for site in problem.satellites:
        orders = [o for o in problem.orders if
                  (site in domains[o.customer] if domains is not None else o.satellite == site)]
        if not orders:
            continue
        earliest = batch.start+p.e1_load_time+travel['outward'][problem.depot][site]+problem.service(site)
        latest = {o.id: min(o.delivery_window[1], batch.cutoff-o.service_time)
                  -p.e2_dispatch_prep_time-travel['outward'][site][o.customer] for o in orders}
        upper = max(latest.values()) if domains is not None else min(latest.values())
        supply[site] = (earliest, upper)
        dues[site] = latest
        visits[site, 'd'] = (earliest-problem.service(site), upper-problem.service(site))
        returned = min(batch.end, p.cycle_end-p.cleaning_duration, p.cycle_end-p.e1_turnaround_time)
        visits[site, 'p'] = (batch.cutoff, returned-problem.service(site)
                            -travel['outward'][site][problem.depot]-p.depot_return_unload_time)
        q = sum(o.count for o in orders)
        for cap, unit in ((f.e1_volume_cap, f.container_volume), (f.e1_weight_cap, f.container_weight)):
            if unit:
                q = min(q, math.floor(cap/unit+1e-9))
        quantities[site] = q
    # A container is either still loaded for delivery or already collected,
    # never both. Volume therefore also bounds weight by the heaviest box.
    max_weight = sum(problem.task(o.id, 'd')[1] for o in problem.orders)
    if f.container_volume and problem.orders:
        ratio = max(problem.task(o.id, 'd')[1]/(o.count*f.container_volume) for o in problem.orders)
        max_weight = min(max_weight, ratio*f.e2_volume_cap)
    weight = min(f.e2_weight_cap, max_weight+1e-9*max(1, max_weight))
    volume = min(f.e2_volume_cap, problem.total*f.container_volume)
    loads = {}
    for o in problem.orders:
        for kind in problem.task_kinds:
            _, w, v = problem.task(o.id, kind)
            loads[o.id, kind] = ((0, weight-w, 0, volume-v) if kind == 'd' else (w, weight, v, volume))
    # T mandatory tasks and <=T used route start/end pairs form a DAG with
    # <=3T vertices. Any feasible causal order has ranks in [0, 3T-1].
    return dict(supply=supply, dues=dues, visits=visits, quantities=quantities,
                loads=loads, causal_limit=max(1, 3*len(problem.orders)*len(problem.task_kinds)-1))
