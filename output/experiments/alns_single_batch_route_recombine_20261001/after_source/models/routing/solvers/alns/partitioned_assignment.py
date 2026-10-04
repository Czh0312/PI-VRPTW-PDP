"""Delivery-only partition exploration retained for M2; route repair is shared."""
import math
import random
import time
from collections import OrderedDict

from models.routing.common.cycle.assignment import assigned_problem, input_assignment, lift_result
from models.routing.common.cycle.state import empty_result
from models.routing.common.cycle.precheck import travel_lower_bounds
from .construction import reassigned_state
from .single_batch_cache import SingleBatchCache


def _tail_route_cost(problem, site, orders):
    """Directed distance of a capacity-feasible, optimistically timed route.

    This only screens a hypothetical sequence. Inbound supply, shared vehicles
    and the actual projected sequence still need full decoding and auditing.
    """
    if not orders:
        return 0.0
    f, p = problem.fleet, problem.operating_policy
    if sum(o.goods_weight+o.count*f.container_weight for o in orders) > f.e2_weight_cap:
        return None
    if sum(o.count*f.container_volume for o in orders) > f.e2_volume_cap:
        return None
    current, previous, distance = p.cycle_start+p.e2_dispatch_prep_time, site, 0.0
    for order in orders:
        current = max(current+problem.travel(previous, order.customer), order.delivery_window[0])
        if current > min(order.delivery_window[1], problem.batch_map[order.batch].cutoff-order.service_time):
            return None
        current += order.service_time
        distance += problem.distance(previous, order.customer)
        previous = order.customer
    if current+problem.travel(previous, site)+p.e2_return_unload_time+p.e2_turnaround_time > p.cycle_end:
        return None
    return distance+problem.distance(previous, site)


