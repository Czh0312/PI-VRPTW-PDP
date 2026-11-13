"""Dependency-free adaptive neighborhoods over all waves, routes and cutoff waits."""
from __future__ import annotations

from models.routing.model_5.alns.parameters import ALNSParameters
from models.routing.model_5.alns.search_control import SearchControl

from models.routing.model_5.alns.neighborhoods import (
    move_multibatch_route as _move,
    EvaluationCache,
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


def solve_alns(
    problem,
    max_time=10,
    max_iterations=5000,
    seed=42,
    initial_result=None,
    seed_state=None,
    _travel_bounds=None,
    *,
    cache_descent_distances=False,
    cache_e2_schedule=False,
    critical_stock_repair=False,
    stock_chain_strategy='stock_first',
):
    if stock_chain_strategy not in ('stock_first', 'cost_aware', 'cost_aware_marginal'):
        raise ValueError('Unknown stock-chain strategy')
    if stock_chain_strategy != 'stock_first' and not critical_stock_repair:
        raise ValueError('A stock-chain strategy requires critical_stock_repair=True')
    if critical_stock_repair and (
        len(problem.operating_policy.batches) < 2 or not getattr(problem, 'optimize_pool', False)
    ):
        raise ValueError("Stock-chain repair requires multi-batch M5 cost optimization")
    if len(problem.operating_policy.batches) == 1:
        if cache_e2_schedule:
            raise ValueError("E2 schedule cache is an experimental multi-batch option")
        if cache_descent_distances:
            raise ValueError("Descent distance cache is an experimental multi-batch option")
        from models.routing.model_5.alns.single_batch_search import solve_alns as solve_single_batch

        return solve_single_batch(
            problem,
            max_time=max_time,
            max_iterations=max_iterations,
            seed=seed,
            initial_result=initial_result,
            seed_state=seed_state,
            _travel_bounds=_travel_bounds,
        )
    if not math.isfinite(max_time) or max_time <= 0 or max_iterations < 0:
        raise ValueError("Invalid ALNS time/iteration budget")
    begun = time.perf_counter()
    parameters = ALNSParameters(max_time, max_iterations, seed)
    control = SearchControl(parameters, begun, time.perf_counter)
    travel_bounds = _travel_bounds or travel_lower_bounds(problem)
    precheck = check_necessary_conditions(problem, travel_bounds)
    if not precheck["passed"]:
        return rejected_result(problem, "alns", precheck, time.perf_counter() - begun)
    rng = random.Random(seed)
    from models.routing.model_5.alns.performance.cycle_cache import CycleEvaluator

    cycle_evaluator = CycleEvaluator(problem, cache_e2_schedule=cache_e2_schedule)
    cached = EvaluationCache(problem, cycle_evaluator)
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
    best, best_cost = None, math.inf
    if seed_state is not None:
        alt_score, alt_result, alt_issues = cached(seed_state)
        if alt_score < score:
            current, score, decoded, issues = seed_state, alt_score, alt_result, alt_issues
            initial_strategy = "repaired_assignment_routes"
    if initial_result is not None and audit_solution(problem, initial_result)["valid"]:
        best, best_cost = deepcopy(initial_result), initial_result["info"]["objective"]
        alternative = state_from_result(problem, initial_result)
        alt_score, alt_result, alt_issues = cached(alternative)
        if alt_score < score:
            current, score, decoded, issues = alternative, alt_score, alt_result, alt_issues
            initial_strategy = "audited_warm_start"
    if not issues:
        audited = audit_solution(problem, decoded)
        if audited["valid"] and decoded["info"]["objective"] < best_cost:
            best, best_cost = decoded, decoded["info"]["objective"]
    first_feasible = time.perf_counter() - begun if best is not None else None
    last_improvement = first_feasible
    multibatch = len(problem.operating_policy.batches) > 1
    best_state = (
        (clone_state(current) if best is decoded else state_from_result(problem, best))
        if multibatch and best is not None
        else None
    )
    initial_objective = best_cost if best is not None else None
    history = (
        [dict(time=first_feasible, iteration=0, objective=best_cost)] if best is not None else []
    )
    descent = None
    pool_descent = None
    stock_repair = None
    stock_improvements = []
    if multibatch:
        from models.routing.model_5.alns.intensification import CycleDescent

        descent = CycleDescent(problem, cache_distances=cache_descent_distances)
        if getattr(problem, 'optimize_pool', False):
            from models.routing.model_5.alns.pool_moves import PoolReuseDescent
            pool_descent = PoolReuseDescent(problem, travel_bounds=travel_bounds)
            if critical_stock_repair:
                from models.routing.model_5.alns.stock_chain import CriticalStockRepair

                repair_class = CriticalStockRepair
                if stock_chain_strategy == 'cost_aware':
                    from models.routing.model_5.alns.cost_chain import CostAwareStockRepair

                    repair_class = CostAwareStockRepair
                elif stock_chain_strategy == 'cost_aware_marginal':
                    from models.routing.model_5.alns.marginal_pickup import MarginalPickupStockRepair

                    repair_class = MarginalPickupStockRepair
                stock_repair = repair_class(problem, lower_bound=precheck['pool_lower_bound'])
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
    if len(problem.operating_policy.batches) > 1:
        operators.append("cutoff")
    weights = control.initial_weights(operators)
    attempts = {op: 0 for op in operators}
    improved = {op: 0 for op in operators}
    noops = {op: 0 for op in operators}
    if multibatch:
        for counts in (attempts, improved, noops):
            counts["cycle_descent"] = 0
            if pool_descent is not None:
                counts['pool_reuse'] = 0
            if stock_repair is not None:
                counts['stock_chain'] = 0
    restarts = 0
    search_rng, anchor_rng = rng, random.Random(0)
    anchor_iterations = 0
    seen_issues, iterations, audit_rejections = set(issues), 0, 0
    for iteration in range(max_iterations if problem.orders else 0):
        if time.perf_counter() - begun >= max_time:
            break
        iterations = iteration + 1
        # A common, bounded construction-improvement phase gives all seeds a
        # comparable starting quality. The user's seed controls exploration
        # after that phase; neither phase receives an extra time budget.
        anchor_phase = (
            multibatch
            and iteration < min(600, max_iterations // 3)
            and time.perf_counter() - begun < 0.4 * max_time
        )
        rng = anchor_rng if anchor_phase else search_rng
        anchor_iterations += int(anchor_phase)
        proposed = None
        index = None
        pool_proposal = False
        stock_proposal = False
        if (stock_repair is not None and best_state is not None and iteration % 150 == 0
                and stock_repair.seconds < 0.15 * max_time):
            local_deadline = min(begun + max_time, time.perf_counter()
                                 + min(0.4, 0.15 * max_time - stock_repair.seconds))
            proposed = stock_repair.propose(best_state, best, cached, local_deadline)
            stock_proposal = proposed is not None
        if proposed is None and descent is not None and best_state is not None and iteration % 3 != 2:
            if pool_descent is not None and iteration % 9 == 0:
                proposed = pool_descent.propose(best_state, best, begun + max_time)
                pool_proposal = proposed is not None
            if proposed is None:
                proposed = descent.propose(best_state, best, begun + max_time)
        if proposed is not None:
            op = 'stock_chain' if stock_proposal else ('pool_reuse' if pool_proposal else "cycle_descent")
        else:
            if time.perf_counter() - begun >= max_time:
                break
            index = control.select_index(rng, weights)
            op = operators[index]
        attempts[op] += 1
        if proposed is not None:
            pass
        elif multibatch and best_state is not None and iteration and iteration % 150 == 0:
            current = clone_state(best_state)
            score, decoded, _ = cached(current)
            restarts += 1
            proposed = destroy_repair(problem, current, rng, "related", begun + max_time)
        elif not multibatch and iteration and iteration % 150 == 0:
            proposed = initial_state(problem, rng, randomized=True)
        elif op.startswith("destroy_"):
            proposed = destroy_repair(problem, current, rng, op.split("_")[1], begun + max_time)
        else:
            proposed = _move(problem, current, rng, op)
        if state_key(proposed) == state_key(current):
            noops[op] += 1
            if index is not None:
                control.penalize_noop(weights, index)
            continue
        new_score, candidate, problems = cached(proposed)
        seen_issues.update(problems)
        reward = 0.5
        if not problems and candidate["info"]["objective"] < best_cost - 1e-9:
            audited = audit_solution(problem, candidate)
            if audited["valid"]:
                best, best_cost = candidate, candidate["info"]["objective"]
                if multibatch:
                    best_state = clone_state(proposed)
                last_improvement = time.perf_counter() - begun
                if first_feasible is None:
                    first_feasible = last_improvement
                reward = 8
                improved[op] += 1
                if stock_proposal:
                    stock_improvements.append(dict(time=last_improvement, iteration=iterations,
                        pool=best['container_pool_size'], objective=best_cost, **stock_repair.last_run))
                if multibatch:
                    history.append(
                        dict(time=last_improvement, iteration=iterations, objective=best_cost)
                    )
            else:
                audit_rejections += 1
                new_score = 1e12
        # Expensive repair moves may hit the wall-clock budget long before the
        # iteration cap. Cool against whichever budget is being consumed faster.
        progress = control.progress(iteration)
        temperature = control.route_temperature(
            decoded["info"].get("objective") or 100, progress, multibatch=multibatch
        )
        accept = control.accept(rng, score, new_score, temperature)
        if accept:
            current, score, decoded = proposed, new_score, candidate
            reward = max(reward, 2)
        if index is not None:
            control.reward_online(weights, index, reward)
    result = best if best is not None else empty_result(problem, "alns")
    result["precheck"] = precheck
    result["info"]["proven_infeasible"] = False
    result["info"].update(
        status="FEASIBLE" if best is not None else "HEURISTIC_NO_SOLUTION",
        proven_optimal=False,
        proof_scope=None,
        best_bound=None,
        mip_gap=None,
        runtime=time.perf_counter() - begun,
        seed=seed,
        iterations=iterations,
        initial_strategy=initial_strategy,
        time_to_first_feasible=first_feasible,
        time_to_best=last_improvement,
        algorithm="adaptive_destroy_repair_cycle_search",
        cooling_schedule="max_time_and_iteration_progress",
        evaluation_cache_hits=cached.hits,
        evaluation_cache_misses=cached.misses,
        operator_noops=noops,
        operator_attempts=attempts,
        operator_improvements=improved,
        encountered_violations=sorted(seen_issues),
        audit_rejections=audit_rejections,
        e2_max_trips_per_vehicle=problem.operating_policy.e2_max_trips_per_vehicle,
    )
    if multibatch:
        arc_cache = descent.distance.cache_info() if cache_descent_distances else None
        result["info"].update(
            algorithm=("adaptive_cycle_cost_search_with_pool_optimization"
                       if getattr(problem, 'optimize_pool', False)
                       else "adaptive_cycle_search_with_distance_descent"),
            initial_objective=initial_objective,
            incumbent_history=history,
            incumbent_restarts=restarts,
            descent_candidates=descent.generated,
            pool_reuse_candidates=pool_descent.generated if pool_descent is not None else 0,
            descent_filtered=descent.filtered,
            descent_exhausted=descent.exhausted,
            descent_enumerations=descent.enumerations,
            descent_site_cache_hits=descent.site_cache_hits,
            descent_arc_cache_enabled=bool(cache_descent_distances),
            descent_arc_cache_hits=arc_cache.hits if arc_cache else 0,
            descent_arc_cache_misses=arc_cache.misses if arc_cache else 0,
            e1_schedule_cache_hits=cycle_evaluator.hits,
            e1_schedule_cache_misses=cycle_evaluator.misses,
            e2_schedule_cache_enabled=bool(cache_e2_schedule),
            e2_schedule_cache_hits=cycle_evaluator.e2_cache.hits if cache_e2_schedule else 0,
            e2_schedule_cache_misses=cycle_evaluator.e2_cache.misses if cache_e2_schedule else 0,
            shared_search_seed=0,
            shared_search_iterations=anchor_iterations,
        )
        if stock_repair is not None:
            result['info'].update(stock_chain_enabled=True, stock_chain_calls=stock_repair.calls,
                stock_chain_strategy=stock_chain_strategy,
                stock_chain_evaluations=stock_repair.evaluations,
                stock_chain_feasible_evaluations=stock_repair.feasible_evaluations,
                stock_chain_seconds=stock_repair.seconds,
                stock_chain_best_pool=stock_repair.best_pool,
                stock_chain_best_pool_cost=stock_repair.best_pool_cost,
                stock_chain_improvements=stock_improvements,
                stock_chain_last_run=stock_repair.last_run)
            if stock_chain_strategy == 'cost_aware_marginal':
                result['info'].update(stock_chain_marginal_generated=stock_repair.marginal_generated,
                    stock_chain_marginal_evaluations=stock_repair.marginal_evaluations,
                    stock_chain_marginal_feasible=stock_repair.marginal_feasible)
    if best is not None:
        audit = audit_solution(problem, result)
        result.update(
            audit=audit,
            cutoff_snapshots=audit["cutoff_snapshots"],
            depot_releases=audit["depot_releases"],
        )
    return result
