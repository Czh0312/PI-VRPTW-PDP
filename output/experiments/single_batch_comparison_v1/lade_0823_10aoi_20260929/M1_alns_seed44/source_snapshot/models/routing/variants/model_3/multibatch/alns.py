"""Dependency-free adaptive neighborhoods over all waves, routes and cutoff waits."""
from __future__ import annotations

from copy import deepcopy
import math
import random
import time

from .state import empty_result
from .evaluator import initial_state, evaluate
from .audit import audit_solution
from .precheck import check_necessary_conditions, rejected_result
from .construction import chronological_state


def _move(problem, original, rng, operator):
    state = deepcopy(original)
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
    return state


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42):
    if not math.isfinite(max_time) or max_time <= 0 or max_iterations < 0:
        raise ValueError('Invalid ALNS time/iteration budget')
    begun = time.perf_counter()
    precheck = check_necessary_conditions(problem)
    if not precheck['passed']:
        return rejected_result(problem, 'alns', precheck, time.perf_counter()-begun)
    rng = random.Random(seed)
    current = initial_state(problem, rng)
    score, decoded, issues = evaluate(problem, current)
    initial_strategy = 'packed'
    if issues and time.perf_counter()-begun < max_time:
        alternative = chronological_state(problem, rng)
        alt_score, alt_result, alt_issues = evaluate(problem, alternative)
        if (not alt_issues or alt_score < score):
            current, score, decoded, issues = alternative, alt_score, alt_result, alt_issues
            initial_strategy = 'chronological_paired_trips'
    best, best_cost = None, math.inf
    if not issues:
        audited = audit_solution(problem, decoded)
        if audited['valid']:
            best, best_cost = decoded, decoded['info']['objective']
    operators = ['relocate', 'pair', 'split_merge', 'vehicle', 'cutoff', 'e1_delivery', 'e1_pickup']
    weights = [1.0]*len(operators)
    attempts = {op: 0 for op in operators}
    improved = {op: 0 for op in operators}
    seen_issues, iterations, audit_rejections = set(issues), 0, 0
    for iteration in range(max_iterations):
        if time.perf_counter()-begun >= max_time:
            break
        iterations = iteration+1
        index = rng.choices(range(len(operators)), weights=weights, k=1)[0]
        op = operators[index]
        attempts[op] += 1
        if iteration and iteration % 150 == 0:
            proposed = initial_state(problem, rng, randomized=True)
        else:
            proposed = _move(problem, current, rng, op)
        new_score, candidate, problems = evaluate(problem, proposed)
        seen_issues.update(problems)
        reward = .5
        if not problems and candidate['info']['objective'] < best_cost-1e-9:
            audited = audit_solution(problem, candidate)
            if audited['valid']:
                best, best_cost = candidate, candidate['info']['objective']
                reward = 8
                improved[op] += 1
            else:
                audit_rejections += 1
                new_score = 1e12
        progress = min(1, (time.perf_counter()-begun)/max_time)
        temperature = max(.01, (decoded['info'].get('objective') or 100)*.05*(1-progress)**2)
        accept = new_score <= score or rng.random() < math.exp(min(0, (score-new_score)/temperature))
        if accept:
            current, score, decoded = proposed, new_score, candidate
            reward = max(reward, 2)
        weights[index] = .9*weights[index]+.1*reward
    result = best if best is not None else empty_result(problem, 'alns')
    result['precheck'] = precheck
    result['info']['proven_infeasible'] = False
    result['info'].update(status='FEASIBLE' if best is not None else 'HEURISTIC_NO_SOLUTION',
                          proven_optimal=False, proof_scope=None, best_bound=None, mip_gap=None,
                          runtime=time.perf_counter()-begun, seed=seed, iterations=iterations,
                          initial_strategy=initial_strategy,
                          operator_attempts=attempts, operator_improvements=improved,
                          encountered_violations=sorted(seen_issues), audit_rejections=audit_rejections,
                          e2_max_trips_per_vehicle=problem.operating_policy.e2_max_trips_per_vehicle)
    if best is not None:
        audit = audit_solution(problem, result)
        result.update(audit=audit, cutoff_snapshots=audit['cutoff_snapshots'], depot_releases=audit['depot_releases'])
    return result
