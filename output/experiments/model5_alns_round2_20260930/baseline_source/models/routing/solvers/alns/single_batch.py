"""Single-batch route search with optional joint customer-assignment moves."""
from __future__ import annotations

from copy import deepcopy
import math
import random
import time

from models.routing.common.cycle.state import empty_result
from .evaluator import initial_state, evaluate
from models.routing.common.cycle.audit import audit_solution
from models.routing.common.cycle.precheck import check_necessary_conditions, rejected_result, travel_lower_bounds
from .construction import chronological_state, appointment_state
from .intensification import CycleDescent
from .neighborhoods import EvaluationCache, clone_state, destroy_repair, state_key, state_from_result


def _move(problem, original, rng, operator):
    state = clone_state(original)
    p, f = problem.operating_policy, problem.fleet
    routes = state['e2']
    if operator in ('relocate', 'pair') and routes:
        chosen = rng.choice(routes)
        oid, kind = rng.choice(chosen['tasks'])
        items = [(oid, k) for k in problem.task_kinds] if operator == 'pair' else [(oid, kind)]
        for r in routes:
            r['tasks'] = [t for t in r['tasks'] if t not in items]
        routes[:] = [r for r in routes if r['tasks']]
        o = problem.order_map[oid]
        destinations = [r for r in routes if r['satellite'] == o.satellite]
        if any(k == 'd' for _, k in items):
            destinations = [r for r in destinations if all(k != 'd' or problem.order_map[i].batch == o.batch for i, k in r['tasks'])]
        if not destinations or rng.random() < .15:
            target = dict(satellite=o.satellite, vehicle=rng.randrange(f.e2_per_sat_count), tasks=[])
            routes.append(target)
        else:
            target = rng.choice(destinations)
        where = rng.randrange(len(target['tasks'])+1)
        target['tasks'][where:where] = items
    elif operator == 'split_merge' and routes:
        source = rng.choice(routes)
        if len(source['tasks']) > 1 and rng.random() < .5:
            index = rng.randrange(1, len(source['tasks']))
            target = dict(satellite=source['satellite'], vehicle=rng.randrange(f.e2_per_sat_count),
                          tasks=source['tasks'][index:])
            source['tasks'] = source['tasks'][:index]
            routes.insert(routes.index(source)+1, target)
        else:
            others = [r for r in routes if r is not source and r['satellite'] == source['satellite']]
            if others:
                other = rng.choice(others)
                source['tasks'] += other['tasks']
                routes.remove(other)
    elif operator == 'vehicle' and routes:
        if rng.random() < .5:
            rng.choice(routes)['vehicle'] = rng.randrange(f.e2_per_sat_count)
        elif len(routes) > 1:
            a, b = rng.sample(range(len(routes)), 2)
            routes[a], routes[b] = routes[b], routes[a]
    elif operator == 'cutoff' and routes:
        r = rng.choice(routes)
        boundary = rng.choice(p.batches).cutoff
        target = rng.choice([p.cycle_start, max(p.cycle_start, boundary-1), boundary, boundary+1])
        r[rng.choice(['depart_after', 'return_after'])] = min(target, p.cycle_end)
    elif operator == 'e1_delivery':
        b = rng.choice(p.batches)
        rows = state['deliveries'][b.id]
        nonempty = [r for r in rows if r]
        if nonempty:
            source = rng.choice(nonempty)
            site = source.pop(rng.randrange(len(source)))
            destination = rng.choice(rows)
            destination.insert(rng.randrange(len(destination)+1), site)
    elif operator == 'e1_pickup':
        b = rng.choice(p.batches)
        rng.shuffle(state['pickup_order'][b.id])
        if problem.satellites:
            s = rng.choice(problem.satellites)
            state['pickup_vehicle'][b.id][s] = rng.randrange(f.e1_count)
    elif operator == 'reverse' and routes:
        r = rng.choice(routes)
        if len(r['tasks']) >= 2:
            a, b = sorted(rng.sample(range(len(r['tasks'])), 2))
            r['tasks'][a:b+1] = reversed(r['tasks'][a:b+1])
    return state


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42, initial_result=None,
               seed_state=None, _travel_bounds=None, _assignment_search=None, _begun=None):
    if len(problem.operating_policy.batches) != 1:
        raise ValueError('Single-batch search requires B=1')
    if not math.isfinite(max_time) or max_time <= 0 or max_iterations < 0:
        raise ValueError('Invalid ALNS time/iteration budget')
    begun = time.perf_counter() if _begun is None else _begun
    travel_bounds = _travel_bounds or travel_lower_bounds(problem)
    precheck = check_necessary_conditions(problem, travel_bounds)
    if not precheck['passed'] and _assignment_search is None:
        return rejected_result(problem, 'alns', precheck, time.perf_counter()-begun)
    rng = random.Random(seed)
    cached = EvaluationCache(problem, evaluate)
    if _assignment_search is not None:
        from models.routing.common.cycle.assignment import input_assignment
        _assignment_search.travel_bounds = travel_bounds
        problem, cached = _assignment_search.context(input_assignment(problem))
        _assignment_search.active_problem = problem
    current = initial_state(problem, rng)
    score, decoded, issues = cached(current)
    initial_strategy = 'packed'
    if issues and time.perf_counter()-begun < max_time:
        alternative = chronological_state(problem, rng)
        alt_score, alt_result, alt_issues = cached(alternative)
        if not alt_issues or alt_score < score:
            current, score, decoded, issues = alternative, alt_score, alt_result, alt_issues
            initial_strategy = 'chronological_paired_trips'
    if issues and time.perf_counter()-begun < max_time:
        alternative = appointment_state(problem, rng)
        alt_score, alt_result, alt_issues = cached(alternative)
        if not alt_issues or alt_score < score:
            current, score, decoded, issues = alternative, alt_score, alt_result, alt_issues
            initial_strategy = 'capacity_grouped_appointments'
    best, best_cost, best_problem = None, math.inf, problem
    best_state, best_decoded, best_cache = None, None, cached
    if seed_state is not None:
        alt_score, alt_result, alt_issues = cached(seed_state)
        if alt_score < score:
            current, score, decoded, issues = seed_state, alt_score, alt_result, alt_issues
            initial_strategy = 'repaired_assignment_routes'
    if initial_result is not None and audit_solution(problem, initial_result)['valid']:
        best, best_cost = deepcopy(initial_result), initial_result['info']['objective']
        alternative = state_from_result(problem, initial_result)
        alt_score, alt_result, alt_issues = cached(alternative)
        if not alt_issues:
            best_state, best_decoded = alternative, alt_result
        if alt_score < score:
            current, score, decoded, issues = alternative, alt_score, alt_result, alt_issues
            initial_strategy = 'audited_warm_start'
    if not issues:
        audited = audit_solution(problem, decoded)
        if audited['valid'] and decoded['info']['objective'] < best_cost:
            best, best_cost = decoded, decoded['info']['objective']
            best_state, best_decoded = current, decoded
    first_feasible = time.perf_counter()-begun if best is not None else None
    last_improvement = first_feasible
    initial_objective = best_cost if best is not None else None
    history = ([dict(time=first_feasible, iteration=0, objective=best_cost)]
               if best is not None else [])
    operators = ['relocate', 'split_merge', 'vehicle', 'e1_delivery', 'reverse', 'destroy_random', 'destroy_related']
    if problem.reusable:
        operators += ['pair', 'e1_pickup']
    weights = [1.0]*len(operators)
    attempts = {op: 0 for op in operators}
    improved = {op: 0 for op in operators}
    noops = {op: 0 for op in operators}
    if _assignment_search is not None:
        attempts['assignment'] = improved['assignment'] = noops['assignment'] = 0
    attempts['descent'] = improved['descent'] = noops['descent'] = 0
    descent = CycleDescent(best_problem)
    descent_seconds = 0.0
    descent_generated = descent_filtered = 0
    seen_issues, iterations, audit_rejections = set(issues), 0, 0
    for iteration in range(max_iterations if problem.orders else 0):
        if time.perf_counter()-begun >= max_time:
            break
        iterations = iteration+1
        assignment_move = (_assignment_search is not None and
                           (iteration % 32 == 31 or (iteration == 0 and issues)))
        descent_move = False
        proposed = None
        proposed_problem, proposed_cache = problem, cached
        # Spend an initial bounded share polishing the incumbent, then revisit
        # it regularly while the ordinary ALNS trajectory keeps exploring.
        anchor = (time.perf_counter()-begun < max_time*.35 and
                  iteration < max_iterations*.35)
        if best_state is not None and not assignment_move and (anchor or iteration % 4 == 0):
            if descent.problem is not best_problem:
                descent_generated += descent.generated
                descent_filtered += descent.filtered
                descent = CycleDescent(best_problem)
            descent_start = time.perf_counter()
            proposed = descent.propose(best_state, best_decoded, begun+max_time)
            descent_seconds += time.perf_counter()-descent_start
            if proposed is not None:
                descent_move = True
                proposed_problem, proposed_cache = best_problem, best_cache
        index = (None if assignment_move or descent_move else
                 rng.choices(range(len(operators)), weights=weights, k=1)[0])
        op = 'assignment' if assignment_move else ('descent' if descent_move else operators[index])
        attempts[op] += 1
        if assignment_move:
            proposal = _assignment_search.propose(problem, current,
                min(begun+max_time, time.perf_counter()+max_time*.02))
            if proposal is None:
                noops[op] += 1
                continue
            proposed_problem, proposed_cache, proposed = proposal
        elif descent_move:
            pass
        elif iteration and iteration % 150 == 0:
            proposed = initial_state(problem, rng, randomized=True)
        elif op.startswith('destroy_'):
            proposed = destroy_repair(problem, current, rng, op.split('_')[1], begun+max_time)
        else:
            proposed = _move(problem, current, rng, op)
        if proposed_problem is problem and state_key(proposed) == state_key(current):
            noops[op] += 1
            if index is not None:
                weights[index] = .9*weights[index]+.01
            continue
        new_score, candidate, problems = proposed_cache(proposed)
        seen_issues.update(problems)
        reward = .5
        if not problems and candidate['info']['objective'] < best_cost-1e-9:
            audited = audit_solution(proposed_problem, candidate)
            if audited['valid']:
                best, best_cost, best_problem = candidate, candidate['info']['objective'], proposed_problem
                best_state, best_decoded, best_cache = proposed, candidate, proposed_cache
                last_improvement = time.perf_counter()-begun
                if first_feasible is None:
                    first_feasible = last_improvement
                history.append(dict(time=last_improvement, iteration=iterations, objective=best_cost))
                reward = 8
                improved[op] += 1
                if assignment_move:
                    _assignment_search.improvements += 1
            else:
                audit_rejections += 1
                new_score = 1e12
        progress = min(1.0, max((iteration+1)/max(1, max_iterations),
                                (time.perf_counter()-begun)/max_time))
        temperature = max(.01, (decoded['info'].get('objective') or 100)*.05*(1-progress)**2)
        accept = (reward == 8 if descent_move else
                  new_score <= score or rng.random() < math.exp(min(0, (score-new_score)/temperature)))
        if accept:
            current, score, decoded = proposed, new_score, candidate
            problem, cached = proposed_problem, proposed_cache
            if assignment_move:
                _assignment_search.accepted += 1
                _assignment_search.active_problem = problem
            reward = max(reward, 2)
        if index is not None:
            weights[index] = .9*weights[index]+.1*reward
    result = best if best is not None else empty_result(problem, 'alns')
    result['precheck'] = precheck
    result['info'].update(status='FEASIBLE' if best is not None else 'HEURISTIC_NO_SOLUTION',
                          proven_optimal=False, proven_infeasible=False, proof_scope=None,
                          best_bound=None, mip_gap=None, runtime=time.perf_counter()-begun,
                          seed=seed, iterations=iterations, initial_strategy=initial_strategy,
                          initial_objective=initial_objective, incumbent_history=history,
                          time_to_first_feasible=first_feasible, time_to_best=last_improvement,
                          algorithm='adaptive_destroy_repair_cycle_search',
                          cooling_schedule='max_time_and_iteration_progress',
                          evaluation_cache_hits=cached.hits, evaluation_cache_misses=cached.misses,
                          descent_seconds=descent_seconds,
                          descent_candidates=descent_generated+descent.generated,
                          descent_filtered=descent_filtered+descent.filtered,
                          operator_noops=noops, operator_attempts=attempts, operator_improvements=improved,
                          encountered_violations=sorted(seen_issues), audit_rejections=audit_rejections,
                          e2_max_trips_per_vehicle=problem.operating_policy.e2_max_trips_per_vehicle)
    if best is not None:
        audit = audit_solution(best_problem, result)
        result.update(audit=audit, cutoff_snapshots=audit['cutoff_snapshots'], depot_releases=audit['depot_releases'])
    if _assignment_search is not None:
        result = _assignment_search.finish(result, best_problem)
    return result
