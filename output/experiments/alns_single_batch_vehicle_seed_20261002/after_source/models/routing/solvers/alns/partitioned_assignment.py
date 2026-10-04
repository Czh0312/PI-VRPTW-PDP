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
from .neighborhoods import clone_state, state_from_result, _goods_ready, _route_score
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


def _pack_delivery_vehicles(problem, original, deadline, *, score_route=None):
    """Propose a real vehicle schedule, preserving every delivery sequence.

    Latest-departure bounds only rank routes. Local scores choose vehicles;
    the complete decoder/audit must still check supply, capacity and time.
    Interruption returns no partial state and never changes the input.
    """
    if problem.reusable or len(problem.operating_policy.batches) != 1:
        return None
    if time.perf_counter() >= deadline:
        return None
    if any(route['satellite'] not in problem.satellites or
           any(kind != 'd' or oid not in problem.order_map for oid,kind in route['tasks'])
           for route in original['e2']):
        return None
    state = clone_state(original)
    p, f = problem.operating_policy, problem.fleet
    goods, packed = _goods_ready(problem,state), []
    score_route = score_route or _route_score
    for site in problem.satellites:
        ready = [p.cycle_start]*f.e2_per_sat_count
        slots = [0]*f.e2_per_sat_count
        routes = [r for r in state['e2'] if r['satellite'] == site and r['tasks']]
        def latest_departure(route):
            latest = p.cycle_end-p.e2_turnaround_time-p.e2_return_unload_time
            previous = site
            for oid,kind in reversed(route['tasks']):
                order = problem.order_map[oid]
                latest -= problem.travel(order.customer,previous)+order.service_time
                latest = min(latest,order.delivery_window[1],
                             problem.batch_map[order.batch].cutoff-order.service_time)
                previous = order.customer
            return latest-problem.travel(site,previous)
        for route in sorted(routes,key=latest_departure):
            if time.perf_counter() >= deadline:
                return None
            nodes = [site]+[problem.order_map[oid].customer for oid,kind in route['tasks']]+[site]
            distance = sum(problem.distance(a,b) for a,b in zip(nodes,nodes[1:]))
            options = []
            for vehicle in range(f.e2_per_sat_count):
                if time.perf_counter() >= deadline:
                    return None
                if slots[vehicle] >= p.e2_max_trips_per_vehicle:
                    continue
                score,finish = score_route(problem,route,goods,ready[vehicle])
                options.append((score-distance,finish,vehicle))
            if not options:
                return None
            _,finish,vehicle = min(options)
            route['vehicle'] = vehicle
            ready[vehicle] = finish+p.e2_turnaround_time
            slots[vehicle] += 1
            packed.append(route)
    if time.perf_counter() >= deadline:
        return None
    state['e2'] = packed
    return state


