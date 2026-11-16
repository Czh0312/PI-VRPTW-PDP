"""Shared M1–M4 search policy with model-specific construction and evaluation."""
from copy import deepcopy
import math
import random
import time
from typing import NamedTuple

from models.common.cycle.audit import audit_solution
from models.common.cycle.precheck import (
    check_necessary_conditions,
    rejected_result,
    travel_lower_bounds,
)
from models.common.cycle.state import empty_result

from .engine import Evaluation, OperatorWeights, Proposal, run_search
from .search_control import ALNSParameters, SearchControl


class RouteContext(NamedTuple):
    problem: object
    cache: object
    routes: dict
    issues: list


def solve_single_batch(
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
    *,
    initial_state,
    chronological_state,
    appointment_state,
    move_route,
    destroy_repair,
    state_key,
    state_from_result,
    cache_factory,
    descent_factory,
):
    if len(problem.operating_policy.batches) != 1:
        raise ValueError("Single-batch search requires B=1")
    if not math.isfinite(max_time) or max_time <= 0 or max_iterations < 0:
        raise ValueError("Invalid ALNS time/iteration budget")
    begun = time.perf_counter() if _begun is None else _begun
    control = SearchControl(ALNSParameters(max_time, max_iterations, seed), begun, time.perf_counter)
    travel_bounds = _travel_bounds or travel_lower_bounds(problem)
    precheck = check_necessary_conditions(problem, travel_bounds)
    if not precheck["passed"] and _assignment_search is None:
        return rejected_result(problem, "alns", precheck, time.perf_counter() - begun)
    rng = random.Random(seed)
    if _context is not None and _context.problem is not problem:
        raise ValueError("Single-batch context belongs to one problem instance")
    cached = _context if _context is not None else cache_factory(problem)
    if _assignment_search is not None:
        from models.common.cycle.assignment import input_assignment

        _assignment_search.travel_bounds = travel_bounds
        problem, cached = _assignment_search.context(input_assignment(problem))
        _assignment_search.active_problem = problem
    current = initial_state(problem, rng)
    score, decoded, issues = cached(current)
    initial_strategy = "packed"
    for strategy, construct in (
        ("chronological_paired_trips", chronological_state),
        ("capacity_grouped_appointments", appointment_state),
    ):
        if not issues or time.perf_counter() - begun >= max_time:
            break
        alternative = construct(problem, rng)
        alt_score, alt_result, alt_issues = cached(alternative)
        if not alt_issues or alt_score < score:
            current, score, decoded, issues = alternative, alt_score, alt_result, alt_issues
            initial_strategy = strategy
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
    history = [dict(time=first_feasible, iteration=0, objective=best_cost)] if best is not None else []

    operators = [
        "relocate", "split_merge", "vehicle", "e1_delivery", "reverse",
        "destroy_random", "destroy_related",
    ]
    if problem.reusable:
        operators += ["pair", "e1_pickup"]
    unweighted = ("assignment", "descent") if _assignment_search is not None else ("descent",)
    weights = OperatorWeights(
        control, operators, unweighted=unweighted,
        rejected_reward=0.5, accepted_reward=2, downhill_reward=2,
    )
    late_pickup_enabled = bool(_late_pickup_greedy and problem.reusable and _assignment_search is None)
    late_pickup_checks = late_pickup_uphill = late_pickup_vetoes = 0
    # Free assignment keeps its budget for partition exploration.
    descent_enabled = _assignment_search is None
    descent = descent_factory(
        best_problem,
        cache_sites=not best_problem.reusable,
        score_route=best_cache.score_route,
        cache_distances=best_problem.reusable,
    )
    descent_seconds = 0.0
    seen_issues, audit_rejections = set(issues), 0
    deadline = begun + max_time

    def propose(trial, iteration):
        nonlocal descent_seconds
        active_problem, cache, routes, violations = trial.state
        assignment_move = _assignment_search is not None and (
            iteration % 32 == 31 or (iteration == 0 and violations)
        )
        proposed = None
        proposed_problem, proposed_cache = active_problem, cache
        # Either budget can put the search in its final polishing phase.
        polishing = (
            time.perf_counter() - begun >= max_time * 0.8 or iteration >= max_iterations * 0.8
        )
        if descent_enabled and best_state is not None and polishing:
            descent_start = time.perf_counter()
            proposed = descent.propose(best_state, best_decoded, deadline)
            descent_seconds += time.perf_counter() - descent_start
            if proposed is not None:
                proposed_problem, proposed_cache = best_problem, best_cache
        if assignment_move:
            operator = "assignment"
            assignment = _assignment_search.propose(
                active_problem, routes, min(deadline, time.perf_counter() + max_time * 0.02)
            )
            if assignment is None:
                return Proposal(trial, operators=(operator,))
            proposed_problem, proposed_cache, proposed = assignment
        elif proposed is not None:
            operator = "descent"
        else:
            operator = weights.choose(rng)
            if iteration and iteration % 150 == 0:
                proposed = initial_state(active_problem, rng, randomized=True)
            elif operator.startswith("destroy_"):
                fixed_recovery = active_problem.reusable and _assignment_search is None
                proposed = destroy_repair(
                    active_problem, routes, rng, operator.split("_")[1], deadline,
                    scorer=cache.route_scorer, cache_distances=fixed_recovery,
                    cache_pickup_prefix=fixed_recovery,
                )
            else:
                proposed = move_route(active_problem, routes, rng, operator)
        if proposed_problem is active_problem and state_key(proposed) == state_key(routes):
            return Proposal(trial, operators=(operator,))
        new_score, candidate, problems = proposed_cache(proposed)
        seen_issues.update(problems)
        return Proposal(trial, Evaluation(
            new_score, RouteContext(proposed_problem, proposed_cache, proposed, problems), candidate
        ), (operator,))

    def remember(proposal, iterations):
        nonlocal best, best_cost, best_problem, best_state, best_decoded, best_cache
        nonlocal first_feasible, last_improvement, audit_rejections
        evaluation = proposal.candidate
        proposed_problem, proposed_cache, proposed, problems = evaluation.state
        candidate = evaluation.result
        if problems or not candidate["info"]["objective"] < best_cost - 1e-9:
            return False
        if not audit_solution(proposed_problem, candidate)["valid"]:
            audit_rejections += 1
            evaluation.score = 1e12
            return False
        best, best_cost, best_problem = candidate, candidate["info"]["objective"], proposed_problem
        best_state, best_decoded, best_cache = proposed, candidate, proposed_cache
        last_improvement = time.perf_counter() - begun
        if first_feasible is None:
            first_feasible = last_improvement
        history.append(dict(time=last_improvement, iteration=iterations, objective=best_cost))
        if proposal.operators[0] == "assignment":
            _assignment_search.improvements += 1
        return True

    def accept(proposal, iteration, improved):
        nonlocal late_pickup_checks, late_pickup_uphill, late_pickup_vetoes
        operator = proposal.operators[0]
        trial, evaluation = proposal.current, proposal.candidate
        progress = control.progress(iteration)
        temperature = control.route_temperature(trial.result["info"].get("objective") or 100, progress)
        accepted = improved if operator == "descent" else control.accept(
            rng, trial.score, evaluation.score, temperature
        )
        # Preserve the annealing draw before applying the late pickup restriction.
        if late_pickup_enabled and operator == "e1_pickup" and progress >= 0.5:
            late_pickup_checks += 1
            if evaluation.score > trial.score:
                late_pickup_uphill += 1
                late_pickup_vetoes += int(accepted)
            accepted = evaluation.score <= trial.score
        if accepted and _assignment_search is not None:
            if operator == "assignment":
                _assignment_search.accepted += 1
            _assignment_search.active_problem = evaluation.state.problem
        return accepted

    start = Evaluation(score, RouteContext(problem, cached, current, issues), decoded)
    current, iterations, _ = run_search(
        start, control, weights, propose, remember, accept,
        max_iterations=max_iterations if problem.orders else 0,
    )
    problem, cached, _, _ = current.state
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
        descent_candidates=descent.generated,
        descent_filtered=descent.filtered,
        descent_site_cache_hits=descent.site_cache_hits,
        descent_site_enumerations=descent.enumerations,
        descent_deferred=descent.deferred_count,
        descent_recovered=descent.recovered,
        operator_statistics_scope="complete_single_batch_solve",
        operator_noops=weights.noops,
        operator_attempts=weights.attempts,
        operator_improvements=weights.improvements,
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
