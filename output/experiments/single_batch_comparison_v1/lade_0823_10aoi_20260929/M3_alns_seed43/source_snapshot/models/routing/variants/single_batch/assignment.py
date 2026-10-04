"""Customer-level assignment search with fixed-assignment cycle subproblems.

Exact search enumerates the complete assignment space. A time limit never
turns the best visited partition into a global optimality certificate.
"""
from copy import deepcopy
from dataclasses import replace
import itertools
import math
import random
import time

from ..model_3.multibatch.instance import MultiBatchInstance
from ..model_3.multibatch.state import empty_result


def validate_assignment(problem, assignment):
    customers = {o.customer for o in problem.orders}
    if not isinstance(assignment, dict) or set(assignment) != customers:
        raise ValueError('Assignment must cover exactly the customer IDs in the orders')
    if any(site not in problem.satellites for site in assignment.values()):
        raise ValueError('Assignment contains an unknown satellite')
    return dict(assignment)


def assigned_problem(problem, assignment):
    assignment = validate_assignment(problem, assignment)
    orders = [replace(o, satellite=assignment[o.customer]) for o in problem.orders]
    return MultiBatchInstance(problem.network, orders, problem.operating_policy,
                              model=3 if problem.reusable else 1)


def input_assignment(problem):
    result = {}
    for order in problem.orders:
        result.setdefault(order.customer, order.satellite)
    return result


def _lift(problem, fixed_result, assignment):
    result = deepcopy(fixed_result)
    result['model'] = problem.model_number
    result['assignment'] = dict(assignment)
    result.pop('audit', None)
    if result['info'].get('objective') is not None:
        from ..model_3.multibatch.audit import audit_solution
        result['audit'] = audit_solution(problem, result)
        if not result['audit']['valid']:
            result['info'].update(status='AUDIT_FAILED', proven_optimal=False)
    return result


def _assignment_stream(problem):
    initial = input_assignment(problem)
    yield initial
    customers = sorted(initial)
    options = [sorted(problem.satellites, key=lambda s: problem.distance(s, c)) for c in customers]
    for values in itertools.product(*options):
        candidate = dict(zip(customers, values))
        if candidate != initial:
            yield candidate


def solve_exact(problem, time_limit=60, seed=42, verbose=False):
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('time_limit must be finite and positive')
    from ..model_3.multibatch.exact_solver import solve_exact as solve_fixed
    start = time.perf_counter()
    total = len(problem.satellites)**len({o.customer for o in problem.orders})
    best, best_cost, records = None, math.inf, []
    for assignment in _assignment_stream(problem):
        remaining = time_limit-(time.perf_counter()-start)
        if remaining <= 0:
            break
        fixed = assigned_problem(problem, assignment)
        remaining = time_limit-(time.perf_counter()-start)
        if remaining <= 0:
            break
        budget = min(remaining, max(.05, remaining/min(8, total-len(records))))
        solved = solve_fixed(fixed, time_limit=budget, seed=seed, verbose=verbose)
        info = solved['info']
        valid = bool(solved.get('audit', {}).get('valid'))
        closed = bool(info.get('proven_infeasible') or (info.get('proven_optimal') and valid))
        records.append(dict(assignment=assignment, status=info['status'], closed=closed,
                            infeasible=bool(info.get('proven_infeasible')),
                            objective=info.get('objective') if valid else None,
                            lower_bound=info.get('best_bound')))
        if valid and info['objective'] < best_cost:
            candidate = _lift(problem, solved, assignment)
            if candidate.get('audit', {}).get('valid'):
                best, best_cost = candidate, info['objective']
    all_closed = len(records) == total and all(r['closed'] for r in records)
    infeasible = all_closed and all(r['infeasible'] for r in records)
    bounds = [r['lower_bound'] if r['lower_bound'] is not None else 0
              for r in records if not r['infeasible']]
    if len(records) < total:
        bounds.append(0)
    lower = min(bounds) if bounds else None
    result = best if best is not None else empty_result(problem, 'exact')
    result['info'].update(status='OPTIMAL' if best and all_closed else 'INFEASIBLE' if infeasible else 'TIME_LIMIT',
                          proven_optimal=bool(best and all_closed), proven_infeasible=infeasible,
                          proof_scope='all_customer_assignments_integer_time_cycle',
                          runtime=time.perf_counter()-start, assignment_count=total,
                          assignments_visited=len(records), assignments_closed=sum(r['closed'] for r in records),
                          best_bound=best_cost if best and all_closed else lower,
                          mip_gap=0.0 if best and all_closed else
                              (max(0.0, best_cost-lower)/max(1e-9, abs(best_cost)) if best and lower is not None else None),
                          subproblems=records)
    return result


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42):
    if not math.isfinite(max_time) or max_time <= 0 or max_iterations < 0:
        raise ValueError('Invalid assignment-search budget')
    from ..model_3.multibatch.alns import solve_alns as solve_fixed
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
            lifted = _lift(problem, solved, candidate)
            if lifted['audit']['valid'] and solved['info']['objective'] < best_cost:
                best, best_cost, current = lifted, solved['info']['objective'], dict(candidate)
    result = best if best is not None else empty_result(problem, 'alns')
    result['info'].update(status='FEASIBLE' if best else 'HEURISTIC_NO_SOLUTION', proven_optimal=False,
                          proven_infeasible=False, best_bound=None, mip_gap=None, proof_scope=None,
                          algorithm='customer_assignment_search_with_cycle_alns',
                          runtime=time.perf_counter()-start, seed=seed, iterations=iterations,
                          assignment_attempts=attempts)
    return result
