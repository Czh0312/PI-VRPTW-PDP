"""Delivery-only partition exploration retained for M2; route repair is shared."""
import math
import random
import time
from collections import OrderedDict

from models.common.cycle.assignment import assigned_problem, input_assignment, lift_result
from models.common.cycle.state import empty_result
from models.common.cycle.precheck import travel_lower_bounds
from models.routing.model_2.alns.construction import reassigned_state
from models.routing.model_2.alns.performance.single_batch_cache import SingleBatchCache
from models.routing.model_2.alns.assignment.moves import consolidation_candidates


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42):
    if not math.isfinite(max_time) or max_time <= 0 or max_iterations < 0:
        raise ValueError('Invalid assignment-search budget')
    from models.routing.model_2.alns.search import solve_alns as solve_fixed
    start, rng = time.perf_counter(), random.Random(seed)
    travel_bounds = travel_lower_bounds(problem)
    current = input_assignment(problem)
    customers = sorted(current)
    nearest = {c: min(problem.satellites, key=lambda s: problem.distance(s, c)) for c in customers}
    starts, seen_starts = [], set()
    for assignment in [dict(current), nearest] + consolidation_candidates(problem, current):
        key = tuple(sorted(assignment.items()))
        if key not in seen_starts:
            seen_starts.add(key)
            starts.append(assignment)
    best, best_cost, attempts, iterations = None, math.inf, 0, 0
    incumbents, reused, projected = OrderedDict(), 0, 0
    first_feasible = last_improvement = None
    history, initial_objective = [], None
    operator_totals = {k: {} for k in ('operator_attempts', 'operator_improvements', 'operator_noops')}
    descent_totals = {k: 0 for k in ('descent_seconds', 'descent_candidates', 'descent_filtered',
                                    'descent_site_cache_hits', 'descent_site_enumerations',
                                    'descent_deferred', 'descent_recovered')}
    while time.perf_counter()-start < max_time:
        if attempts and (iterations >= max_iterations or not customers):
            break
        if attempts < len(starts):
            candidate = dict(starts[attempts])
        elif attempts % 4 == 0:
            coordinated = consolidation_candidates(problem, current)
            candidate = rng.choice(coordinated) if coordinated else dict(current)
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
        remaining = max_time-(time.perf_counter()-start)
        if remaining <= 0:
            break
        # Bound every partition visit so the initial ownership cannot consume
        # 40% of the call before alternatives are proposed. Existing warm
        # starts and periodic incumbent revisits still deepen useful routes.
        share = .05
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
                          time_to_first_feasible=first_feasible, time_to_best=last_improvement,
                          initial_objective=initial_objective, incumbent_history=history,
                          operator_statistics_scope='all_partition_subsolves',
                          history_scope='best_at_completed_partition_subsolve',
                          assignment_budget_policy='coordinated_starts_5pct_partition_visits')
    result['info'].update(operator_totals, **descent_totals)
    return result
