"""Adaptive large-neighborhood search on order batches and full cycle routes.

The shared physical decoder is a schedule propagation routine, not a call to
another numbered solver. Every batch change rebuilds demand and downstream
inventory. No exact backend or Gurobi is loaded.
"""
from __future__ import annotations

from copy import deepcopy
import math
import random
import time

from models.common.cycle.audit import audit_solution
from models.common.cycle.joint import joint_empty_result
from models.common.cycle.pool_bound import forced_batch_pool_violation
from models.common.cycle.scope import require_fixed_calendar
from models.routing.alns.engine import Evaluation, OperatorWeights, Proposal, run_search
from models.routing.alns.search_control import SearchControl

from .construction import _best_construction, _initial_cutoff_profiles, _seed_from_result, cover_delivery_batches
from .cutoff_moves import _polish_cutoff
from .evaluator import JointEvaluator
from .neighborhoods import BATCH_ROUTE_OPERATORS, CUTOFF_OPERATORS, propose_batch_routes
from .parameters import ALNSParameters
from .state import clone_joint_state as clone_state


def solve_alns(problem, max_time=10, max_iterations=5000, seed=42, initial_result=None):
    require_fixed_calendar(problem)
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
    repair_stats = dict(timing_repair_attempts=0, timing_repair_successes=0,
                        chain_candidates=0, chain_improvements=0)
    deadline = begun + max_time
    parameters = ALNSParameters(max_time, max_iterations, seed)
    control = SearchControl(parameters, begun, time.perf_counter)
    # Reserve a bounded share for refining the best cutoff. Putting a
    # multi-evaluation descent in the adaptive operator roulette can starve
    # route/batch exploration while rewarding repeated no-op descents.
    route_polish_share = .2 if len(problem.orders) <= 16 else 0.
    search_deadline = deadline - (route_polish_share + (.1 if problem.optimize_cutoffs else 0.))*max_time
    proposal_problem = problem
    if len(problem.orders) <= 9:
        from models.routing.model_5.alns.temporal_domains import proposal_view
        proposal_problem, bounds = proposal_view(problem)
        if bounds['certificate'] is not None:
            result = joint_empty_result(problem, 'alns')
            result['info'].update(status='INFEASIBLE', proven_infeasible=True,
                proof_scope='necessary_temporal_assignment_domain', domain_certificate=bounds['certificate'],
                runtime=time.perf_counter()-begun, iterations=0)
            return result
    missing = [oid for oid, choices in proposal_problem.legal_batches.items() if not choices]
    if missing:
        result = joint_empty_result(problem, "alns")
        result["info"].update(
            status="INFEASIBLE",
            proven_infeasible=True,
            proof_scope="empty_necessary_order_batch_domain",
            orders=missing,
        )
        return result
    pool_violation = forced_batch_pool_violation(problem)
    if pool_violation:
        result = joint_empty_result(problem, "alns")
        result["info"].update(status="INFEASIBLE", proven_infeasible=True,
            proof_scope="forced_batch_demand_exceeds_pool_max", pool_certificate=pool_violation,
            runtime=time.perf_counter()-begun, iterations=0)
        return result
    rng = random.Random(seed)
    evaluator = JointEvaluator(problem)
    original = {
        o.id: o.batch if o.batch in proposal_problem.legal_batches[o.id] else proposal_problem.legal_batches[o.id][0]
        for o in problem.orders
    }
    earliest = {
        oid: min(bs, key=lambda b: problem.batch_index[b])
        for oid, bs in proposal_problem.legal_batches.items()
    }
    latest = {
        oid: max(bs, key=lambda b: problem.batch_index[b])
        for oid, bs in proposal_problem.legal_batches.items()
    }
    from models.routing.model_5.alns.batch_initialization import balanced_assignments
    seeds = balanced_assignments(proposal_problem) + [original, earliest, latest]
    for b in problem.operating_policy.batches:
        seeds.append(
            {
                oid: b.id if b.id in bs else earliest[oid]
                for oid, bs in proposal_problem.legal_batches.items()
            }
        )
    seeds = [covered for assignment in seeds
             if (covered := cover_delivery_batches(proposal_problem, assignment)) is not None]
    if not seeds:
        result = joint_empty_result(problem, "alns")
        result["info"].update(
            status="INFEASIBLE", proven_infeasible=True,
            proof_scope="nonempty_batch_assignment_impossible",
            runtime=time.perf_counter() - begun, iterations=0,
        )
        return result
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
        current = Evaluation(score, (dict(best_assignment), clone_state(best_state)), candidate)
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
    starts += [(seeds[0], profile) for profile in profiles[1:]]
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
        if current is None or score < current.score:
            current = Evaluation(score, (assignment, state), result)
        if result is not None and score < best_cost:
            best, best_cost = deepcopy(result), score
            best_assignment, best_state = dict(assignment), clone_state(state)
            history.append(dict(iteration=0, objective=score, time=time.perf_counter() - begun))
    operators = BATCH_ROUTE_OPERATORS + (CUTOFF_OPERATORS if problem.optimize_cutoffs else ())
    weights = OperatorWeights(control, operators, segmented=True)
    restarts = 0

    def propose(trial, iteration):
        nonlocal restarts
        op = weights.choose(rng)
        if iteration and iteration % 100 == 0 and best is not None:
            assignment, state = dict(best_assignment), clone_state(best_state)
            score, decoded, _ = evaluator(assignment, state)
            trial = Evaluation(score, (assignment, state), decoded)
            restarts += 1
        assignment, state = trial.state
        proposed_assignment, proposed = propose_batch_routes(
            evaluator, proposal_problem, assignment, state, rng, op,
            search_deadline, repair_stats,
        )
        if time.perf_counter() >= search_deadline:
            return Proposal(trial, operators=(op,), stop=True)
        new_score, candidate, issues = evaluator(proposed_assignment, proposed)
        seen.update(issues)
        return Proposal(trial, Evaluation(new_score, (proposed_assignment, proposed), candidate), (op,))

    def remember(proposal, iterations):
        nonlocal best, best_cost, best_assignment, best_state
        evaluation = proposal.candidate
        if evaluation.result is None or not evaluation.score < best_cost - 1e-8:
            return False
        assignment, state = evaluation.state
        best, best_cost = deepcopy(evaluation.result), evaluation.score
        best_assignment, best_state = dict(assignment), clone_state(state)
        history.append(dict(iteration=iterations, objective=best_cost, time=time.perf_counter() - begun))
        return True

    def accept(proposal, _iteration, _improved):
        return control.accept_joint(rng, proposal.current.score, proposal.candidate.score, best_cost)

    _, iterations, accepted = run_search(
        current, control, weights, propose, remember, accept, deadline=search_deadline,
    )
    route_polish_evaluations = route_polish_improvements = 0
    if route_polish_share and best is not None:
        from models.routing.model_5.alns.polish import polish_routes
        route_deadline = deadline - (.1*max_time if problem.optimize_cutoffs else 0.)
        if len(problem.orders) <= 9:
            from models.routing.model_5.alns.chain_repair import recovery_frontier, complete_request_repair, _distance
            fixed = evaluator.fixed(best_assignment, best_state['cutoffs'])
            ranking = []
            for oid in best_assignment:
                removed = [{**row, 'tasks': [task for task in row['tasks'] if task[0] != oid]} for row in best_state['e2']]
                ranking.append((_distance(fixed, removed), oid))
            polished, calls, gains = complete_request_repair(problem, evaluator, best_assignment, best_state,
                [oid for _, oid in sorted(ranking)[:2]],
                time.perf_counter()+.4*max(0., route_deadline-time.perf_counter()), max_evaluations=72)
            repair_stats['chain_candidates'] += calls
            repair_stats['chain_improvements'] += gains
            value, candidate, _ = evaluator(best_assignment, polished)
            if candidate is not None and value < best_cost-1e-8:
                best, best_cost, best_state = deepcopy(candidate), value, polished
                history.append(dict(iteration=iterations, objective=value,
                    time=time.perf_counter()-begun, source='complete_cost_route_repair'))
            polished, calls, gains = recovery_frontier(problem, evaluator, best_assignment, best_state,
                time.perf_counter()+.25*max(0., route_deadline-time.perf_counter()), max_evaluations=32)
            repair_stats['chain_candidates'] += calls
            repair_stats['chain_improvements'] += gains
            value, candidate, _ = evaluator(best_assignment, polished)
            if candidate is not None and value < best_cost-1e-8:
                best, best_cost, best_state = deepcopy(candidate), value, polished
                history.append(dict(iteration=iterations, objective=value,
                    time=time.perf_counter()-begun, source='joint_recovery_profile'))
        polished, route_polish_evaluations, route_polish_improvements = polish_routes(
            evaluator.fixed(best_assignment, best_state.get("cutoffs")), evaluator,
            best_assignment, best_state, route_deadline)
        value, candidate, _ = evaluator(best_assignment, polished)
        if candidate is not None and value < best_cost-1e-8:
            best, best_cost, best_state = deepcopy(candidate), value, polished
            history.append(dict(iteration=iterations, objective=value,
                                time=time.perf_counter()-begun, source="route_polish"))
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
        **repair_stats,
        operator_attempts=weights.attempts,
        operator_improvements=weights.improvements,
        operator_weights=weights.weights,
        cache_hits=evaluator.hits,
        evaluations=evaluator.misses,
        route_polish_evaluations=route_polish_evaluations,
        route_polish_improvements=route_polish_improvements,
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
