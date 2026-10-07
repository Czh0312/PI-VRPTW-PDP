"""Necessary conditions for static fixed-batch instances, independent of solvers.

Travel lower bounds use shortest paths in a relaxed network. Direct arcs are
not safe lower bounds for user-supplied, possibly nonmetric travel matrices.
Passing these checks is not a feasibility certificate.
"""
from __future__ import annotations

import heapq
import math


def _shortest_times(problem, origin, reverse=False):
    distances = {node: math.inf for node in problem.network.nodes}
    distances[origin] = 0
    queue = [(0, origin)]
    while queue:
        elapsed, node = heapq.heappop(queue)
        if elapsed != distances[node]:
            continue
        for other in distances:
            travel = problem.travel(other, node) if reverse else problem.travel(node, other)
            candidate = elapsed + travel
            if candidate < distances[other]:
                distances[other] = candidate
                heapq.heappush(queue, (candidate, other))
    return distances


def travel_lower_bounds(problem):
    """A per-solve context: assignments share travel, but later solves may edit it."""
    return dict(outward={s: _shortest_times(problem, s) for s in (problem.depot,) + problem.satellites},
                inward={s: _shortest_times(problem, s, reverse=True) for s in problem.satellites})


def check_necessary_conditions(problem, travel_bounds=None):
    p, f = problem.operating_policy, problem.fleet
    from .availability import fixed_order_availability_errors
    errors, order_bounds = fixed_order_availability_errors(problem), []

    def fail(code, **details):
        errors.append(dict(code=code, **details))

    travel_bounds = travel_bounds or travel_lower_bounds(problem)
    outward, inward = travel_bounds['outward'], travel_bounds['inward']
    for batch in p.batches:
        total_weight = total_volume = 0
        for satellite in problem.satellites:
            count = problem.demands[satellite, batch.id]
            weight = problem.weights[satellite, batch.id]
            volume = count * f.container_volume
            total_weight += weight
            total_volume += volume
            if weight > f.e1_weight_cap + 1e-6 or volume > f.e1_volume_cap + 1e-6:
                fail('UNSPLIT_E1_DELIVERY_CAPACITY', satellite_id=satellite, batch_id=batch.id)
            earliest = batch.start + p.e1_load_time + outward[problem.depot][satellite] + problem.service(satellite)
            if count and earliest > batch.cutoff:
                fail('E1_DELIVERY_DEADLINE', satellite_id=satellite, batch_id=batch.id,
                     earliest_complete=earliest, deadline=batch.cutoff)
        if (total_weight > f.e1_count * (f.e1_weight_cap + 1e-6)
                or total_volume > f.e1_count * (f.e1_volume_cap + 1e-6)):
            fail('TOTAL_E1_DELIVERY_CAPACITY', batch_id=batch.id)

    for order in problem.orders:
        batch = problem.batch_map[order.batch]
        site = order.satellite
        for kind in problem.task_kinds:
            _, weight, volume = problem.task(order.id, kind)
            if weight > f.e2_weight_cap + 1e-6 or volume > f.e2_volume_cap + 1e-6:
                fail('UNSPLIT_E2_ORDER_CAPACITY', order_id=order.id, kind=kind)
        supplied = (batch.start + p.e1_load_time + outward[problem.depot][site]
                    + problem.service(site) + p.e2_dispatch_prep_time)
        delivered = max(order.delivery_window[0], supplied + outward[site][order.customer])
        picked = max(order.pickup_window[0], delivered + order.service_time + order.min_use_time)
        unloaded = picked + order.service_time + inward[site][order.customer] + p.e2_return_unload_time
        if len(p.batches) == 1 and delivered+order.service_time > batch.cutoff:
            fail('DELIVERY_PHASE_UNREACHABLE', order_id=order.id)
        if delivered > order.delivery_window[1]:
            fail('DELIVERY_WINDOW_UNREACHABLE', order_id=order.id, earliest=delivered,
                 deadline=order.delivery_window[1])
        if not problem.reusable:
            returned = delivered+order.service_time+inward[site][order.customer]+p.e2_return_unload_time
            if returned+p.e2_turnaround_time > p.cycle_end:
                fail('E2_TERMINAL_UNREACHABLE', order_id=order.id)
            continue
        if picked > order.pickup_window[1]:
            fail('PICKUP_WINDOW_UNREACHABLE', order_id=order.id, earliest=picked,
                 deadline=order.pickup_window[1])
        if unloaded > p.batches[-1].cutoff:
            fail('FINAL_CUTOFF_UNREACHABLE', order_id=order.id, earliest=unloaded,
                 deadline=p.batches[-1].cutoff)
        if unloaded + p.e2_turnaround_time > p.cycle_end:
            fail('E2_TERMINAL_UNREACHABLE', order_id=order.id)
        # Optimistically place E1 at this satellite at the cutoff. Ignore
        # competing orders/vehicles; this can only make reuse earlier.
        releases = []
        for wave in p.batches:
            if unloaded > wave.cutoff:
                continue
            depot_return = (wave.cutoff + problem.service(site) + outward[site][problem.depot]
                            + p.depot_return_unload_time)
            if (depot_return <= wave.end and depot_return + p.e1_turnaround_time <= p.cycle_end
                    and depot_return + p.cleaning_duration <= p.cycle_end):
                releases.append((depot_return + p.cleaning_duration, wave.id))
        clean, return_batch = min(releases) if releases else (None, None)
        if clean is None:
            fail('NO_POSSIBLE_CLEAN_RETURN', order_id=order.id)
        order_bounds.append(dict(order_id=order.id, delivery_batch_id=order.batch,
                                 container_count=order.count, earliest_delivery=delivered,
                                 earliest_pickup=picked, earliest_unload=unloaded,
                                 earliest_clean=clean, optimistic_return_batch=return_batch))

    stock_bounds, cumulative = [], 0
    lower_bound = max(problem.batch_demands.values(), default=0) if problem.reusable else 0
    for index, batch in enumerate(p.batches if problem.reusable else ()):
        cumulative += problem.batch_demands[batch.id]
        reusable = sum(row['container_count'] for row in order_bounds
                       if problem.batch_index[row['delivery_batch_id']] < index
                       and row['earliest_clean'] is not None and row['earliest_clean'] <= batch.start)
        required = max(problem.batch_demands[batch.id], cumulative - reusable)
        lower_bound = max(lower_bound, required)
        stock_bounds.append(dict(batch_id=batch.id, time=batch.start, cumulative_issues=cumulative,
                                 optimistic_clean_returns=reusable, pool_lower_bound=required))
    if p.container_pool_size < lower_bound:
        fail('POOL_BELOW_NECESSARY_BOUND', pool=p.container_pool_size, lower_bound=lower_bound)
    return dict(passed=not errors, proven_infeasible=bool(errors),
                proof_scope='static_necessary_conditions_v1', time_unit='ticks',
                time_tick_seconds=p.time_tick_seconds, time_tick_subdivision=p.time_tick_subdivision,
                pool_lower_bound=lower_bound,
                stock_bounds=stock_bounds, order_bounds=order_bounds, errors=errors,
                limitation='Passing necessary conditions does not prove feasibility or a minimum pool.')


def rejected_result(problem, method, check, runtime):
    from .state import empty_result
    result = empty_result(problem, method)
    result['precheck'] = check
    result['info'].update(status='INFEASIBLE', proven_infeasible=True,
                          proof_scope=check['proof_scope'], runtime=runtime,
                          infeasibility_source='necessary_conditions')
    return result
