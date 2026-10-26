"""Safe K exclusions from input data, independent of any optimizer outcome.

Each productive batch consumes at least one whole order. In an equal calendar,
the first batch must be long enough to return at least one issued container to
the depot. Earliest return calculations relax vehicle competition, loading
capacity, cutoff placement and other orders. Shortest paths, not direct arcs,
keep these lower bounds valid for nonmetric travel matrices too.
"""
from models.common.cycle.precheck import travel_lower_bounds


def productive_count_certificate(problem, minimum_phase_minutes=1):
    p = problem.operating_policy
    minimum = p.ticks(minimum_phase_minutes)
    if minimum <= 0 or not problem.orders or not problem.reusable:
        raise ValueError('Positive phases and reusable positive-demand orders required')
    travel = travel_lower_bounds(problem)
    rows = []
    for order in problem.orders:
        site = order.satellite
        delivered = max(order.delivery_window[0],
            p.cycle_start+p.e1_load_time+travel['outward'][problem.depot][site]
            +problem.service(site)+p.e2_dispatch_prep_time+travel['outward'][site][order.customer])
        picked = max(order.pickup_window[0], delivered+order.service_time+order.min_use_time)
        returned = (picked+order.service_time+travel['inward'][site][order.customer]
            +p.e2_return_unload_time+problem.service(site)
            +travel['outward'][site][problem.depot]+p.depot_return_unload_time)
        rows.append(dict(order_id=order.id, earliest_delivery_ticks=delivered,
                         earliest_pickup_ticks=picked, depot_return_lower_bound_ticks=returned))
    duration = p.cycle_end-p.cycle_start
    elapsed = min(r['depot_return_lower_bound_ticks'] for r in rows)-p.cycle_start
    phase_bound = duration//(2*minimum)
    order_bound = len(problem.orders)
    equal_bound = duration//elapsed if elapsed > 0 else phase_bound
    flexible_bound = 1+(duration-max(elapsed,2*minimum))//(2*minimum)
    uppers = {str(m): min(phase_bound,order_bound,equal_bound if m<8 else flexible_bound)
              for m in (6,7,8)}
    if min(uppers.values()) < 1:
        raise ValueError('No productive calendar can fit; investigate inputs rather than dropping the case')
    return dict(schema_version=1, proof='productive_order_count_and_first_return_v1',
        original_positive_integer_domain=[1,phase_bound], order_count=order_bound,
        minimum_phase_ticks=minimum, cycle_duration_ticks=duration,
        time_tick_seconds=p.time_tick_seconds, time_tick_subdivision=p.time_tick_subdivision,
        first_return_elapsed_lower_bound_ticks=elapsed,
        equal_calendar_upper_bound=equal_bound,
        upper_bound_by_model=uppers,
        candidates_by_model={m:list(range(1,n+1)) for m,n in uppers.items()},
        assumptions=['Every counted batch has positive delivery and depot-return quantities.',
                     'Each positive-demand order is assigned to exactly one batch; orders are not split.',
                     'The cycle starts with clean stock and no outstanding previous-cycle orders.',
                     'M6/M7 have equal batch lengths; M8 may use unequal lengths.',
                     'Refined clocks preserve already rounded physical durations.'],
        proof_steps=['K cannot exceed the number of orders because every batch delivers at least one order.',
                     'The first batch must return a current-cycle order before its end.',
                     'Each listed return time is optimistic: competition, capacity and cutoff waiting are ignored.',
                     'For M6/M7, H/K is at least the earliest optimistic return elapsed time.',
                     'For M8, the first batch consumes at least that time; other batches need two minimum phases.'],
        scope='Excluded counts are infeasible; retained counts are not certified feasible or optimal.',
        solver_calls=0, order_return_bounds=rows)
