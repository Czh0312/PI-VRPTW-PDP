"""Model 7 ALNS: free customer assignment plus asynchronous box relay.

Model 7 combines the two independent extensions represented by Model 4 and
Model 6. The first phase searches customer--satellite assignments and the
associated E1/E2 routes with Model 4 operators. The second phase applies
Model 6 asynchronous handoff search to a bounded pool of distinct assignments.
Unused relay time can resume free-assignment search from a clean snapshot.
The zero-handoff Model 4 incumbent is always retained.
"""

from __future__ import annotations

import random
import time

from models.routing.alns.engine import ALNSConfig, run_alns_search
from models.routing.alns.types import FEAS_TOL
from models.routing.alns.variants.model_4 import (
    Evaluator as FreeAssignmentEvaluator,
    M4Hooks,
    build_initial_solution as build_free_assignment_initial_solution,
    consolidated_satellite_insertion,
    multi_sat_insertion,
    multi_sat_insertion_cross,
    random_customer_removal,
    randomized_satellite_insertion,
    satellite_cluster_removal,
    shaw_customer_removal,
    worst_customer_removal,
)
from models.routing.alns.assignment_operators import (
    assignment_boundary_removal,
    two_satellite_removal,
)
from models.routing.common.task_builder import build_tasks_from_assignment
from models.routing.alns.variants.model_6 import (
    Evaluator as RelayEvaluator,
    _path_with_relocations,
    postprocess_transshipment,
)


class AssignmentPoolHooks(M4Hooks):
    """Keep the cheapest accepted solution for each of a few assignments."""

    def __init__(self, size, fixed_route_probability=0.8):
        super().__init__(fixed_route_probability)
        self.size = max(1, int(size))
        self.pool = {}

    def remember(self, evaluator, solution):
        key = tuple(sorted(evaluator.assignment(solution).items()))
        previous = self.pool.get(key)
        if previous is not None and previous.cost <= solution.cost:
            return
        if len(self.pool) >= self.size and previous is None:
            worst = max(self.pool, key=lambda k: self.pool[k].cost)
            if self.pool[worst].cost <= solution.cost:
                return
            del self.pool[worst]
        self.pool[key] = solution.copy()

    def after_accept(self, evaluator, accepted_solution):
        self.remember(evaluator, accepted_solution)


M7_DESTROY_OPS = (
    random_customer_removal,
    worst_customer_removal,
    shaw_customer_removal,
    satellite_cluster_removal,
    assignment_boundary_removal,
    two_satellite_removal,
)

M7_REPAIR_OPS = (
    multi_sat_insertion,
    multi_sat_insertion_cross,
    randomized_satellite_insertion,
    consolidated_satellite_insertion,
)


def _no_solution(return_info, elapsed, iterations=0, reason="NO_SOLUTION"):
    info = {
        "objective": None,
        "runtime": float(elapsed),
        "iterations": int(iterations),
        "feasible": False,
        "status": reason,
        "assignment": None,
        "transshipment_count": 0,
        "transshipment_events": [],
    }
    return ((None, None, None, info) if return_info
            else (None, None, None))