def _audited_tail_seed(problem, current, incumbent, deadline, statistics):
    """Compare direct/projected sequences, also packing each onto real vehicles.

    Three geometric options, four representations each, one common deadline.
    Only fresh complete fixed and lifted audits may supply an improving seed.
    """
    statistics.update(geometric_options=0,decoded_candidates=0,audited_candidates=0,
                      improving_candidates=0,timed_out=0,pack_calls=0,pack_completed=0,
                      pack_timeouts=0,lifted_audits=0)
    if problem.reusable or len(problem.operating_policy.batches) != 1:
        return None
    if time.perf_counter() >= deadline:
        statistics['timed_out'] = 1
        return None
    original = state_from_result(problem,incumbent)
    options = _tail_options(problem,current,original,deadline)
    if options is None:
        statistics['timed_out'] = 1
        return None
    statistics['geometric_options'] = len(options)
    best,best_cost = None,incumbent['info']['objective']
    for option in options[:3]:
        if time.perf_counter() >= deadline:
            statistics['timed_out'] = 1
            break
        assignment,fixed,direct = _tail_state(problem,current,original,option)
        context = SingleBatchCache(fixed)
        for representation in ('direct','repaired'):
            if time.perf_counter() >= deadline:
                statistics['timed_out'] = 1
                break
            state = (direct if representation == 'direct' else
                     reassigned_state(fixed,incumbent,deadline,scorer=context.route_scorer))
            if state is None:
                continue
            for packed in (False,True):
                if time.perf_counter() >= deadline:
                    statistics['timed_out'] = 1
                    break
                mode = representation+('_packed' if packed else '')
                if packed:
                    statistics['pack_calls'] += 1
                    proposal = _pack_delivery_vehicles(fixed,state,deadline,score_route=context.score_route)
                    if proposal is None:
                        statistics['pack_timeouts'] += int(time.perf_counter() >= deadline)
                        continue
                    statistics['pack_completed'] += 1
                else:
                    proposal = state
                score,result,issues = context(proposal)
                statistics['decoded_candidates'] += 1
                if issues or score >= best_cost-1e-9:
                    continue
                audit = audit_solution(fixed,result)
                statistics['audited_candidates'] += 1
                if not audit['valid'] or not audit.get('periodic_replay',{}).get('valid'):
                    continue
                lifted = lift_result(problem,result,assignment)
                statistics['lifted_audits'] += 1
                if not lifted['audit']['valid'] or not lifted['audit'].get('periodic_replay',{}).get('valid'):
                    continue
                statistics['improving_candidates'] += 1
                best_cost = score
                result['audit'] = audit
                best = dict(assignment=assignment,fixed=fixed,context=context,result=result,
                            geometry_delta=option[0],gain=incumbent['info']['objective']-score,mode=mode)
    return best


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42, *, audited_route_seeds=True):
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
    seed_checks, seed_starts, direct_starts, repaired_starts = 0, 0, 0, 0
    seed_totals = {key: 0 for key in ('geometric_options','decoded_candidates','audited_candidates',
                                   'improving_candidates','timed_out','pack_calls','pack_completed','pack_timeouts','lifted_audits')}
    seed_gains, seed_modes, packed_starts = [], [], 0
    first_feasible = last_improvement = None
    history, initial_objective = [], None
    operator_totals = {k: {} for k in ('operator_attempts', 'operator_improvements', 'operator_noops')}
    descent_totals = {k: 0 for k in ('descent_seconds', 'descent_candidates', 'descent_filtered',
                                    'descent_site_cache_hits', 'descent_site_enumerations',
                                    'descent_deferred', 'descent_recovered')}
    while time.perf_counter()-start < max_time:
        route_seed = None
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
            if audited_route_seeds and best is not None and attempts % 4 == 2:
                seed_checks += 1
                statistics = {}
                route_seed = _audited_tail_seed(problem, current, best,
                    min(start+max_time, time.perf_counter()+max_time/20), statistics)
                for field, count in statistics.items():
                    seed_totals[field] += count
            if route_seed is not None:
                candidate = route_seed['assignment']
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
        fixed = route_seed['fixed'] if route_seed is not None else assigned_problem(problem, candidate)
        context = route_seed['context'] if route_seed is not None else SingleBatchCache(fixed)
        remaining = max_time-(time.perf_counter()-start)
        if remaining <= 0:
            break
        share = .4 if attempts == 0 and len(customers) > 8 else .1
        iteration_budget = min(max_iterations-iterations, max(1, int(max_iterations*share))) if max_iterations else 0
        key = tuple(sorted(candidate.items()))
        warm = route_seed['result'] if route_seed is not None else incumbents.get(key)
        seed_state = None
        if warm is None and best is not None:
            seed_state = reassigned_state(fixed, best, min(start+max_time, time.perf_counter()+max_time/20),
                                           scorer=context.route_scorer)
            projected += int(seed_state is not None)
        remaining = max_time-(time.perf_counter()-start)
        if remaining <= 0:
            break
        reused += int(warm is not None and route_seed is None)
        if route_seed is not None:
            seed_starts += 1
            direct_starts += int(route_seed['mode'] == 'direct')
            repaired_starts += int(route_seed['mode'] == 'repaired')
            seed_gains.append(route_seed['gain'])
            seed_modes.append(route_seed['mode'])
            packed_starts += int(route_seed['mode'].endswith('_packed'))
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
    result = best if best is not None else empty_result(problem, 'alns')
    result['info'].update(status='FEASIBLE' if best else 'HEURISTIC_NO_SOLUTION', proven_optimal=False,
                          proven_infeasible=False, best_bound=None, mip_gap=None, proof_scope=None,
                          algorithm='customer_assignment_search_with_cycle_alns',
                          runtime=time.perf_counter()-start, seed=seed, iterations=iterations,
                          assignment_attempts=attempts, assignment_warm_starts=reused,
                          assignment_route_projections=projected,
                          assignment_route_seed_checks=seed_checks, assignment_route_seed_starts=seed_starts,
                          assignment_route_seed_direct_starts=direct_starts,
                          assignment_route_seed_repaired_starts=repaired_starts,
                          assignment_route_seed_options=seed_totals['geometric_options'],
                          assignment_route_seed_decodes=seed_totals['decoded_candidates'],
                          assignment_route_seed_audits=seed_totals['audited_candidates'],
                          assignment_route_seed_improvements=seed_totals['improving_candidates'],
                          assignment_route_seed_timeouts=seed_totals['timed_out'],
                          assignment_route_seed_gains=seed_gains, assignment_route_seed_modes=seed_modes,
                          assignment_route_seed_packed_starts=packed_starts,
                          assignment_route_seed_pack_calls=seed_totals['pack_calls'],
                          assignment_route_seed_pack_completed=seed_totals['pack_completed'],
                          assignment_route_seed_pack_timeouts=seed_totals['pack_timeouts'],
                          assignment_route_seed_lifted_audits=seed_totals['lifted_audits'],
                          time_to_first_feasible=first_feasible, time_to_best=last_improvement,
                          initial_objective=initial_objective, incumbent_history=history,
                          operator_statistics_scope='all_partition_subsolves',
                          history_scope='best_at_completed_partition_subsolve',
                          assignment_budget_policy='initial_40pct_then_project_and_refine')
    result['info'].update(operator_totals, **descent_totals)
    return result
