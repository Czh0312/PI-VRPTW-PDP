"""Heuristic customer assignment search with ALNS fixed-assignment subproblems."""
import math
import random
import time

from models.routing.common.cycle.assignment import assigned_problem, input_assignment, lift_result
from models.routing.common.cycle.state import empty_result


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42):
    if not math.isfinite(max_time) or max_time <= 0 or max_iterations < 0:
        raise ValueError('Invalid assignment-search budget')
    from .solver import solve_alns as solve_fixed
    start, rng = time.perf_counter(), random.Random(seed)
    current = input_assignment(problem)
    customers = sorted(current)
    nearest = {c: min(problem.satellites, key=lambda s: problem.distance(s, c)) for c in customers}
    best, best_cost, attempts, iterations = None, math.inf, 0, 0
    while time.perf_counter()-start < max_time:
        if attempts and (iterations >= max_iterations or not customers):
            break
        if attempts == 0:
            candidate = dict(current)
        elif attempts == 1:
            candidate = nearest
        else:
            candidate = dict(current)
            if len(customers) > 1 and rng.random() < .3:
                a, b = rng.sample(customers, 2)
                candidate[a], candidate[b] = candidate[b], candidate[a]
            else:
                customer = rng.choice(customers)
                candidate[customer] = rng.choice(problem.satellites)
        fixed = assigned_problem(problem, candidate)
        remaining = max_time-(time.perf_counter()-start)
        if remaining <= 0:
            break
        iteration_budget = min(max_iterations-iterations, max(1, max_iterations//10)) if max_iterations else 0
        solved = solve_fixed(fixed, max_time=min(remaining, max(.01, max_time/10)),
                             max_iterations=iteration_budget, seed=seed+attempts)
        attempts += 1
        used = solved['info'].get('iterations', 0)
        # A precheck-rejected partition still consumes a proposal budget.
        iterations += max(1, used) if max_iterations else 0
        if solved.get('audit', {}).get('valid'):
            lifted = lift_result(problem, solved, candidate)
            if lifted['audit']['valid'] and solved['info']['objective'] < best_cost:
                best, best_cost, current = lifted, solved['info']['objective'], dict(candidate)
    result = best if best is not None else empty_result(problem, 'alns')
    result['info'].update(status='FEASIBLE' if best else 'HEURISTIC_NO_SOLUTION', proven_optimal=False,
                          proven_infeasible=False, best_bound=None, mip_gap=None, proof_scope=None,
                          algorithm='customer_assignment_search_with_cycle_alns',
                          runtime=time.perf_counter()-start, seed=seed, iterations=iterations,
                          assignment_attempts=attempts)
    return result
