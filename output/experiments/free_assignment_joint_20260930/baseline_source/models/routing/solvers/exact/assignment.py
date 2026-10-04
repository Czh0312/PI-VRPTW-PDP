"""Exhaustive assignment search; only closed partitions certify global optimality."""
import itertools
import math
import time
from collections import OrderedDict

from models.routing.common.cycle.assignment import assigned_problem, input_assignment, lift_result
from models.routing.common.cycle.state import empty_result
from .bounds import distance_lower_bound
from models.routing.common.cycle.precheck import travel_lower_bounds


def _assignment_stream(problem):
    initial = input_assignment(problem)
    yield initial
    customers = sorted(initial)
    options = [sorted(problem.satellites, key=lambda s: problem.distance(s, c)) for c in customers]
    nearest = {c: sites[0] for c, sites in zip(customers, options)}
    if nearest != initial:
        yield nearest
    for values in itertools.product(*options):
        candidate = dict(zip(customers, values))
        if candidate != initial and candidate != nearest:
            yield candidate


def solve_exact(problem, time_limit=60, seed=42, verbose=False):
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('time_limit must be finite and positive')
    from .solver import solve_exact as solve_fixed
    start = time.perf_counter()
    travel_bounds = travel_lower_bounds(problem)
    total = len(problem.satellites)**len({o.customer for o in problem.orders})
    best, best_cost, records = None, math.inf, []
    incumbents = OrderedDict()

    def solve_partition(fixed, assignment, budget, record=None):
        nonlocal best, best_cost
        key = tuple(sorted(assignment.items()))
        solved = solve_fixed(fixed, time_limit=budget, seed=seed, verbose=verbose,
                             initial_result=incumbents.get(key), _travel_bounds=travel_bounds)
        info = solved['info']
        valid = bool(solved.get('audit', {}).get('valid'))
        if valid:
            incumbents[key] = solved
            incumbents.move_to_end(key)
            if len(incumbents) > 32:
                incumbents.popitem(last=False)
        closed = bool(info.get('proven_infeasible') or (info.get('proven_optimal') and valid))
        if record is None:
            record = dict(assignment=assignment, lower_bound=distance_lower_bound(fixed), solves=0)
            records.append(record)
        record.update(status=info['status'], closed=closed,
                      infeasible=bool(info.get('proven_infeasible')), solves=record['solves']+1,
                      objective=info.get('objective') if valid else record.get('objective'),
                      lower_bound=max(record['lower_bound'], info.get('best_bound') or 0))
        if valid and info['objective'] < best_cost:
            candidate = lift_result(problem, solved, assignment)
            if candidate.get('audit', {}).get('valid'):
                best, best_cost = candidate, info['objective']

    for assignment in _assignment_stream(problem):
        remaining = time_limit-(time.perf_counter()-start)
        if remaining <= 0:
            break
        fixed = assigned_problem(problem, assignment)
        remaining = time_limit-(time.perf_counter()-start)
        if remaining <= 0:
            break
        bound = distance_lower_bound(fixed)
        if bound >= best_cost:
            records.append(dict(assignment=assignment, status='BOUND_PRUNED', closed=True,
                                infeasible=False, objective=None, lower_bound=bound, solves=0))
            continue
        remaining = time_limit-(time.perf_counter()-start)
        if remaining <= 0:
            break
        # Establish the fixed-assignment incumbent before fragmenting the
        # budget across exponentially many alternatives. Unused solve time
        # stays available to later partitions when the first one closes early.
        share = remaining*.4 if not records and total > 1 else remaining/min(8, total-len(records))
        budget = min(remaining, max(.05, share))
        solve_partition(fixed, assignment, budget)
    # A once-visited time-limited subproblem remains open. Spend the remaining
    # budget on its proof instead of returning immediately after enumeration.
    while len(records) == total:
        pending = [r for r in records if not r['closed']]
        for record in pending:
            if record['lower_bound'] >= best_cost:
                record.update(closed=True, status='BOUND_PRUNED')
        pending = [r for r in pending if not r['closed']]
        remaining = time_limit-(time.perf_counter()-start)
        if not pending or remaining <= 0:
            break
        record = min(pending, key=lambda r: (r['solves'], r['lower_bound']))
        fixed = assigned_problem(problem, record['assignment'])
        remaining = time_limit-(time.perf_counter()-start)
        if remaining <= 0:
            break
        solve_partition(fixed, record['assignment'], remaining/min(4, len(pending)), record)
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
                          assignments_pruned=sum(r['status'] == 'BOUND_PRUNED' for r in records),
                          subproblem_solves=sum(r['solves'] for r in records),
                          best_bound=best_cost if best and all_closed else lower,
                          mip_gap=0.0 if best and all_closed else
                              (max(0.0, best_cost-lower)/max(1e-9, abs(best_cost)) if best and lower is not None else None),
                          subproblems=records)
    return result
