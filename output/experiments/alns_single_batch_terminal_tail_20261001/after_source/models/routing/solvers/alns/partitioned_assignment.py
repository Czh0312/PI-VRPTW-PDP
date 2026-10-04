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
from .neighborhoods import clone_state, state_from_result
from models.routing.common.cycle.audit import audit_solution


def _tail_route_cost(problem, site, orders):
    """Rank a hypothetical E2 sequence under optimistic local readiness."""
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


def _tail_options(problem, current, state, deadline):
    """Enumerate only suffixes containing every order of each moved customer."""
    customer_orders = {}
    for order in problem.orders:
        customer_orders.setdefault(order.customer,set()).add(order.id)
    routes = []
    for index, route in enumerate(state['e2']):
        if not route['tasks'] or any(kind != 'd' for _,kind in route['tasks']):
            continue
        site = route['satellite']
        orders = tuple(problem.order_map[oid] for oid,_ in route['tasks'])
        if any(current[o.customer] != site for o in orders):
            continue
        distance = _tail_route_cost(problem,site,orders)
        if distance is None:
            continue
        cuts = []
        for cut in range(len(orders)+1):
            suffix = orders[cut:];members = {o.customer for o in suffix};ids = {o.id for o in suffix}
            if all(customer_orders[c] <= ids for c in members):
                cuts.append((cut,members))
        routes.append((index,site,orders,distance,cuts))
    options = []
    for index, (ia,sa,a,da,cuts_a) in enumerate(routes):
        for ib,sb,b,db,cuts_b in routes[index+1:]:
            if time.perf_counter() >= deadline:
                return None
            if sa == sb:
                continue
            for ca,ma in cuts_a:
                for cb,mb in cuts_b:
                    if len(ma)+len(mb) < 2:
                        continue
                    cost_a = _tail_route_cost(problem,sa,a[:ca]+b[cb:])
                    if cost_a is None:
                        continue
                    cost_b = _tail_route_cost(problem,sb,b[:cb]+a[ca:])
                    if cost_b is None:
                        continue
                    delta = cost_a+cost_b-da-db
                    if delta < -1e-9:
                        options.append((delta,ia,ib,ca,cb,ma,mb))
    return sorted(options,key=lambda row:row[0])


def _tail_state(problem, current, original, option):
    """Preserve exchanged task sequences; full decoding checks new supply."""
    _,ia,ib,ca,cb,ma,mb = option
    state = clone_state(original)
    a,b = state['e2'][ia],state['e2'][ib]
    assignment = dict(current)
    assignment.update((c,b['satellite']) for c in ma)
    assignment.update((c,a['satellite']) for c in mb)
    a['tasks'],b['tasks'] = a['tasks'][:ca]+b['tasks'][cb:],b['tasks'][:cb]+a['tasks'][ca:]
    state['e2'] = [r for r in state['e2'] if r['tasks']]
    fixed = assigned_problem(problem,assignment)
    # Both destinations already have a route; no new site needs inserting.
    for batch,rows in state['deliveries'].items():
        for row in rows:
            row[:] = [s for s in row if fixed.demands[s,batch]]
    return assignment,fixed,state


