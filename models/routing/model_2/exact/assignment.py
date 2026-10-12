"""Sparse joint assignment MILP and certified domain-tree search."""
import itertools
import math
import time
from collections import OrderedDict

from models.common.cycle.assignment import assigned_problem, input_assignment, lift_result
from models.common.cycle.state import empty_result
from models.routing.model_2.exact.distance_bounds import distance_lower_bound, free_assignment_lower_bound
from models.routing.model_2.exact.domains import assignment_domains, public_domain_check
from models.routing.model_2.exact.strengthening import joint_arc_estimate
from models.common.cycle.precheck import travel_lower_bounds, rejected_result


JOINT_ARC_LIMIT = 120000


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


def _assignment_seed(problem, domains, deadline):
    """Bounded primal-only probes; no candidate restriction or proof transfer."""
    from models.routing.model_2.exact.initial_solution import construct_incumbent
    best = fixed_best = None
    tried = 0
    preferred = []
    for assignment in itertools.islice(_assignment_stream(problem, domains), 64):
        if time.perf_counter() >= deadline:
            break
        if len(preferred) < 2:
            preferred.append(assignment)
        # Preserve breadth: expensive insertion in every partition would use
        # the entire seed budget before reaching promising later partitions.
        trial = construct_incumbent(assigned_problem(problem, assignment), deadline, split_trips=False)
        tried += 1
        if trial is not None and trial.get('audit', {}).get('valid'):
            if best is None or trial['info']['objective'] < best['info']['objective']:
                lifted = lift_result(problem, trial, assignment)
                if lifted['audit']['valid']:
                    best, fixed_best = lifted, trial
    refinements = ([best['assignment']] if best is not None else [])+preferred
    seen = set()
    for assignment in refinements:
        if time.perf_counter() >= deadline:
            break
        key = tuple(sorted(assignment.items()))
        if key in seen:
            continue
        seen.add(key)
        trial = construct_incumbent(assigned_problem(problem, assignment), deadline)
        if trial is not None and (best is None or trial['info']['objective'] < best['info']['objective']):
            lifted = lift_result(problem, trial, assignment)
            if lifted['audit']['valid']:
                best, fixed_best = lifted, trial
    return best, fixed_best, tried


def solve_exact(problem, time_limit=60, seed=42, verbose=False, strategy='auto'):
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('time_limit must be finite and positive')
    if strategy not in ('auto', 'joint', 'domain_tree', 'enumeration'):
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
    bound = free_assignment_lower_bound(problem, check['domains'])
    try:
        estimate = joint_arc_estimate(problem, check, start+time_limit)
        # Compression reduces binary copies, not the underlying routing
        # difficulty. Keep the seed/warm-start trigger on the indexed scale.
        warm_estimate = joint_arc_estimate(problem, check, start+time_limit, indexed=True)
    except TimeoutError:
        result = empty_result(problem, 'exact')
        result['info'].update(status='TIME_LIMIT_PRECHECK', best_bound=bound,
            runtime=time.perf_counter()-start, assignment_strategy='precheck',
            assignment_count=total, domain_assignment_count=domain_total,
            assignments_closed=total-domain_total, assignments_visited=0)
        result['assignment_precheck'] = public_domain_check(check)
        return result
    selected = ('joint' if estimate <= JOINT_ARC_LIMIT else 'domain_tree') if strategy == 'auto' else strategy
    seed_start = time.perf_counter()
    primal = fixed_primal = None
    seed_probes = 0
    if selected != 'enumeration' and warm_estimate > 5000:
        primal, fixed_primal, seed_probes = _assignment_seed(problem, check['domains'],
            min(start+time_limit, seed_start+min(.2, time_limit*.05)))
    seed_runtime = time.perf_counter()-seed_start
    remaining = time_limit-(time.perf_counter()-start)
    if remaining <= 0:
        result = primal if primal is not None else empty_result(problem, 'exact')
        result['info'].update(status='TIME_LIMIT_PRECHECK', best_bound=bound)
    elif selected == 'joint':
        from models.routing.model_2.exact.engine import solve_exact as solve_joint
        warm, warm_runtime = primal, 0.0
        if warm_estimate > 5000:
            # A compact fixed partition often supplies a much better incumbent
            # before the larger joint model reaches its first search node.
            initial = input_assignment(problem)
            initial = {c: s if s in check['domains'][c] else check['domains'][c][0]
                       for c, s in initial.items()}
            warm_start = time.perf_counter()
            fixed = assigned_problem(problem, initial)
            trial = solve_joint(fixed, time_limit=min(remaining, time_limit*.2), seed=seed,
                                 verbose=verbose, _travel_bounds=travel,
                                 initial_result=fixed_primal if primal is not None and primal['assignment'] == initial else None)
            if trial.get('audit', {}).get('valid') and (warm is None or
                    trial['info']['objective'] < warm['info']['objective']):
                warm = lift_result(problem, trial, initial)
            if warm is not None:
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
                            initial_partition_solves=int(warm_estimate > 5000),
                            initial_partition_objective=warm['info']['objective'] if warm else None)
        result['info'].update(assignments_visited=0,
            assignments_closed=domain_total if result['info'].get('proven_optimal') or result['info'].get('proven_infeasible') else 0,
            subproblem_solves=int(warm_estimate > 5000))
    elif selected == 'domain_tree':
        from models.routing.model_2.exact.assignment_tree import solve_domain_tree
        result = solve_domain_tree(problem, remaining, seed, verbose, travel, check, bound, JOINT_ARC_LIMIT,
                                   initial_result=primal, initial_fixed_result=fixed_primal)
    else:
        result = _solve_enumeration(problem, time_limit=remaining, seed=seed, verbose=verbose,
                                     _travel_bounds=travel, _domain_check=check, _global_bound=bound)
    info = result['info']
    if not info.get('proven_infeasible'):
        info['best_bound'] = max(bound, info.get('best_bound') or 0.0)
        if info.get('objective') is not None:
            info['mip_gap'] = max(0, info['objective']-info['best_bound'])/max(1e-9, abs(info['objective']))
    info.update(runtime=time.perf_counter()-start, assignment_strategy=selected,
                assignment_seed_probes=seed_probes, assignment_seed_runtime=seed_runtime,
                assignment_seed_objective=primal['info']['objective'] if primal is not None else None,
                assignment_count=total, domain_assignment_count=domain_total,
                domain_assignments_eliminated=total-domain_total, estimated_joint_e2_arcs=estimate,
                estimated_indexed_e2_arcs=warm_estimate,
                global_assignment_distance_bound=bound,
                assignments_closed=info.get('assignments_closed', 0)+total-domain_total)
    result['assignment_precheck'] = public_domain_check(check)
    return result


def _solve_enumeration(problem, time_limit=60, seed=42, verbose=False,
                       _travel_bounds=None, _domain_check=None, _global_bound=0.0):
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('time_limit must be finite and positive')
    from models.routing.model_2.exact.engine import solve_exact as solve_fixed
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
