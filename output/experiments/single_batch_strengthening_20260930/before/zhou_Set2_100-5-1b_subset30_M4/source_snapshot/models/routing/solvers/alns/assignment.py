"""Heuristic customer assignment search with ALNS fixed-assignment subproblems."""
import math
import random
import time
from collections import OrderedDict

from models.routing.common.cycle.assignment import assigned_problem, input_assignment, lift_result
from models.routing.common.cycle.state import empty_result
from models.routing.common.cycle.precheck import travel_lower_bounds
from .construction import reassigned_state


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42):
    if not math.isfinite(max_time) or max_time <= 0 or max_iterations < 0:
        raise ValueError('Invalid assignment-search budget')
    from .solver import solve_alns as solve_fixed
    start, rng = time.perf_counter(), random.Random(seed)
    travel_bounds = travel_lower_bounds(problem)
    current = input_assignment(problem)
    customers = sorted(current)
    nearest = {c: min(problem.satellites, key=lambda s: problem.distance(s, c)) for c in customers}
    best, best_cost, attempts, iterations = None, math.inf, 0, 0
    incumbents, reused, projected = OrderedDict(), 0, 0
    first_feasible = last_improvement = None
    while time.perf_counter()-start < max_time:
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
        remaining = max_time-(time.perf_counter()-start)
        if remaining <= 0:
            break
        share = .4 if attempts == 0 and len(customers) > 8 else .1
        iteration_budget = min(max_iterations-iterations, max(1, int(max_iterations*share))) if max_iterations else 0
        key = tuple(sorted(candidate.items()))
        warm = incumbents.get(key)
        seed_state = None
        if warm is None and best is not None:
            seed_state = reassigned_state(fixed, best, min(start+max_time, time.perf_counter()+max_time/20))
            projected += int(seed_state is not None)
        remaining = max_time-(time.perf_counter()-start)
        if remaining <= 0:
            break
        reused += int(warm is not None)
        solved = solve_fixed(fixed, max_time=min(remaining, max(.01, max_time*share)),
                             max_iterations=iteration_budget, seed=seed+attempts, initial_result=warm,
                             seed_state=seed_state, _travel_bounds=travel_bounds)
        attempts += 1
        used = solved['info'].get('iterations', 0)
        # A precheck-rejected partition still consumes a proposal budget.
        iterations += max(1, used) if max_iterations else 0
        if solved.get('audit', {}).get('valid'):
            incumbents[key] = solved
            incumbents.move_to_end(key)
            if len(incumbents) > 32:
                incumbents.popitem(last=False)
            lifted = lift_result(problem, solved, candidate)
            if lifted['audit']['valid'] and solved['info']['objective'] < best_cost:
                best, best_cost, current = lifted, solved['info']['objective'], dict(candidate)
                last_improvement = time.perf_counter()-start
                if first_feasible is None:
                    first_feasible = last_improvement
    result = best if best is not None else empty_result(problem, 'alns')
    result['info'].update(status='FEASIBLE' if best else 'HEURISTIC_NO_SOLUTION', proven_optimal=False,
                          proven_infeasible=False, best_bound=None, mip_gap=None, proof_scope=None,
                          algorithm='customer_assignment_search_with_cycle_alns',
                          runtime=time.perf_counter()-start, seed=seed, iterations=iterations,
                          assignment_attempts=attempts, assignment_warm_starts=reused,
                          assignment_route_projections=projected,
                          time_to_first_feasible=first_feasible, time_to_best=last_improvement,
                          assignment_budget_policy='initial_40pct_then_project_and_refine')
    return result
