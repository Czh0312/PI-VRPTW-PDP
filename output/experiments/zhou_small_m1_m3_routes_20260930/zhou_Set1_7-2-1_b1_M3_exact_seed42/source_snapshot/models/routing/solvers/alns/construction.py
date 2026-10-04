"""Alternative starting schedule for orders spread across a long static cycle."""
from .evaluator import initial_state


def reassigned_state(problem, result, deadline):
    """Keep unaffected routes when customer ownership changes, then repair.

    A projected state is only a proposal: new inbound quantities can make old
    routes infeasible, so the fixed solver must decode and audit it afresh.
    """
    from .evaluator import _e1_trip, route_distance
    from .neighborhoods import state_from_result, repair_orders
    state = state_from_result(problem, result)
    moved = {oid for route in state['e2'] for oid, _ in route['tasks']
             if problem.order_map[oid].satellite != route['satellite']}
    for batch in problem.operating_policy.batches:
        rows = state['deliveries'][batch.id]
        for row in rows:
            row[:] = [s for s in row if problem.demands[s, batch.id]]
        existing = {s for row in rows for s in row}
        for site in problem.satellites:
            if not problem.demands[site, batch.id] or site in existing:
                continue
            choices = []
            for vehicle, row in enumerate(rows):
                for pos in range(len(row)+1):
                    candidate = row[:pos]+[site]+row[pos:]
                    trip, issues = _e1_trip(problem, batch, vehicle, candidate, [], {},
                                            problem.operating_policy.cycle_start)
                    choices.append((len(issues), route_distance(problem, trip, 'e1'), vehicle, pos))
            _, _, vehicle, pos = min(choices)
            rows[vehicle].insert(pos, site)
    return repair_orders(problem, state, [problem.order_map[oid] for oid in sorted(moved)], deadline)


def chronological_state(problem, rng):
    state = initial_state(problem, rng)
    fleet = problem.fleet
    # Dedicated initial E1 routes avoid visiting a distant satellite before
    # an early delivery. ALNS may subsequently combine or reassign them.
    if fleet.e1_count >= len(problem.satellites):
        for batch in problem.operating_policy.batches:
            state['deliveries'][batch.id] = [[] for _ in range(fleet.e1_count)]
            for vehicle, site in enumerate(problem.satellites):
                if problem.demands[site, batch.id]:
                    state['deliveries'][batch.id][vehicle] = [site]
                state['pickup_vehicle'][batch.id][site] = vehicle
    state['e2'] = []
    for site in problem.satellites:
        orders = sorted((o for o in problem.orders if o.satellite == site),
                        key=lambda o: (o.pickup_window[0] if problem.reusable else o.delivery_window[0], o.delivery_window[0], o.id))
        for index, order in enumerate(orders):
            # Dedicated paired trips preserve early pickups even when B=1
            # includes late deliveries. The decoder still enforces physical
            # vehicle occupancy and the configured trip-slot limit.
            state['e2'].append(dict(satellite=site, vehicle=index % fleet.e2_per_sat_count,
                                    tasks=[(order.id, k) for k in problem.task_kinds]))
    return state