def _route_tail_assignment(problem, current, incumbent, rng):
    """Recombine complete customer suffixes across two incumbent owner sites.

    Empty suffixes also allow capacity-feasible tail or whole-route transfers.
    No random numbers are consumed when the original proposal must be used.
    """
    customer_orders = {}
    for order in problem.orders:
        customer_orders.setdefault(order.customer, set()).add(order.id)
    routes = []
    for trip in incumbent.get('e2_trips', ()):
        events, site = trip.get('events', ()), trip['satellite_id']
        if not events or any(e['kind'] != 'd' for e in events):
            continue
        orders = tuple(problem.order_map[e['order_id']] for e in events)
        if any(current[o.customer] != site for o in orders):
            continue
        distance = _tail_route_cost(problem, site, orders)
        if distance is None:
            continue
        cuts = []
        for cut in range(len(orders)+1):
            suffix = orders[cut:]
            members = {o.customer for o in suffix}
            ids = {o.id for o in suffix}
            if all(customer_orders[c] <= ids for c in members):
                cuts.append((cut, members))
        routes.append((site, orders, distance, cuts))
    options = []
    for index, (site_a, a, distance_a, cuts_a) in enumerate(routes):
        for site_b, b, distance_b, cuts_b in routes[index+1:]:
            if site_a == site_b:
                continue
            for cut_a, members_a in cuts_a:
                for cut_b, members_b in cuts_b:
                    if len(members_a)+len(members_b) < 2:
                        continue
                    combined_a, combined_b = a[:cut_a]+b[cut_b:], b[:cut_b]+a[cut_a:]
                    cost_a = _tail_route_cost(problem, site_a, combined_a)
                    if cost_a is None:
                        continue
                    cost_b = _tail_route_cost(problem, site_b, combined_b)
                    if cost_b is None:
                        continue
                    delta = cost_a+cost_b-distance_a-distance_b
                    if delta < -1e-9:
                        options.append((delta, site_a, site_b, members_a, members_b))
    if not options:
        return None
    options.sort(key=lambda row: row[0])
    delta, site_a, site_b, members_a, members_b = rng.choice(options[:3])
    candidate = dict(current)
    candidate.update((c, site_b) for c in members_a)
    candidate.update((c, site_a) for c in members_b)
    return candidate, delta


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42, *, route_recombination=True):
    if not math.isfinite(max_time) or max_time <= 0 or max_iterations < 0:
        raise ValueError('Invalid assignment-search budget')
    from .single_batch import solve_alns as solve_fixed
    start, rng = time.perf_counter(), random.Random(seed)
    travel_bounds = travel_lower_bounds(problem)
    current = input_assignment(problem)
    customers = sorted(current)
    nearest = {c: min(problem.satellites, key=lambda s: problem.distance(s, c)) for c in customers}
    best, best_cost, attempts, iterations = None, math.inf, 0, 0
    incumbents, reused, projected = OrderedDict(), 0, 0
    tail_checks, tail_attempts, tail_improvements, tail_deltas = 0, 0, 0, []
    first_feasible = last_improvement = None
    history, initial_objective = [], None
    operator_totals = {k: {} for k in ('operator_attempts', 'operator_improvements', 'operator_noops')}
    descent_totals = {k: 0 for k in ('descent_seconds', 'descent_candidates', 'descent_filtered',
                                    'descent_site_cache_hits', 'descent_site_enumerations',
                                    'descent_deferred', 'descent_recovered')}
    while time.perf_counter()-start < max_time:
        tail_proposal = False
        if attempts and (iterations >= max_iterations or not customers):
            break
        if attempts == 0:
            candidate = dict(current)
        elif attempts == 1:
            candidate = nearest
        elif attempts % 3 == 0 and best is not None:
            # Periodically deepen the incumbent partition instead of spending
            # the whole budget on many shallow, cold assignment attempts.
            candidate = dict(current)
        else:
            recombined = None
            if route_recombination and best is not None and attempts % 4 == 2:
                tail_checks += 1
                recombined = _route_tail_assignment(problem, current, best, rng)
            if recombined is not None:
                candidate, delta = recombined
                tail_proposal = True
                tail_attempts += 1
                tail_deltas.append(delta)
            else:
                candidate = dict(current)
                if len(customers) > 1 and rng.random() < .3:
                    a, b = rng.sample(customers, 2)
                    candidate[a], candidate[b] = candidate[b], candidate[a]
                else:
                    customer = rng.choice(customers)
                    sites = [s for s in problem.satellites if s != candidate[customer]]
                    if sites:
                        candidate[customer] = rng.choice(sites)
        fixed = assigned_problem(problem, candidate)
        context = SingleBatchCache(fixed)
        remaining = max_time-(time.perf_counter()-start)
        if remaining <= 0:
            break
        share = .4 if attempts == 0 and len(customers) > 8 else .1
        iteration_budget = min(max_iterations-iterations, max(1, int(max_iterations*share))) if max_iterations else 0
        key = tuple(sorted(candidate.items()))
        warm = incumbents.get(key)
        seed_state = None
        if warm is None and best is not None:
            seed_state = reassigned_state(fixed, best, min(start+max_time, time.perf_counter()+max_time/20),
                                           scorer=context.route_scorer)
            projected += int(seed_state is not None)
        remaining = max_time-(time.perf_counter()-start)
        if remaining <= 0:
            break
        reused += int(warm is not None)
        subproblem_start = time.perf_counter()-start
        solved = solve_fixed(fixed, max_time=min(remaining, max(.01, max_time*share)),
                             max_iterations=iteration_budget, seed=seed+attempts, initial_result=warm,
                             seed_state=seed_state, _travel_bounds=travel_bounds, _context=context)
        attempts += 1
        for field, counts in operator_totals.items():
            for operator, count in solved['info'].get(field, {}).items():
                counts[operator] = counts.get(operator, 0)+count
        for field in descent_totals:
            descent_totals[field] += solved['info'].get(field, 0)
        used = solved['info'].get('iterations', 0)
        # A precheck-rejected partition still consumes a proposal budget.
        iterations += max(1, used) if max_iterations else 0
        if solved.get('audit', {}).get('valid'):
            if first_feasible is None:
                first_feasible = subproblem_start+(solved['info'].get('time_to_first_feasible') or 0)
            if attempts == 1:
                initial_objective = solved['info'].get('initial_objective')
            incumbents[key] = solved
            incumbents.move_to_end(key)
            if len(incumbents) > 32:
                incumbents.popitem(last=False)
            lifted = lift_result(problem, solved, candidate)
            if lifted['audit']['valid'] and solved['info']['objective'] < best_cost:
                tail_improvements += int(tail_proposal)
                best, best_cost, current = lifted, solved['info']['objective'], dict(candidate)
                last_improvement = subproblem_start+(solved['info'].get('time_to_best') or 0)
                history.append(dict(time=last_improvement, iteration=iterations, objective=best_cost))
    result = best if best is not None else empty_result(problem, 'alns')
    result['info'].update(status='FEASIBLE' if best else 'HEURISTIC_NO_SOLUTION', proven_optimal=False,
                          proven_infeasible=False, best_bound=None, mip_gap=None, proof_scope=None,
                          algorithm='customer_assignment_search_with_cycle_alns',
                          runtime=time.perf_counter()-start, seed=seed, iterations=iterations,
                          assignment_attempts=attempts, assignment_warm_starts=reused,
                          assignment_route_projections=projected,
                          assignment_tail_checks=tail_checks, assignment_tail_attempts=tail_attempts,
                          assignment_tail_improvements=tail_improvements, assignment_tail_deltas=tail_deltas,
                          time_to_first_feasible=first_feasible, time_to_best=last_improvement,
                          initial_objective=initial_objective, incumbent_history=history,
                          operator_statistics_scope='all_partition_subsolves',
                          history_scope='best_at_completed_partition_subsolve',
                          assignment_budget_policy='initial_40pct_then_project_and_refine')
    result['info'].update(operator_totals, **descent_totals)
    return result
