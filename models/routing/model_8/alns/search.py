"""Adaptive joint search of boundaries, cutoffs, order batches and routes.

Calendar changes repair order domains and routes using the physical decoder.
No mathematical solver, exact backend, or exact solution is used to initialize.
"""
from __future__ import annotations

from models.routing.model_8.alns.parameters import ALNSParameters
from models.routing.model_8.alns.search_control import SearchControl

from models.routing.model_8.alns.construction import initial_calendars, cover_delivery_batches

from models.routing.model_8.alns.neighborhoods import move_boundaries, boundary_events


from collections import OrderedDict
from copy import deepcopy
import math
import random
import time

from models.common.cycle.joint import joint_empty_result, audit_joint_solution
from models.routing.model_8.alns.joint_solver import (
    JointEvaluator,
    clone_state,
    _best_construction,
    _batch_move,
    _route_move,
    _cutoff_move,
    _seed_from_result,
    _initial_cutoff_profiles,
    _local_cost_scorer,
    destroy_repair,
)


def solve_variable_count(variable, *, max_time, max_iterations, seed, initial_result=None):
    parent, k = variable.parent, variable.count
    if k == 1:
        # There are no movable boundaries. Use exactly the same independent-
        # cutoff route search as M7, including its cutoff refinement phase.
        from models.routing.model_8.alns.joint_solver import solve_alns

        return solve_alns(
            parent.fixed_calendar(1),
            max_time=max_time,
            max_iterations=max_iterations,
            seed=seed,
            initial_result=initial_result,
        )
    begun = time.perf_counter()
    if k > len(parent.orders):
        result = joint_empty_result(variable, "alns")
        result["info"].update(
            status="INFEASIBLE", proven_infeasible=True,
            proof_scope="nonempty_batch_assignment_impossible",
            runtime=time.perf_counter() - begun, iterations=0,
        )
        return result
    parameters = ALNSParameters(max_time, max_iterations, seed)
    control = SearchControl(parameters, begun, time.perf_counter)
    deadline = begun + max_time
    rng = random.Random(seed)
    evaluators = OrderedDict()
    evicted_hits = evicted_misses = 0
    calendar_visits = set()
    penalty = (
        max(1.0, sum(parent.cost_profile.rates.values()))
        * parent.total
        * max(1, parent.cycle_duration)
        * 100
    )

    def context(boundaries):
        nonlocal evicted_hits, evicted_misses
        boundaries = tuple(boundaries)
        calendar_visits.add(boundaries)
        if boundaries not in evaluators:
            evaluators[boundaries] = JointEvaluator(parent.fixed_calendar(k, boundaries))
            if len(evaluators) > 24:
                _, old = evaluators.popitem(last=False)
                evicted_hits += old.hits
                evicted_misses += old.misses
        evaluators.move_to_end(boundaries)
        evaluator = evaluators[boundaries]
        return evaluator.problem, evaluator

    def repair(boundaries, assignment=None, state=None, *, profile=None):
        child, evaluator = context(boundaries)
        missing = sum(not bs for bs in child.legal_batches.values())
        if missing:
            return penalty * (10 + missing), None, None, None
        proposed = {
            oid: assignment[oid] if assignment is not None and assignment[oid] in bs else bs[-1]
            for oid, bs in child.legal_batches.items()
        }
        proposed = cover_delivery_batches(child, proposed)
        if proposed is None:
            return penalty * 10, None, None, None
        cuts = profile or (state.get("cutoffs") if state is not None else child.default_cutoffs)
        cuts = {bid: min(hi, max(lo, cuts[bid])) for bid, (lo, hi) in child.cutoff_bounds.items()}
        chosen = None
        if state is not None and proposed == assignment:
            trial = clone_state(state)
            trial["cutoffs"] = cuts
            # Explicit waits from an old calendar must not pin the new one.
            for route in trial["e2"]:
                route.pop("depart_after", None)
                route.pop("return_after", None)
            score, result, _ = evaluator(proposed, trial)
            chosen = score, proposed, trial, result
        if chosen is None or time.perf_counter() < deadline:
            score, trial, result, _ = _best_construction(
                child,
                evaluator,
                proposed,
                rng,
                deadline,
                randomized=state is not None,
                cutoffs=cuts,
            )
            if chosen is None or score < chosen[0]:
                chosen = score, proposed, trial, result
        return chosen

    best = best_state = best_assignment = best_boundaries = None
    best_cost = math.inf
    current = None
    history = []

    def remember(score, boundaries, assignment, state, candidate, iteration, source):
        nonlocal best, best_cost, best_state, best_assignment, best_boundaries
        if candidate is not None and score < best_cost - 1e-8:
            best, best_cost = deepcopy(candidate), score
            best_boundaries, best_assignment, best_state = (
                tuple(boundaries),
                dict(assignment),
                clone_state(state),
            )
            history.append(
                dict(
                    iteration=iteration,
                    objective=score,
                    time=time.perf_counter() - begun,
                    source=source,
                )
            )
            return True
        return False

    if initial_result is not None:
        bounds = tuple(initial_result["batch_boundaries"])
        child, evaluator = context(bounds)
        candidate, assignment, state = _seed_from_result(child, initial_result)
        remember(
            candidate["info"]["objective"],
            bounds,
            assignment,
            state,
            candidate,
            0,
            "supplied_incumbent",
        )
        score, decoded, _ = evaluator(assignment, state)
        current = score, bounds, assignment, state
        remember(score, bounds, assignment, state, decoded, 0, "initial_decode")
    init_deadline = begun + 0.3 * max_time
    for bounds in initial_calendars(variable):
        if current is not None and time.perf_counter() >= init_deadline:
            break
        child, _ = context(bounds)
        for profile in _initial_cutoff_profiles(child):
            if current is not None and time.perf_counter() >= init_deadline:
                break
            score, assignment, state, candidate = repair(bounds, profile=profile)
            if current is None or score < current[0]:
                current = score, bounds, assignment, state
            remember(score, bounds, assignment, state, candidate, 0, "calendar_construction")
            if state is None:
                break
    if current is None:
        current = penalty * 100, parent.balanced_boundaries(k), None, None
    boundary_ops = ["shift_boundary", "align_boundary", "redistribute_lengths"] if k > 1 else []
    route_ops = [
        "destroy_random_batch",
        "destroy_related_batch",
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
        "shift_cutoff",
        "align_cutoff",
    ]
    operators = boundary_ops + route_ops
    weights = control.initial_weight_dict(operators)
    attempts = {op: 0 for op in operators}
    improvements = {op: 0 for op in operators}
    rewards = {op: 0.0 for op in operators}
    uses = {op: 0 for op in operators}
    accepted = iterations = 0
    for iteration in range(max_iterations):
        if time.perf_counter() >= deadline:
            break
        iterations = iteration + 1
        if iteration and iteration % 100 == 0 and best is not None:
            current = best_cost, best_boundaries, dict(best_assignment), clone_state(best_state)
        score, bounds, assignment, state = current
        available = boundary_ops if state is None and boundary_ops else operators
        op = control.select_name(rng, available, weights)
        attempts[op] += 1
        uses[op] += 1
        proposed_bounds = bounds
        if op in boundary_ops:
            proposed_bounds = move_boundaries(variable, bounds, rng, op, best)
            new_score, proposed_assignment, proposed, candidate = repair(
                proposed_bounds, assignment, state
            )
        elif state is None:
            new_score, proposed_assignment, proposed, candidate = repair(bounds)
        else:
            child, evaluator = context(bounds)
            proposed_assignment = dict(assignment)
            if "batch" in op:
                proposed_assignment, proposed = _batch_move(
                    child, evaluator, assignment, rng, op, deadline, state["cutoffs"]
                )
            elif op in ("shift_cutoff", "align_cutoff"):
                proposed = _cutoff_move(
                    child, evaluator, assignment, state, rng, op == "align_cutoff"
                )
            else:
                fixed = evaluator.fixed(assignment, state["cutoffs"])
                if op == "destroy_routes":
                    fixed._joint_cost_rates = parent.cost_profile.rates
                    proposed = destroy_repair(
                        fixed,
                        state,
                        rng,
                        rng.choice(["random", "related"]),
                        deadline,
                        scorer=_local_cost_scorer,
                    )
                    proposed["cutoffs"] = dict(state["cutoffs"])
                else:
                    proposed = _route_move(fixed, state, rng, op)
            new_score, candidate, _ = evaluator(proposed_assignment, proposed)
        improved = remember(
            new_score, proposed_bounds, proposed_assignment, proposed, candidate, iterations, op
        )
        reward = 8.0 if improved else 0.0
        improvements[op] += int(improved)
        fraction = control.time_fraction()
        temperature = control.joint_temperature(best_cost, score, fraction)
        if control.accept(rng, score, new_score, temperature, joint=True):
            reward = max(reward, 3.0 if new_score < score else 1.0)
            current = new_score, proposed_bounds, proposed_assignment, proposed
            accepted += 1
        rewards[op] += reward
        if iterations % parameters.segment_period == 0:
            control.update_named_segment(operators, weights, rewards, uses)
    result = best if best is not None else joint_empty_result(variable, "alns")
    result["info"].update(
        status="FEASIBLE" if best is not None else "HEURISTIC_NO_SOLUTION",
        proven_optimal=False,
        proven_infeasible=False,
        best_bound=None,
        mip_gap=None,
        proof_scope=None,
        runtime=time.perf_counter() - begun,
        iterations=iterations,
        operator_attempts=attempts,
        operator_improvements=improvements,
        operator_weights=weights,
        accepted_moves=accepted,
        history=history,
        calendar_evaluations=len(calendar_visits),
        evaluations=evicted_misses + sum(e.misses for e in evaluators.values()),
        cache_hits=evicted_hits + sum(e.hits for e in evaluators.values()),
    )
    if best is not None:
        result["audit"] = audit_joint_solution(parent.fixed_calendar(k, best_boundaries), result)
        if not result["audit"]["valid"]:
            result["info"].update(status="AUDIT_FAILED", objective=None)
    return result
