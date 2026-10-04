"""Dependency-free adaptive neighborhoods over all waves, routes and cutoff waits."""
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
               seed_state=None, _travel_bounds=None):
    if not math.isfinite(max_time) or max_time <= 0 or max_iterations < 0:
        raise ValueError('Invalid ALNS time/iteration budget')
    begun = time.perf_counter()
    precheck = check_necessary_conditions(problem, _travel_bounds or travel_lower_bounds(problem))
    if not precheck['passed']:
        return rejected_result(problem, 'alns', precheck, time.perf_counter()-begun)
    rng = random.Random(seed)
    cached = EvaluationCache(problem, evaluate)
    current = initial_state(problem, rng)
    score, decoded, issues = cached(current)
    initial_strategy = 'packed'
    if issues and time.perf_counter()-begun < max_time:
        alternative = chronological_state(problem, rng)
        alt_score, alt_result, alt_issues = cached(alternative)
        if (not alt_issues or alt_score < score):
            current, score, decoded, issues = alternative, alt_score, alt_result, alt_issues
            initial_strategy = 'chronological_paired_trips'
    if issues and time.perf_counter()-begun < max_time:
        alternative = appointment_state(problem, rng)
        alt_score, alt_result, alt_issues = cached(alternative)
        if not alt_issues or alt_score < score:
            current, score, decoded, issues = alternative, alt_score, alt_result, alt_issues
            initial_strategy = 'capacity_grouped_appointments'
    best, best_cost = None, math.inf
    if seed_state is not None:
        alt_score, alt_result, alt_issues = cached(seed_state)
        if alt_score < score:
            current, score, decoded, issues = seed_state, alt_score, alt_result, alt_issues
            initial_strategy = 'repaired_assignment_routes'
    if initial_result is not None and audit_solution(problem, initial_result)['valid']:
        best, best_cost = deepcopy(initial_result), initial_result['info']['objective']
        alternative = state_from_result(problem, initial_result)
        alt_score, alt_result, alt_issues = cached(alternative)
        if alt_score < score:
            current, score, decoded, issues = alternative, alt_score, alt_result, alt_issues
            initial_strategy = 'audited_warm_start'
    if not issues:
        audited = audit_solution(problem, decoded)
        if audited['valid'] and decoded['info']['objective'] < best_cost:
            best, best_cost = decoded, decoded['info']['objective']
    first_feasible = time.perf_counter()-begun if best is not None else None
    last_improvement = first_feasible
    multibatch = len(problem.operating_policy.batches) > 1
    best_state = (clone_state(current) if best is decoded else
                  state_from_result(problem, best)) if multibatch and best is not None else None
    initial_objective = best_cost if best is not None else None
    history = ([dict(time=first_feasible, iteration=0, objective=best_cost)]
               if best is not None else [])
    descent = None
    if multibatch:
        from .intensification import CycleDescent
        descent = CycleDescent(problem)
    operators = ['relocate', 'split_merge', 'vehicle', 'e1_delivery', 'reverse', 'destroy_random', 'destroy_related']
    if problem.reusable:
        operators += ['pair', 'e1_pickup']
    if len(problem.operating_policy.batches) > 1:
        operators.append('cutoff')
    weights = [1.0]*len(operators)
    attempts = {op: 0 for op in operators}
    improved = {op: 0 for op in operators}
    noops = {op: 0 for op in operators}
    if multibatch:
        for counts in (attempts, improved, noops):
            counts['cycle_descent'] = 0
    restarts = 0
    seen_issues, iterations, audit_rejections = set(issues), 0, 0
    for iteration in range(max_iterations if problem.orders else 0):
        if time.perf_counter()-begun >= max_time:
            break
        iterations = iteration+1
        proposed = None
        index = None
        if descent is not None and best_state is not None and iteration % 3 != 2:
            proposed = descent.propose(best_state, best, begun+max_time)
        if proposed is not None:
            op = 'cycle_descent'
        else:
            if time.perf_counter()-begun >= max_time:
                break
            index = rng.choices(range(len(operators)), weights=weights, k=1)[0]
            op = operators[index]
        attempts[op] += 1
        if proposed is not None:
            pass
        elif multibatch and best_state is not None and iteration and iteration % 150 == 0:
            current = clone_state(best_state)
            score, decoded, _ = cached(current)
            restarts += 1
            proposed = destroy_repair(problem, current, rng, 'related', begun+max_time)
        elif not multibatch and iteration and iteration % 150 == 0:
            proposed = initial_state(problem, rng, randomized=True)
        elif op.startswith('destroy_'):
            proposed = destroy_repair(problem, current, rng, op.split('_')[1], begun+max_time)
        else:
            proposed = _move(problem, current, rng, op)
        if state_key(proposed) == state_key(current):
            noops[op] += 1
            if index is not None:
                weights[index] = .9*weights[index]+.01
            continue
        new_score, candidate, problems = cached(proposed)
        seen_issues.update(problems)
        reward = .5
        if not problems and candidate['info']['objective'] < best_cost-1e-9:
            audited = audit_solution(problem, candidate)
            if audited['valid']:
                best, best_cost = candidate, candidate['info']['objective']
                if multibatch:
                    best_state = clone_state(proposed)
                last_improvement = time.perf_counter()-begun
                if first_feasible is None:
                    first_feasible = last_improvement
                reward = 8
                improved[op] += 1
                if multibatch:
                    history.append(dict(time=last_improvement, iteration=iterations, objective=best_cost))
            else:
                audit_rejections += 1
                new_score = 1e12
        # Expensive repair moves may hit the wall-clock budget long before the
        # iteration cap. Cool against whichever budget is being consumed faster.
        progress = min(1.0, max((iteration+1)/max(1, max_iterations),
                                (time.perf_counter()-begun)/max_time))
        temperature = max(.01, (decoded['info'].get('objective') or 100)*(.01 if multibatch else .05)*(1-progress)**2)
        accept = new_score <= score or rng.random() < math.exp(min(0, (score-new_score)/temperature))
        if accept:
            current, score, decoded = proposed, new_score, candidate
            reward = max(reward, 2)
        if index is not None:
            weights[index] = .9*weights[index]+.1*reward
    result = best if best is not None else empty_result(problem, 'alns')
    result['precheck'] = precheck
    result['info']['proven_infeasible'] = False
    result['info'].update(status='FEASIBLE' if best is not None else 'HEURISTIC_NO_SOLUTION',
                          proven_optimal=False, proof_scope=None, best_bound=None, mip_gap=None,
                          runtime=time.perf_counter()-begun, seed=seed, iterations=iterations,
                          initial_strategy=initial_strategy,
                          time_to_first_feasible=first_feasible, time_to_best=last_improvement,
                          algorithm='adaptive_destroy_repair_cycle_search', cooling_schedule='max_time_and_iteration_progress',
                          evaluation_cache_hits=cached.hits, evaluation_cache_misses=cached.misses,
                          operator_noops=noops,
                          operator_attempts=attempts, operator_improvements=improved,
                          encountered_violations=sorted(seen_issues), audit_rejections=audit_rejections,
                          e2_max_trips_per_vehicle=problem.operating_policy.e2_max_trips_per_vehicle)
    if multibatch:
        result['info'].update(algorithm='adaptive_cycle_search_with_distance_descent',
            initial_objective=initial_objective, incumbent_history=history,
            incumbent_restarts=restarts, descent_candidates=descent.generated,
            descent_filtered=descent.filtered, descent_exhausted=descent.exhausted)
    if best is not None:
        audit = audit_solution(problem, result)
        result.update(audit=audit, cutoff_snapshots=audit['cutoff_snapshots'], depot_releases=audit['depot_releases'])
    return result
