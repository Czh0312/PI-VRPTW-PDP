"""Exhaustive assignment search; only closed partitions certify global optimality."""
import itertools
import math
import time

from models.routing.common.cycle.assignment import assigned_problem, input_assignment, lift_result
from models.routing.common.cycle.state import empty_result


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
    from .solver import solve_exact as solve_fixed
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
            candidate = lift_result(problem, solved, assignment)
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
