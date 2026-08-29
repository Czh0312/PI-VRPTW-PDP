"""Model 7 ALNS: free customer assignment plus asynchronous box relay.

Model 7 combines the two independent extensions represented by Model 4 and
Model 6. The first phase searches customer--satellite assignments and the
associated E1/E2 routes with Model 4 operators. The second phase freezes the
selected assignment and applies Model 6 asynchronous handoff search. The
zero-handoff Model 4 incumbent is always retained.
"""

from __future__ import annotations

import random
import time

from models.routing.alns.engine import ALNSConfig, run_alns_search
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
from models.routing.common.task_builder import build_tasks_from_assignment
from models.routing.alns.variants.model_6 import (
    Evaluator as RelayEvaluator,
    _path_with_relocations,
    postprocess_transshipment,
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

    if verbose:
        print(f"\n{'=' * 60}")
        print("ALNS 求解器 (Model 7 自由分仓 + 异步空箱交接)")
        print(f"{'=' * 60}")
        print(f"初始目标值: {free_solution.cost:.2f}")
        print(f"阶段一时间占比: {ratio:.2f}")
        print("阶段一: Model 4 分配感知 ALNS")

    stage1_result = run_alns_search(
        initial_solution=free_solution,
        evaluator=free_evaluator,
        destroy_ops=[
            random_customer_removal,
            worst_customer_removal,
            shaw_customer_removal,
            satellite_cluster_removal,
        ],
        repair_ops=[
            multi_sat_insertion,
            multi_sat_insertion_cross,
            randomized_satellite_insertion,
            consolidated_satellite_insertion,
        ],
        config=ALNSConfig(
            max_iterations=max_iterations,
            max_time=stage1_limit,
            e2_probability=1.0,
            target_cost=target_objective,
            target_tolerance=target_tolerance,
            **(alns_config_overrides or {}),
        ),
        hooks=M4Hooks(),
        verbose=verbose,
        start_time=start_time,
    )
    best_solution = stage1_result.best_solution
    if best_solution is None:
        return _no_solution(
            return_info, time.time() - start_time,
            stage1_result.iterations)

    assignment = free_evaluator.assignment(best_solution)
    free_assignment_cost = float(stage1_result.best_cost)
    best_solution.cost = free_assignment_cost
    moved_customers = sum(
        1 for customer in problem.customers
        if assignment.get(customer.id) != customer.assigned_sat
    )

    # Rebuild a fixed task catalogue for the phase-one assignment so Model 6's
    # transport-node load and synchronization audit can be reused unchanged.
    relay_e1_tasks, relay_e2_tasks = build_tasks_from_assignment(
        problem, assignment)
    relay_evaluator = RelayEvaluator(
        problem,
        relay_e1_tasks,
        relay_e2_tasks,
        TFp,
        vehicles_per_satellite=vehicles_per_satellite,
    )
    best_solution.task_to_phys = dict(relay_evaluator.task_to_phys)
    best_solution.transshipments = {}
    best_solution.cost = relay_evaluator.total_cost(best_solution)

    if not relay_evaluator.is_feasible(best_solution):
        if verbose:
            print("[WARN] 自由分仓解无法通过 Model 7 交接评估器复核")
        return _no_solution(
            return_info, time.time() - start_time,
            stage1_result.iterations, "AUDIT_FAILED")

    # Recalculate the phase-one value with the relay evaluator to make the
    # before/after accounting an exact identity under one cost implementation.
    free_assignment_cost = float(best_solution.cost)
    if verbose:
        print(f"阶段一完成: cost={best_solution.cost:.2f}, "
              f"换仓顾客={moved_customers}")
        print("阶段二: Model 6 异步空箱交接搜索")

    remaining = max(0.0, float(max_time) - (time.time() - start_time))
    requested_relay_budget = max(0.0, float(transshipment_time_budget))
    relay_budget = min(requested_relay_budget, remaining)
    transshipment_events = []
    relay_started = time.time()
    relay_target_hit_time = postprocess_transshipment(
        best_solution,
        relay_evaluator,
        verbose=verbose,
        time_budget=relay_budget,
        sample_pickups=transshipment_sample_pickups,
        sample_locations=transshipment_sample_locations,
        sample_carriers=transshipment_sample_carriers,
        position_top_k=transshipment_position_top_k,
        min_net_saving=transshipment_min_net_saving,
        max_transshipments=transshipment_max_total,
        max_transshipments_per_sat=transshipment_max_per_satellite,
        target_cost=target_objective,
        target_tolerance=target_tolerance,
        solver_start_time=start_time,
        audit_log=transshipment_events,
    )
    relay_elapsed = time.time() - relay_started
    best_solution.cost = relay_evaluator.total_cost(best_solution)
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
        "free_assignment_objective": float(free_assignment_cost),
        "transshipment_improvement": float(improvement),
        "runtime": float(elapsed),
        "iterations": int(stage1_result.iterations),
        "feasible": feasible,
        "status": "FEASIBLE" if feasible else "NO_SOLUTION",
        "assignment": dict(assignment),
        "moved_customers": int(moved_customers),
        "transshipment_count": len(best_solution.transshipments),
        "transshipment_events": transshipment_events,
        "stage1_time_ratio": ratio,
        "stage1_time_budget_seconds": float(stage1_limit),
        "transshipment_time_budget_requested": requested_relay_budget,
        "transshipment_time_budget_effective": float(relay_budget),
        "transshipment_search_elapsed_seconds": float(relay_elapsed),
        "best_found_time": stage1_result.best_found_time,
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
