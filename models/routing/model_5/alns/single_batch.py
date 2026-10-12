"""Single-batch route search with optional joint customer-assignment moves."""
from __future__ import annotations

from models.routing.model_5.alns.parameters import ALNSParameters
from models.routing.model_5.alns.search_control import SearchControl

from models.routing.model_5.alns.neighborhoods import (
    move_route as _move,
    clone_state,
    destroy_repair,
    state_key,
    state_from_result,
)

from copy import deepcopy
import math
import random
import time

from models.common.cycle.state import empty_result
from models.routing.model_5.alns.evaluator import initial_state, evaluate
from models.common.cycle.audit import audit_solution
from models.common.cycle.precheck import (
    check_necessary_conditions,
    rejected_result,
    travel_lower_bounds,
)
from models.routing.model_5.alns.construction import chronological_state, appointment_state
from models.routing.model_5.alns.single_batch_cache import SingleBatchCache
from models.routing.model_5.alns.single_batch_descent import SingleBatchDescent


def solve_alns(
    problem,
    max_time=10,
    max_iterations=5000,
    seed=42,
    initial_result=None,
    seed_state=None,
    _travel_bounds=None,
    _assignment_search=None,
    _begun=None,
    _context=None,
    _late_pickup_greedy=True,
):
    if len(problem.operating_policy.batches) != 1:
        raise ValueError("Single-batch search requires B=1")
    if not math.isfinite(max_time) or max_time <= 0 or max_iterations < 0:
        raise ValueError("Invalid ALNS time/iteration budget")
    begun = time.perf_counter() if _begun is None else _begun
    parameters = ALNSParameters(max_time, max_iterations, seed)
    control = SearchControl(parameters, begun, time.perf_counter)
    travel_bounds = _travel_bounds or travel_lower_bounds(problem)
    precheck = check_necessary_conditions(problem, travel_bounds)
    if not precheck["passed"] and _assignment_search is None:
        return rejected_result(problem, "alns", precheck, time.perf_counter() - begun)
    rng = random.Random(seed)
    if _context is not None and _context.problem is not problem:
        raise ValueError("Single-batch context belongs to one problem instance")
    cached = _context if _context is not None else SingleBatchCache(problem)
    if _assignment_search is not None:
        from models.common.cycle.assignment import input_assignment

        _assignment_search.travel_bounds = travel_bounds
        problem, cached = _assignment_search.context(input_assignment(problem))
        _assignment_search.active_problem = problem
    current = initial_state(problem, rng)
    score, decoded, issues = cached(current)
    initial_strategy = "packed"
    if issues and time.perf_counter() - begun < max_time:
        alternative = chronological_state(problem, rng)
        alt_score, alt_result, alt_issues = cached(alternative)
        if not alt_issues or alt_score < score:
            current, score, decoded, issues = alternative, alt_score, alt_result, alt_issues
            initial_strategy = "chronological_paired_trips"
    if issues and time.perf_counter() - begun < max_time:
        alternative = appointment_state(problem, rng)
        alt_score, alt_result, alt_issues = cached(alternative)
        if not alt_issues or alt_score < score:
            current, score, decoded, issues = alternative, alt_score, alt_result, alt_issues
            initial_strategy = "capacity_grouped_appointments"
    best, best_cost, best_problem = None, math.inf, problem
    best_state, best_decoded, best_cache = None, None, cached
    if seed_state is not None:
        alt_score, alt_result, alt_issues = cached(seed_state)
        if alt_score < score:
            current, score, decoded, issues = seed_state, alt_score, alt_result, alt_issues
            initial_strategy = "repaired_assignment_routes"
    if initial_result is not None and audit_solution(problem, initial_result)["valid"]:
        best, best_cost = deepcopy(initial_result), initial_result["info"]["objective"]
        alternative = state_from_result(problem, initial_result)
        alt_score, alt_result, alt_issues = cached(alternative)
        if not alt_issues:
            best_state, best_decoded = alternative, alt_result
        if alt_score < score:
            current, score, decoded, issues = alternative, alt_score, alt_result, alt_issues
            initial_strategy = "audited_warm_start"
    if not issues:
        audited = audit_solution(problem, decoded)
        if audited["valid"] and decoded["info"]["objective"] < best_cost:
            best, best_cost = decoded, decoded["info"]["objective"]
            best_state, best_decoded = current, decoded
    first_feasible = time.perf_counter() - begun if best is not None else None
    last_improvement = first_feasible
    initial_objective = best_cost if best is not None else None
    history = (
        [dict(time=first_feasible, iteration=0, objective=best_cost)] if best is not None else []
    )
    operators = [
        "relocate",
        "split_merge",
        "vehicle",
        "e1_delivery",
        "reverse",
        "destroy_random",
        "destroy_related",
    ]
    if problem.reusable:
        operators += ["pair", "e1_pickup"]
    weights = control.initial_weights(operators)
    attempts = {op: 0 for op in operators}
    improved = {op: 0 for op in operators}
    noops = {op: 0 for op in operators}
    if _assignment_search is not None:
        attempts["assignment"] = improved["assignment"] = noops["assignment"] = 0
    attempts["descent"] = improved["descent"] = noops["descent"] = 0
    late_pickup_enabled = bool(
        _late_pickup_greedy and problem.reusable and _assignment_search is None
    )
    late_pickup_checks = late_pickup_uphill = late_pickup_vetoes = 0
    # Free delivery/pickup assignment retains its original continuous search:
    # incumbent descent competed with partition exploration in paired pilots.
    descent_enabled = _assignment_search is None
    descent = SingleBatchDescent(
        best_problem,
        cache_sites=not best_problem.reusable,
        score_route=best_cache.score_route,
        cache_distances=best_problem.reusable,
    )
    descent_seconds = 0.0
    descent_generated = descent_filtered = descent_cache_hits = descent_enumerations = 0
    descent_deferred = descent_recovered = 0
    seen_issues, iterations, audit_rejections = set(issues), 0, 0
    for iteration in range(max_iterations if problem.orders else 0):
        if time.perf_counter() - begun >= max_time:
            break
        iterations = iteration + 1
        assignment_move = _assignment_search is not None and (
            iteration % 32 == 31 or (iteration == 0 and issues)
        )
        descent_move = False
        proposed = None
        proposed_problem, proposed_cache = problem, cached
        # Preserve the exploratory trajectory until the final budget share,
        # then polish its audited incumbent. Either budget may end the search.
        polishing = (
            time.perf_counter() - begun >= max_time * 0.8 or iteration >= max_iterations * 0.8
        )
        if descent_enabled and best_state is not None and not assignment_move and polishing:
            if descent.problem is not best_problem:
                descent_generated += descent.generated
                descent_filtered += descent.filtered
                descent_cache_hits += descent.site_cache_hits
                descent_enumerations += descent.enumerations
                descent_deferred += descent.deferred_count
                descent_recovered += descent.recovered
                descent = SingleBatchDescent(
                    best_problem,
                    cache_sites=not best_problem.reusable,
                    score_route=best_cache.score_route,
                    cache_distances=best_problem.reusable,
                )
            descent_start = time.perf_counter()
            proposed = descent.propose(best_state, best_decoded, begun + max_time)
            descent_seconds += time.perf_counter() - descent_start
            if proposed is not None:
                descent_move = True
                proposed_problem, proposed_cache = best_problem, best_cache
        index = None if assignment_move or descent_move else control.select_index(rng, weights)
        op = "assignment" if assignment_move else ("descent" if descent_move else operators[index])
        attempts[op] += 1
        if assignment_move:
            proposal = _assignment_search.propose(
                problem, current, min(begun + max_time, time.perf_counter() + max_time * 0.02)
            )
            if proposal is None:
                noops[op] += 1
                continue
            proposed_problem, proposed_cache, proposed = proposal
        elif descent_move:
            pass
        elif iteration and iteration % 150 == 0:
            proposed = initial_state(problem, rng, randomized=True)
        elif op.startswith("destroy_"):
            proposed = destroy_repair(
                problem,
                current,
                rng,
                op.split("_")[1],
                begun + max_time,
                scorer=cached.route_scorer,
                cache_distances=problem.reusable and _assignment_search is None,
                cache_pickup_prefix=problem.reusable and _assignment_search is None,
            )
        else:
            proposed = _move(problem, current, rng, op)
        if proposed_problem is problem and state_key(proposed) == state_key(current):
            noops[op] += 1
            if index is not None:
                control.penalize_noop(weights, index)
            continue
        new_score, candidate, problems = proposed_cache(proposed)
        seen_issues.update(problems)
        reward = 0.5
        if not problems and candidate["info"]["objective"] < best_cost - 1e-9:
            audited = audit_solution(proposed_problem, candidate)
            if audited["valid"]:
                best, best_cost, best_problem = (
                    candidate,
                    candidate["info"]["objective"],
                    proposed_problem,
                )
                best_state, best_decoded, best_cache = proposed, candidate, proposed_cache
                last_improvement = time.perf_counter() - begun
                if first_feasible is None:
                    first_feasible = last_improvement
                history.append(
                    dict(time=last_improvement, iteration=iterations, objective=best_cost)
                )
                reward = 8
                improved[op] += 1
                if assignment_move:
                    _assignment_search.improvements += 1
            else:
                audit_rejections += 1
                new_score = 1e12
        progress = control.progress(iteration)
        temperature = control.route_temperature(decoded["info"].get("objective") or 100, progress)
        accept = reward == 8 if descent_move else control.accept(rng, score, new_score, temperature)
        # Keep the original random draw, but stop uphill pickup-only moves
        # in the latter half. Other operators retain their annealing policy.
        if late_pickup_enabled and op == "e1_pickup" and progress >= 0.5:
            late_pickup_checks += 1
            if new_score > score:
                late_pickup_uphill += 1
                late_pickup_vetoes += int(accept)
            accept = new_score <= score
        if accept:
            current, score, decoded, issues = proposed, new_score, candidate, problems
            problem, cached = proposed_problem, proposed_cache
            if assignment_move:
                _assignment_search.accepted += 1
            if _assignment_search is not None:
                _assignment_search.active_problem = problem
            reward = max(reward, 2)
        if index is not None:
            control.reward_online(weights, index, reward)
    result = best if best is not None else empty_result(problem, "alns")
    result["precheck"] = precheck
    result["info"].update(
        status="FEASIBLE" if best is not None else "HEURISTIC_NO_SOLUTION",
        proven_optimal=False,
        proven_infeasible=False,
        proof_scope=None,
        best_bound=None,
        mip_gap=None,
        runtime=time.perf_counter() - begun,
        seed=seed,
        iterations=iterations,
        initial_strategy=initial_strategy,
        initial_objective=initial_objective,
        incumbent_history=history,
        time_to_first_feasible=first_feasible,
        time_to_best=last_improvement,
        algorithm="adaptive_destroy_repair_cycle_search",
        cooling_schedule="max_time_and_iteration_progress",
        late_pickup_policy="greedy_last_half" if late_pickup_enabled else "original",
        late_pickup_checks=late_pickup_checks,
        late_pickup_uphill=late_pickup_uphill,
        late_pickup_vetoes=late_pickup_vetoes,
        evaluation_cache_hits=cached.hits,
        evaluation_cache_misses=cached.misses,
        descent_seconds=descent_seconds,
        descent_enabled=descent_enabled,
        descent_budget_policy="last_20pct_of_either_budget" if descent_enabled else "disabled",
        descent_candidates=descent_generated + descent.generated,
        descent_filtered=descent_filtered + descent.filtered,
        descent_site_cache_hits=descent_cache_hits + descent.site_cache_hits,
        descent_site_enumerations=descent_enumerations + descent.enumerations,
        descent_deferred=descent_deferred + descent.deferred_count,
        descent_recovered=descent_recovered + descent.recovered,
        operator_statistics_scope="complete_single_batch_solve",
        operator_noops=noops,
        operator_attempts=attempts,
        operator_improvements=improved,
        encountered_violations=sorted(seen_issues),
        audit_rejections=audit_rejections,
        e2_max_trips_per_vehicle=problem.operating_policy.e2_max_trips_per_vehicle,
    )
    if best is not None:
        audit = audit_solution(best_problem, result)
        result.update(
            audit=audit,
            cutoff_snapshots=audit["cutoff_snapshots"],
            depot_releases=audit["depot_releases"],
        )
    if _assignment_search is not None:
        result = _assignment_search.finish(result, best_problem)
    return result
