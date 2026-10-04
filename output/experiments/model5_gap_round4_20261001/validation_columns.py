import math

TRAVEL = {}

def close(a, b):
    assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6), (a, b)

def trip_cost(problem, site, tasks):
    places = [site]+[problem.order_map[key[0]].customer for key in tasks]+[site]
    return math.fsum(problem.distance(a, b) for a, b in zip(places, places[1:]))

def replay_column(problem, site, column):
    """Replay serialized times and loads; do not call the pricing evaluator."""
    p, f = problem.operating_policy, problem.fleet
    tasks = [tuple(key) for key in column['tasks']]
    assert tasks and len(set(tasks)) == len(tasks) == len(column['starts'])
    assert all(problem.order_map[oid].satellite == site and kind in ('d', 'p') for oid, kind in tasks)
    batches = {problem.order_map[oid].batch for oid, kind in tasks if kind == 'd'}
    assert len(batches) <= 1
    elapsed, previous, delivered = column['departure'], site, {}
    assert isinstance(elapsed, int) and p.cycle_start <= elapsed <= p.cycle_end
    # Necessary supply time: shortest travel can only relax actual E1 supply.
    if batches:
        from models.routing.common.cycle.precheck import travel_lower_bounds
        if problem not in TRAVEL:
            TRAVEL[problem] = travel_lower_bounds(problem)
        lower = TRAVEL[problem]['outward'][problem.depot][site]
        assert elapsed >= max(problem.batch_map[b].start+p.e1_load_time+lower+problem.service(site)
                              +p.e2_dispatch_prep_time for b in batches)
    load = [math.fsum(problem.task(oid, kind)[i] for oid, kind in tasks if kind == 'd') for i in (1, 2)]
    caps = [f.e2_weight_cap, f.e2_volume_cap]
    assert all(-1e-6 <= v <= c+1e-6 for v, c in zip(load, caps))
    for (oid, kind), started in zip(tasks, column['starts']):
        order, weight, volume = problem.task(oid, kind)
        assert isinstance(started, int) and started >= elapsed+problem.travel(previous, order.customer)
        low, high = order.delivery_window if kind == 'd' else order.pickup_window
        assert low <= started <= high
        if kind == 'p' and (oid, 'd') in tasks:
            assert oid in delivered and started >= delivered[oid]+order.service_time+order.min_use_time
        if kind == 'd':
            delivered[oid] = started
        for i, amount in enumerate((weight, volume)):
            load[i] += amount*(1 if kind == 'p' else -1)
        assert all(-1e-6 <= v <= c+1e-6 for v, c in zip(load, caps))
        elapsed, previous = started+order.service_time, order.customer
    returned = column['return_complete']
    assert isinstance(returned, int) and returned >= elapsed+problem.travel(previous, site)+p.e2_return_unload_time
    assert returned+p.e2_turnaround_time <= p.cycle_end
    if any(kind == 'p' for _, kind in tasks):
        assert returned <= p.batches[-1].cutoff
    close(column['cost'], trip_cost(problem, site, tasks))
