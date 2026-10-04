"""Alternative starting schedule for orders spread across a long static cycle."""
from .evaluator import initial_state


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
                        key=lambda o: (o.pickup_window[0], o.delivery_window[0], o.id))
        for index, order in enumerate(orders):
            # Dedicated paired trips preserve early pickups even when B=1
            # includes late deliveries. The decoder still enforces physical
            # vehicle occupancy and the configured trip-slot limit.
            state['e2'].append(dict(satellite=site, vehicle=index % fleet.e2_per_sat_count,
                                    tasks=[(order.id, 'd'), (order.id, 'p')]))
    return state