def solve_alns(
    problem,
    e1_tasks=None,
    e2_tasks_by_sat=None,
    vehicles_per_satellite=2,
    TFp=9999.0,
    max_iterations=5000,
    max_time=120,
    seed=42,
    verbose=True,
    stage1_time_ratio=0.75,
    assignment_pool_size=4,
    transshipment_time_budget=30.0,
    transshipment_sample_pickups=None,
    transshipment_sample_locations=None,
    transshipment_sample_carriers=None,
    transshipment_position_top_k=None,
    transshipment_min_net_saving=0.01,
    transshipment_max_total=None,
    transshipment_max_per_satellite=None,
    return_info=False,
    initial_solution=None,
    target_objective=None,
    target_tolerance=1e-6,
    alns_config_overrides=None,
    refill_unused_time=True,
    fixed_route_probability=0.8,
    **deprecated_options,
):
    """Solve the hybrid free-assignment/asynchronous-relay Model 7.

    ``e1_tasks`` and ``e2_tasks_by_sat`` are accepted for the common service
    contract but ignored: a free-assignment model rebuilds both task sets from
    the assignment selected by the search.
    """
    del e1_tasks, e2_tasks_by_sat
    random.seed(seed)
    start_time = time.time()

    if deprecated_options and verbose:
        names = ", ".join(sorted(deprecated_options))
        print("[WARN] Model 7 已改为 M4+M6 联合机制，以下旧逐轮探针参数不再使用: "
              f"{names}")

    ratio = max(0.0, min(1.0, float(stage1_time_ratio)))
    stage1_limit = max(0.0, float(max_time) * ratio)
    free_evaluator = FreeAssignmentEvaluator(
        problem, TFp, vehicles_per_satellite)
    free_solution = (
        initial_solution.copy()
        if initial_solution is not None
        else build_free_assignment_initial_solution(
            problem, free_evaluator, TFp)
    )
    free_solution.cost = free_evaluator.total_cost(free_solution)
    initial_objective = float(free_solution.cost)

    if verbose:
        print(f"\n{'=' * 60}")
        print("ALNS 求解器 (Model 7 自由分仓 + 异步空箱交接)")
        print(f"{'=' * 60}")
        print(f"初始目标值: {free_solution.cost:.2f}")
        print(f"阶段一时间占比: {ratio:.2f}")
        print("阶段一: Model 4 分配感知 ALNS")

    pool_hooks = AssignmentPoolHooks(assignment_pool_size, fixed_route_probability)
    if free_evaluator.is_feasible(free_solution):
        pool_hooks.remember(free_evaluator, free_solution)

    polish_reserve = (min(.05, stage1_limit*.01) if len(problem.customers) <= 20
                      and (alns_config_overrides or {}).get('cooling_schedule','time') == 'time' else 0.0)
    stage1_result = run_alns_search(
        initial_solution=free_solution,
        evaluator=free_evaluator,
        destroy_ops=M7_DESTROY_OPS,
        repair_ops=M7_REPAIR_OPS,
        config=ALNSConfig(
            max_iterations=max_iterations,
            max_time=stage1_limit,
            e2_probability=1.0,
            target_cost=target_objective,
            target_tolerance=target_tolerance,
            **(alns_config_overrides or {}),
        ),
        hooks=pool_hooks,
        verbose=verbose,
        start_time=start_time,
        search_time_limit=stage1_limit-polish_reserve,
    )
    best_solution = stage1_result.best_solution
    if best_solution is None:
        return _no_solution(
            return_info, time.time() - start_time,
            stage1_result.iterations)

    if (polish_reserve and stage1_result.target_hit_time is None
            and any(best_solution.e2_routes.values())):
        from models.routing.alns.route_exchange import improve_route_exchanges
        free_evaluator._alns_deadline = start_time+stage1_limit
        polished, changes = improve_route_exchanges(best_solution,free_evaluator,start_time+stage1_limit)
        if polished.cost < best_solution.cost-FEAS_TOL:
            best_solution = polished
            stage1_result.best_solution = polished
            stage1_result.best_cost = polished.cost
            stage1_result.best_found_time = time.time()-start_time
            stage1_result.local_search_improvements += changes
            if target_objective is not None and polished.cost <= target_objective+target_tolerance:
                stage1_result.target_hit_time = stage1_result.best_found_time
                stage1_result.termination_reason = 'target'

    no_relay_snapshot = best_solution.copy()
    pool_hooks.remember(free_evaluator, best_solution)
    stage1_best_cost = float(stage1_result.best_cost)
    candidates = sorted(pool_hooks.pool.values(), key=lambda sol: sol.cost)
    remaining = max(0.0, float(max_time) - (time.time() - start_time))
    requested_relay_budget = max(0.0, float(transshipment_time_budget))
    relay_budget = min(requested_relay_budget, remaining)
    relay_started = time.time()
    relay_deadline = relay_started + relay_budget
    winner = None
    candidate_reports = []
    relay_target_hit_time = None
    best_found_time = stage1_result.best_found_time
    for index, candidate in enumerate(candidates):
        # Always audit the best zero-relay candidate, even with no time left.
        if index and time.time() >= relay_deadline:
            break
        candidate_assignment = free_evaluator.assignment(candidate)
        relay_e1_tasks, relay_e2_tasks = build_tasks_from_assignment(
            problem, candidate_assignment)
        evaluator = RelayEvaluator(
            problem, relay_e1_tasks, relay_e2_tasks, TFp,
            vehicles_per_satellite=vehicles_per_satellite)
        candidate = candidate.copy()
        candidate.task_to_phys = dict(evaluator.task_to_phys)
        candidate.transshipments = {}
        candidate.cost = evaluator.total_cost(candidate)
        if not evaluator.is_feasible(candidate):
            continue
        baseline = candidate.copy()
        baseline_cost = float(candidate.cost)
        events = []
        candidate_budget = max(0.0, relay_deadline - time.time()) / (
            len(candidates) - index)
        hit = postprocess_transshipment(
            candidate, evaluator, verbose=verbose,
            time_budget=candidate_budget,
            sample_pickups=transshipment_sample_pickups,
            sample_locations=transshipment_sample_locations,
            sample_carriers=transshipment_sample_carriers,
            position_top_k=transshipment_position_top_k,
            min_net_saving=transshipment_min_net_saving,
            max_transshipments=transshipment_max_total,
            max_transshipments_per_sat=transshipment_max_per_satellite,
            target_cost=target_objective, target_tolerance=target_tolerance,
            solver_start_time=start_time, audit_log=events)
        candidate.cost = evaluator.total_cost(candidate)
        if (not evaluator.is_feasible(candidate)
                or candidate.cost > baseline_cost):
            candidate, events, hit = baseline, [], None
            evaluator.task_to_phys = dict(baseline.task_to_phys)
        candidate_reports.append({
            "assignment": dict(candidate_assignment),
            "baseline_objective": baseline_cost,
            "objective": float(candidate.cost),
            "transshipment_count": len(candidate.transshipments),
        })
        if winner is None or candidate.cost < winner[0].cost:
            if candidate.cost < stage1_best_cost:
                best_found_time = time.time() - start_time
            winner = (candidate, evaluator, candidate_assignment,
                      baseline_cost, events)
        if hit is not None:
            relay_target_hit_time = hit
            break
    if winner is None:
        return _no_solution(return_info, time.time() - start_time,
                            stage1_result.iterations, "AUDIT_FAILED")
    (best_solution, relay_evaluator, assignment,
     free_assignment_cost, transshipment_events) = winner
    relay_elapsed = time.time() - relay_started
    best_solution.cost = relay_evaluator.total_cost(best_solution)
    relay_cost = float(best_solution.cost)
    total_iterations = int(stage1_result.iterations)
    refill_iterations = 0
    refill_result = None
    refill_hooks = M4Hooks(fixed_route_probability)

    # Relay enumeration may finish well before its reserved budget.  Reuse the
    # remaining total budget for free-assignment ALNS from a clean pre-relay
    # snapshot, then keep the better incumbent.  This avoids exposing virtual
    # relay nodes to Model 4 operators while ensuring Model 7 uses its full cap.
    if (refill_unused_time
            and relay_target_hit_time is None
            and stage1_result.target_hit_time is None
            and total_iterations < max_iterations
            and time.time() - start_time < max_time - 1e-3):
        refill_result = run_alns_search(
            initial_solution=no_relay_snapshot,
            evaluator=free_evaluator,
            destroy_ops=M7_DESTROY_OPS,
            repair_ops=M7_REPAIR_OPS,
            config=ALNSConfig(
                max_iterations=max_iterations - total_iterations,
                max_time=max_time,
                e2_probability=1.0,
                target_cost=target_objective,
                target_tolerance=target_tolerance,
                **(alns_config_overrides or {}),
            ),
            hooks=refill_hooks,
            verbose=False,
            start_time=start_time,
        )
        refill_iterations = int(refill_result.iterations)
        total_iterations += refill_iterations
        if refill_result.best_solution is not None:
            if refill_result.best_cost < relay_cost - FEAS_TOL:
                refill_solution = refill_result.best_solution.copy()
                refill_assignment = free_evaluator.assignment(refill_solution)
                relay_e1_tasks, relay_e2_tasks = build_tasks_from_assignment(
                    problem, refill_assignment)
                refill_evaluator = RelayEvaluator(
                    problem,
                    relay_e1_tasks,
                    relay_e2_tasks,
                    TFp,
                    vehicles_per_satellite=vehicles_per_satellite,
                )
                refill_solution.task_to_phys = dict(
                    refill_evaluator.task_to_phys)
                refill_solution.transshipments = {}
                refill_solution.cost = refill_evaluator.total_cost(refill_solution)
                if (refill_solution.cost < relay_cost - FEAS_TOL
                        and refill_evaluator.is_feasible(refill_solution)):
                    best_solution = refill_solution
                    assignment = refill_assignment
                    relay_evaluator = refill_evaluator
                    # A refill winner has no relay; preserve the winning
                    # assignment's before/after accounting, not another seed's.
                    free_assignment_cost = float(best_solution.cost)
                    transshipment_events = []
                    best_found_time = refill_result.best_found_time
                    if refill_result.target_hit_time is not None:
                        relay_target_hit_time = refill_result.target_hit_time

    moved_customers = sum(
        1 for customer in problem.customers
        if assignment.get(customer.id) != customer.assigned_sat
    )
    feasible = bool(relay_evaluator.is_feasible(best_solution))
    improvement = free_assignment_cost - best_solution.cost

    e1_routes_out = [
        _path_with_relocations(route, best_solution, relay_evaluator)
        for route in best_solution.e1_routes
    ]
    e2_routes_out = {
        sat_id: [
            _path_with_relocations(route, best_solution, relay_evaluator)
            for route in routes
        ]
        for sat_id, routes in best_solution.e2_routes.items()
    }

    elapsed = time.time() - start_time
    info = {
        "objective": float(best_solution.cost),
        "stage1_best_objective": stage1_best_cost,
        "assignment_pool_size": pool_hooks.size,
        "assignment_candidates_evaluated": len(candidate_reports),
        "assignment_candidate_results": candidate_reports,
        "free_assignment_objective": float(free_assignment_cost),
        "transshipment_improvement": float(improvement),
        "runtime": float(elapsed),
        "iterations": int(total_iterations),
        "feasible": feasible,
        "status": "FEASIBLE" if feasible else "NO_SOLUTION",
        "external_initial_solution": initial_solution is not None,
        "initial_objective": initial_objective,
        "assignment": dict(assignment),
        "moved_customers": int(moved_customers),
        "transshipment_count": len(best_solution.transshipments),
        "transshipment_events": transshipment_events,
        "stage1_time_ratio": ratio,
        "stage1_time_budget_seconds": float(stage1_limit),
        "transshipment_time_budget_requested": requested_relay_budget,
        "transshipment_time_budget_effective": float(relay_budget),
        "transshipment_search_elapsed_seconds": float(relay_elapsed),
        "best_found_time": best_found_time,
        "refill_unused_time": bool(refill_unused_time),
        "refill_iterations": int(refill_iterations),
        "target_hit_time": (
            stage1_result.target_hit_time
            if stage1_result.target_hit_time is not None
            else relay_target_hit_time
        ),
        "termination_reason": (
            "target" if relay_target_hit_time is not None
            else stage1_result.termination_reason
        ),
        "restarts": stage1_result.restarts,
        "local_search_improvements": stage1_result.local_search_improvements,
        "alns_diagnostics": stage1_result.diagnostics,
        "fixed_route_probability": pool_hooks.fixed_route_probability,
        "neighborhood_attempts": {
            key: count + refill_hooks.neighborhood_attempts[key]
            for key, count in pool_hooks.neighborhood_attempts.items()
        },
        "model_scope": "free_assignment_plus_async_event_handoff",
    }

    if verbose:
        print(f"Model 7 完成: cost={best_solution.cost:.2f}, "
              f"中转改进={improvement:.2f}, "
              f"中转数={len(best_solution.transshipments)}, "
              f"总耗时={elapsed:.2f}s")

    return ((e1_routes_out, e2_routes_out, assignment, info)
            if return_info
            else (e1_routes_out, e2_routes_out, assignment))
