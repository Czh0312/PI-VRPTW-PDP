"""Adaptive large-neighborhood search on order batches and full cycle routes.

The shared physical decoder is a schedule propagation routine, not a call to
another numbered solver. Every batch change rebuilds demand and downstream
inventory. No exact backend or Gurobi is loaded.
"""
from __future__ import annotations

from models.routing.model_7.alns.parameters import ALNSParameters
from models.routing.model_7.alns.search_control import SearchControl

from models.routing.model_7.alns.neighborhoods import (
    _cutoff_move,
    _polish_cutoff,
    _with_cutoff,
    _cutoff_events,
    _local_cost_scorer,
    _route_move,
    _batch_move,
    clone_state as _physical_clone,
    state_key,
    destroy_repair,
    _route_score,
)


from models.routing.model_7.alns.construction import (
    _seed_from_result,
    _initial_cutoff_profiles,
    _best_construction,
    chronological_state,
    appointment_state,
)


from models.routing.model_7.alns.evaluator import (
    JointEvaluator,
    _delay_departures,
    initial_state,
    evaluate,
)

from models.routing.model_7.alns.state import clone_joint_state as clone_state

from collections import OrderedDict
from copy import deepcopy
import math
import random
import time

from models.common.cycle.scope import validate_problem
from models.common.cycle.joint import joint_empty_result, lift_result
from models.common.cycle.audit import audit_solution


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42, initial_result=None):
    validate_problem(problem, 6)
    if (
        isinstance(max_time, bool)
        or not math.isfinite(max_time)
        or max_time <= 0
        or isinstance(max_iterations, bool)
        or not isinstance(max_iterations, int)
        or max_iterations < 0
    ):
        raise ValueError("Invalid ALNS time or iteration budget")
    begun = time.perf_counter()
    deadline = begun + max_time
    parameters = ALNSParameters(max_time, max_iterations, seed)
    control = SearchControl(parameters, begun, time.perf_counter)
    # Reserve a bounded share for refining the best cutoff. Putting a
    # multi-evaluation descent in the adaptive operator roulette can starve
    # route/batch exploration while rewarding repeated no-op descents.
    search_deadline = deadline - 0.1 * max_time if problem.optimize_cutoffs else deadline
    missing = [oid for oid, choices in problem.legal_batches.items() if not choices]
    if missing:
        result = joint_empty_result(problem, "alns")
        result["info"].update(
            status="INFEASIBLE",
            proven_infeasible=True,
            proof_scope="empty_necessary_order_batch_domain",
            orders=missing,
        )
        return result
    rng = random.Random(seed)
    evaluator = JointEvaluator(problem)
    original = {
        o.id: o.batch if o.batch in problem.legal_batches[o.id] else problem.legal_batches[o.id][0]
        for o in problem.orders
    }
    earliest = {
        oid: min(bs, key=lambda b: problem.batch_index[b])
        for oid, bs in problem.legal_batches.items()
    }
    latest = {
        oid: max(bs, key=lambda b: problem.batch_index[b])
        for oid, bs in problem.legal_batches.items()
    }
    seeds = [original, earliest, latest]
    for b in problem.operating_policy.batches:
        seeds.append(
            {
                oid: b.id if b.id in bs else earliest[oid]
                for oid, bs in problem.legal_batches.items()
            }
        )
    current = None
    best = None
    best_state = None
    best_assignment = None
    best_cost = math.inf
    seen = set()
    history = []
    initial_cost = None
    if initial_result is not None:
        best, best_assignment, best_state = _seed_from_result(problem, initial_result)
        best_cost = initial_cost = best["info"]["objective"]
        score, candidate, issues = evaluator(best_assignment, best_state)
        current = (score, dict(best_assignment), clone_state(best_state))
        if candidate is not None and score < best_cost:
            best, best_cost = deepcopy(candidate), score
        history.append(
            dict(
                iteration=0,
                objective=best_cost,
                time=time.perf_counter() - begun,
                source="supplied_incumbent",
            )
        )
    profiles = _initial_cutoff_profiles(problem)
    starts = [(assignment, profiles[0]) for assignment in seeds]
    starts += [(original, profile) for profile in profiles[1:]]
    visited_starts = set()
    for assignment, cutoffs in starts:
        if current is not None and time.perf_counter() >= search_deadline:
            break
        start_key = (tuple(sorted(assignment.items())), tuple(sorted(cutoffs.items())))
        if start_key in visited_starts:
            continue
        visited_starts.add(start_key)
        score, state, result, issues = _best_construction(
            problem, evaluator, assignment, rng, search_deadline, cutoffs=cutoffs
        )
        seen.update(issues)
        if current is None or score < current[0]:
            current = (score, assignment, state)
        if result is not None and score < best_cost:
            best, best_cost = deepcopy(result), score
            best_assignment, best_state = dict(assignment), clone_state(state)
            history.append(dict(iteration=0, objective=score, time=time.perf_counter() - begun))
    operators = [
        "destroy_random_batch",
        "destroy_related_batch",
        "close_batch",
        "split_batch",
        "destroy_routes",
        "relocate",
        "pair",
        "split_merge",
        "vehicle",
        "wait",
        "e1_delivery",
        "e1_pickup",
        "reverse",
    ]
    if problem.optimize_cutoffs:
        operators += ["shift_cutoff", "align_cutoff"]
    weights = control.initial_weights(operators)
    rewards = [0.0] * len(operators)
    uses = [0] * len(operators)
    attempts = {op: 0 for op in operators}
    improvements = {op: 0 for op in operators}
    accepted = iterations = restarts = 0
    score, assignment, state = current
    for iteration in range(max_iterations):
        if time.perf_counter() >= search_deadline:
            break
        iterations = iteration + 1
        index = control.select_index(rng, weights)
        op = operators[index]
        attempts[op] += 1
        uses[index] += 1
        if iteration and iteration % 100 == 0 and best is not None:
            assignment, state = dict(best_assignment), clone_state(best_state)
            score = evaluator(assignment, state)[0]
            restarts += 1
        proposed_assignment = dict(assignment)
        if "batch" in op:
            proposed_assignment, proposed = _batch_move(
                problem, evaluator, assignment, rng, op, search_deadline, state.get("cutoffs")
            )
        elif op in ("shift_cutoff", "align_cutoff"):
            proposed = _cutoff_move(
                problem, evaluator, assignment, state, rng, op == "align_cutoff"
            )
        else:
            fixed = evaluator.fixed(assignment, state.get("cutoffs"))
            if op == "destroy_routes":
                fixed._joint_cost_rates = problem.cost_profile.rates
                proposed = destroy_repair(
                    fixed,
                    state,
                    rng,
                    rng.choice(["random", "related"]),
                    search_deadline,
                    scorer=_local_cost_scorer,
                )
                proposed["cutoffs"] = dict(state["cutoffs"])
            else:
                proposed = _route_move(fixed, state, rng, op)
        if time.perf_counter() >= search_deadline:
            break
        new_score, candidate, issues = evaluator(proposed_assignment, proposed)
        seen.update(issues)
        reward = 0.0
        if candidate is not None and new_score < best_cost - 1e-8:
            best, best_cost = deepcopy(candidate), new_score
            best_assignment, best_state = dict(proposed_assignment), clone_state(proposed)
            history.append(
                dict(iteration=iterations, objective=best_cost, time=time.perf_counter() - begun)
            )
            improvements[op] += 1
            reward = 8.0
        fraction = control.time_fraction()
        temperature = control.joint_temperature(best_cost, score, fraction)
        if control.accept(rng, score, new_score, temperature, joint=True):
            if new_score < score:
                reward = max(reward, 3.0)
            else:
                reward = max(reward, 1.0)
            score, assignment, state = new_score, proposed_assignment, proposed
            accepted += 1
        rewards[index] += reward
        if iterations % parameters.segment_period == 0:
            weights = control.update_segment(weights, rewards, uses)
            rewards = [0.0] * len(operators)
            uses = [0] * len(operators)
    polish_calls = polish_improvements = 0
    polish_started = time.perf_counter()
    polish_evaluations = evaluator.misses
    if problem.optimize_cutoffs and best is not None:
        polish_deadline = min(deadline, polish_started + 0.1 * max_time)
        while time.perf_counter() < polish_deadline:
            polished = _polish_cutoff(
                problem, evaluator, best_assignment, best_state, polish_deadline
            )
            polished_cost, candidate, issues = evaluator(best_assignment, polished)
            seen.update(issues)
            polish_calls += 1
            if candidate is None or polished_cost >= best_cost - 1e-8:
                break
            best, best_cost = deepcopy(candidate), polished_cost
            best_state = clone_state(polished)
            polish_improvements += 1
            history.append(
                dict(
                    iteration=iterations,
                    objective=best_cost,
                    time=time.perf_counter() - begun,
                    source="cutoff_polish",
                )
            )
    polish_runtime = time.perf_counter() - polish_started
    polish_evaluations = evaluator.misses - polish_evaluations
    result = best if best is not None else joint_empty_result(problem, "alns")
    result["solver_module"] = __name__
    result["info"].update(
        status="FEASIBLE" if best is not None else "HEURISTIC_NO_SOLUTION",
        proven_optimal=False,
        proven_infeasible=False,
        best_bound=None,
        mip_gap=None,
        runtime=time.perf_counter() - begun,
        iterations=iterations,
        seed=seed,
        accepted_moves=accepted,
        restarts=restarts,
        operator_attempts=attempts,
        operator_improvements=improvements,
        operator_weights=dict(zip(operators, weights)),
        cache_hits=evaluator.hits,
        evaluations=evaluator.misses,
        cutoff_polish_calls=polish_calls,
        cutoff_polish_improvements=polish_improvements,
        cutoff_polish_evaluations=polish_evaluations,
        cutoff_polish_runtime=polish_runtime,
        cutoff_polish_budget=0.1 * max_time if problem.optimize_cutoffs else 0.0,
        encountered_violations=sorted(seen),
        history=history,
        optimized_cutoffs=problem.optimize_cutoffs,
        initial_incumbent_cost=initial_cost,
        decoder_scope="earliest_DAG_events_plus_explicit_waits_and_latest_departure_before_first_service",
    )
    if best is not None:
        result["audit"] = audit_solution(problem, result)
        if not result["audit"]["valid"]:
            result["info"].update(status="AUDIT_FAILED", objective=None)
    return result
