"""Joint assignment MILP for bounded models; certified enumeration fallback."""
import itertools
import math
import time
from collections import OrderedDict

from models.routing.common.cycle.assignment import assigned_problem, input_assignment, lift_result
from models.routing.common.cycle.state import empty_result
from .bounds import distance_lower_bound, free_assignment_lower_bound
from .domains import assignment_domains, public_domain_check
from models.routing.common.cycle.precheck import travel_lower_bounds, rejected_result


def _assignment_stream(problem, domains=None):
    initial = input_assignment(problem)
    customers = sorted(initial)
    domains = domains or {c: problem.satellites for c in customers}
    options = [sorted(domains[c], key=lambda s: problem.distance(s, c)+problem.distance(c, s)) for c in customers]
    nearest = {c: sites[0] for c, sites in zip(customers, options)}
    initial = {c: s if s in domains[c] else nearest[c] for c, s in initial.items()}
    preferred = [initial, nearest]
    moves = []
    for base in (initial, nearest):
        for c in customers:
            for s in domains[c]:
                if s != base[c]:
                    change = (problem.distance(s, c)+problem.distance(c, s)
                              -problem.distance(base[c], c)-problem.distance(c, base[c]))
                    moves.append((change, c, s, {**base, c: s}))
    preferred.extend(row[3] for row in sorted(moves, key=lambda row: row[:3]))
    seen = set()
    for candidate in preferred:
        key = tuple(candidate[c] for c in customers)
        if key not in seen:
            seen.add(key)
            yield candidate
    for values in itertools.product(*options):
        if values not in seen:
            yield dict(zip(customers, values))


def solve_exact(problem, time_limit=60, seed=42, verbose=False, strategy='auto'):
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('time_limit must be finite and positive')
    if strategy not in ('auto', 'joint', 'enumeration'):
        raise ValueError('Unknown exact assignment strategy')
    start = time.perf_counter()
    travel = travel_lower_bounds(problem)
    check = assignment_domains(problem, travel)
    total = len(problem.satellites)**len(check['domains'])
    if not check['passed']:
        result = rejected_result(problem, 'exact', public_domain_check(check), time.perf_counter()-start)
        result['info'].update(assignment_count=total, assignments_closed=total, assignments_visited=0,
                              assignment_strategy='necessary_domain_proof')
        return result
    domain_total = math.prod(len(sites) for sites in check['domains'].values())
    # Estimate the route-indexed E2 arc expansion before materializing it.
    f, p = problem.fleet, problem.operating_policy
    estimate = 0
    for site in problem.satellites:
        tasks = sum(site in check['domains'][o.customer] for o in problem.orders)*len(problem.task_kinds)
        estimate += (tasks*max(0, tasks-1)+2*tasks)*min(f.e2_per_sat_count, tasks)*min(p.e2_max_trips_per_vehicle, tasks)
    selected = ('joint' if estimate <= 60000 else 'enumeration') if strategy == 'auto' else strategy
    bound = free_assignment_lower_bound(problem, check['domains'])
    remaining = time_limit-(time.perf_counter()-start)
    if remaining <= 0:
        result = empty_result(problem, 'exact')
        result['info'].update(status='TIME_LIMIT_PRECHECK', best_bound=bound)
    elif selected == 'joint':
        from .solver import solve_exact as solve_joint
        warm, warm_runtime = None, 0.0
        if estimate > 5000:
            # A compact fixed partition often supplies a much better incumbent
            # before the larger joint model reaches its first search node.
            initial = input_assignment(problem)
            initial = {c: s if s in check['domains'][c] else check['domains'][c][0]
                       for c, s in initial.items()}
            warm_start = time.perf_counter()
            fixed = assigned_problem(problem, initial)
            trial = solve_joint(fixed, time_limit=min(remaining, time_limit*.2), seed=seed,
                                 verbose=verbose, _travel_bounds=travel)
            if trial.get('audit', {}).get('valid'):
                warm = lift_result(problem, trial, initial)
                # A fixed partition's optimum/bound is not a global certificate.
                warm['info'].update(proven_optimal=False, proven_infeasible=False,
                                     best_bound=bound, mip_gap=None, status='TIME_LIMIT')
            warm_runtime = time.perf_counter()-warm_start
        remaining = time_limit-(time.perf_counter()-start)
        if remaining > 0:
            result = solve_joint(problem, time_limit=remaining, seed=seed, verbose=verbose,
                                 initial_result=warm, _travel_bounds=travel, _domain_check=check)
        else:
            result = warm if warm is not None else empty_result(problem, 'exact')
            result['info'].update(status='TIME_LIMIT', proven_optimal=False, proven_infeasible=False)
        result['info'].update(initial_partition_runtime=warm_runtime,
                            initial_partition_solves=int(estimate > 5000),
                            initial_partition_objective=warm['info']['objective'] if warm else None)
        result['info'].update(assignments_visited=0,
            assignments_closed=domain_total if result['info'].get('proven_optimal') or result['info'].get('proven_infeasible') else 0,
            subproblem_solves=int(estimate > 5000))
    else:
        result = _solve_enumeration(problem, time_limit=remaining, seed=seed, verbose=verbose,
                                     _travel_bounds=travel, _domain_check=check, _global_bound=bound)
    info = result['info']
    if not info.get('proven_infeasible'):
        info['best_bound'] = max(bound, info.get('best_bound') or 0.0)
        if info.get('objective') is not None:
            info['mip_gap'] = max(0, info['objective']-info['best_bound'])/max(1e-9, abs(info['objective']))
    info.update(runtime=time.perf_counter()-start, assignment_strategy=selected,
                assignment_count=total, domain_assignment_count=domain_total,
                domain_assignments_eliminated=total-domain_total, estimated_joint_e2_arcs=estimate,
                global_assignment_distance_bound=bound,
                assignments_closed=info.get('assignments_closed', 0)+total-domain_total)
    result['assignment_precheck'] = public_domain_check(check)
    return result


def _solve_enumeration(problem, time_limit=60, seed=42, verbose=False,
                       _travel_bounds=None, _domain_check=None, _global_bound=0.0):
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('time_limit must be finite and positive')
    from .solver import solve_exact as solve_fixed
    start = time.perf_counter()
    travel_bounds = _travel_bounds or travel_lower_bounds(problem)
    domains = _domain_check['domains'] if _domain_check else None
    total = math.prod(len(sites) for sites in domains.values()) if domains is not None else len(problem.satellites)**len({o.customer for o in problem.orders})
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
            record = dict(assignment=assignment, lower_bound=max(_global_bound, distance_lower_bound(fixed)), solves=0)
            records.append(record)
        record.update(status=info['status'], closed=closed,
                      infeasible=bool(info.get('proven_infeasible')), solves=record['solves']+1,
                      objective=info.get('objective') if valid else record.get('objective'),
                      lower_bound=max(record['lower_bound'], info.get('best_bound') or 0))
        if valid and info['objective'] < best_cost:
            candidate = lift_result(problem, solved, assignment)
            if candidate.get('audit', {}).get('valid'):
                best, best_cost = candidate, info['objective']

    for assignment in _assignment_stream(problem, domains):
        remaining = time_limit-(time.perf_counter()-start)
        if remaining <= 0:
            break
        fixed = assigned_problem(problem, assignment)
        remaining = time_limit-(time.perf_counter()-start)
        if remaining <= 0:
            break
        bound = max(_global_bound, distance_lower_bound(fixed))
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
        bounds.append(_global_bound)
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