def _audited_tail_seed(problem, current, incumbent, deadline, statistics):
    """Compare preserved and repaired routes, retaining audited improvement.

    Geometry only ranks up to three options. A complete decode and independent
    audit must succeed before a result can replace an audited incumbent.
    A failed or expired search leaves the parent's random proposal stream intact.
    """
    statistics.update(geometric_options=0,decoded_candidates=0,audited_candidates=0,
                      improving_candidates=0,timed_out=0)
    if time.perf_counter() >= deadline:
        statistics['timed_out'] = 1
        return None
    original = state_from_result(problem,incumbent)
    options = _tail_options(problem,current,original,deadline)
    if options is None:
        statistics['timed_out'] = 1
        return None
    statistics['geometric_options'] = len(options)
    best, best_cost = None,incumbent['info']['objective']
    for option in options[:3]:
        if time.perf_counter() >= deadline:
            statistics['timed_out'] = 1
            break
        assignment,fixed,state = _tail_state(problem,current,original,option)
        context = SingleBatchCache(fixed)
        for mode in ('direct','repaired'):
            if time.perf_counter() >= deadline:
                statistics['timed_out'] = 1
                break
            if mode == 'repaired':
                state = reassigned_state(fixed,incumbent,deadline,scorer=context.route_scorer)
                if state is None:
                    continue
            score,result,issues = context(state)
            statistics['decoded_candidates'] += 1
            if issues or score >= best_cost-1e-9:
                continue
            audit = audit_solution(fixed,result)
            statistics['audited_candidates'] += 1
            if not audit['valid']:
                continue
            statistics['improving_candidates'] += 1
            best_cost = score
            result['audit'] = audit
            best = dict(assignment=assignment,fixed=fixed,context=context,result=result,
                        geometry_delta=option[0],gain=incumbent['info']['objective']-score,mode=mode)
    return best


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42, *, terminal_route_descent=True):
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
    terminal_enabled = terminal_route_descent and max_iterations > 0
    search_time = max_time*.95 if terminal_enabled else max_time
    terminal_checks = terminal_steps = direct_steps = repaired_steps = 0
    terminal_totals = {key: 0 for key in ('geometric_options','decoded_candidates','audited_candidates',
                                       'improving_candidates','timed_out')}
    terminal_gains, terminal_seconds = [], 0.0
    first_feasible = last_improvement = None
    history, initial_objective = [], None
    operator_totals = {k: {} for k in ('operator_attempts', 'operator_improvements', 'operator_noops')}
    descent_totals = {k: 0 for k in ('descent_seconds', 'descent_candidates', 'descent_filtered',
                                    'descent_site_cache_hits', 'descent_site_enumerations',
                                    'descent_deferred', 'descent_recovered')}
    while time.perf_counter()-start < search_time:
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
        remaining = search_time-(time.perf_counter()-start)
        if remaining <= 0:
            break
        share = .4 if attempts == 0 and len(customers) > 8 else .1
        iteration_budget = min(max_iterations-iterations, max(1, int(max_iterations*share))) if max_iterations else 0
        key = tuple(sorted(candidate.items()))
        warm = incumbents.get(key)
        seed_state = None
        if warm is None and best is not None:
            seed_state = reassigned_state(fixed, best, min(start+search_time, time.perf_counter()+max_time/20),
                                           scorer=context.route_scorer)
            projected += int(seed_state is not None)
        remaining = search_time-(time.perf_counter()-start)
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
                best, best_cost, current = lifted, solved['info']['objective'], dict(candidate)
                last_improvement = subproblem_start+(solved['info'].get('time_to_best') or 0)
                history.append(dict(time=last_improvement, iteration=iterations, objective=best_cost))
    if terminal_enabled and best is not None:
        terminal_start = time.perf_counter()
        terminal_deadline = min(start+max_time, terminal_start+max_time/20)
        for _ in range(5):
            if time.perf_counter() >= terminal_deadline:
                break
            statistics = {}
            seed = _audited_tail_seed(problem, current, best, terminal_deadline, statistics)
            terminal_checks += 1
            for field, count in statistics.items():
                terminal_totals[field] += count
            if seed is None:
                break
            lifted = lift_result(problem, seed['result'], seed['assignment'])
            if not lifted['audit']['valid'] or lifted['info']['objective'] >= best_cost-1e-9:
                break
            terminal_gains.append(best_cost-lifted['info']['objective'])
            best, best_cost, current = lifted, lifted['info']['objective'], dict(seed['assignment'])
            terminal_steps += 1
            direct_steps += int(seed['mode'] == 'direct')
            repaired_steps += int(seed['mode'] == 'repaired')
            last_improvement = time.perf_counter()-start
            history.append(dict(time=last_improvement, iteration=iterations, objective=best_cost))
        terminal_seconds = time.perf_counter()-terminal_start
    result = best if best is not None else empty_result(problem, 'alns')
    result['info'].update(status='FEASIBLE' if best else 'HEURISTIC_NO_SOLUTION', proven_optimal=False,
                          proven_infeasible=False, best_bound=None, mip_gap=None, proof_scope=None,
                          algorithm='customer_assignment_search_with_cycle_alns',
                          runtime=time.perf_counter()-start, seed=seed, iterations=iterations,
                          assignment_attempts=attempts, assignment_warm_starts=reused,
                          assignment_route_projections=projected,
                          assignment_terminal_checks=terminal_checks, assignment_terminal_steps=terminal_steps,
                          assignment_terminal_direct_steps=direct_steps,
                          assignment_terminal_repaired_steps=repaired_steps,
                          assignment_terminal_decodes=terminal_totals['decoded_candidates'],
                          assignment_terminal_audits=terminal_totals['audited_candidates'],
                          assignment_terminal_timeouts=terminal_totals['timed_out'],
                          assignment_terminal_gains=terminal_gains, assignment_terminal_seconds=terminal_seconds,
                          assignment_main_time_budget=search_time,
                          assignment_terminal_budget_policy=('main_95pct_terminal_at_most_5pct'
                              if terminal_enabled else 'original_full_budget'),
                          time_to_first_feasible=first_feasible, time_to_best=last_improvement,
                          initial_objective=initial_objective, incumbent_history=history,
                          operator_statistics_scope='all_partition_subsolves',
                          history_scope=('completed_partition_subsolves_and_terminal_descent' if terminal_steps
                                         else 'best_at_completed_partition_subsolve'),
                          assignment_budget_policy='initial_40pct_then_project_and_refine')
    result['info'].update(operator_totals, **descent_totals)
    return result
