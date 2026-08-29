"""Globally exact solver for Model 7 by exhaustive assignment partitioning."""

from __future__ import annotations

import math
import time
import copy

try:
    from gurobipy import GRB
except ModuleNotFoundError:  # Keeps decomposition logic testable without Gurobi.
    class GRB:  # type: ignore[no-redef]
        OPTIMAL = 2
        INFEASIBLE = 3
        TIME_LIMIT = 9

from .model import (
    assignment_count,
    build_assignment_subproblem,
    iter_assignments,
)


def _solve_fixed_assignment(*args, **kwargs):
    """Load Model 6 lazily; kept separate for exact-decomposition tests."""
    from models.routing.variants.model_6.exact_solver import solve_exact

    return solve_exact(*args, **kwargs)


def _global_bound(partition_summaries, all_partitions_started):
    bounds = [
        summary["best_bound"]
        for summary in partition_summaries
        if summary.get("best_bound") is not None
        and math.isfinite(float(summary["best_bound"]))
    ]
    # Distance is nonnegative. Unvisited partitions have no stronger valid
    # lower bound, so zero must participate in the global minimum.
    if not all_partitions_started:
        bounds.append(0.0)
    return min(bounds) if bounds else 0.0


def _global_relaxation_certificate(
    problem,
    vehicles_per_satellite,
    TFp,
    remaining_time,
    gurobi_params,
    formulation_options,
    verbose,
):
    """Try to close Model 7 with a metric zero-container-load bound."""
    from models.routing.variants.model_4.exact_solver import solve_exact as solve_m4
    from models.routing.variants.model_5.exact_solver import _has_metric_distances

    original_e1, original_e2 = build_assignment_subproblem(
        problem,
        {customer.id: customer.assigned_sat for customer in problem.customers},
    )
    if not _has_metric_distances(problem, original_e1, original_e2):
        return None

    if verbose:
        print("[CERT] 求自由分仓无交接上界与零容器载荷全局下界")
    direct_e1, direct_e2, direct_assignment, direct_info = solve_m4(
        problem,
        vehicles_per_satellite,
        TFp=TFp,
        time_limit=remaining_time(),
        verbose=False,
        return_info=True,
        return_incumbent_on_time_limit=True,
        gurobi_params=gurobi_params,
        formulation_options=formulation_options,
    )
    incumbent = direct_info.get("objective")
    if direct_e1 is None or direct_e2 is None or incumbent is None:
        return None

    relaxed_problem = copy.deepcopy(problem)
    relaxed_problem.fleet.container_weight = 0.0
    relaxed_problem.fleet.container_volume = 0.0
    _lb_e1, _lb_e2, _lb_assignment, lower_info = solve_m4(
        relaxed_problem,
        vehicles_per_satellite,
        TFp=TFp,
        time_limit=remaining_time(),
        verbose=False,
        return_info=True,
        return_incumbent_on_time_limit=True,
        warm_start_routes=(direct_e1, direct_e2),
        gurobi_params=gurobi_params,
        formulation_options=formulation_options,
    )
    lower_bound = (
        lower_info.get("objective")
        if lower_info.get("proven_optimal") else None
    )
    tolerance = 1e-6 * max(1.0, abs(float(incumbent)))
    if lower_bound is None or float(lower_bound) < float(incumbent) - tolerance:
        return None

    return {
        "routes_e1": direct_e1,
        "routes_e2": direct_e2,
        "assignment": direct_assignment,
        "objective": float(incumbent),
        "lower_bound": float(lower_bound),
        "direct_info": direct_info,
        "lower_info": lower_info,
    }


def solve_exact(
    problem,
    e1_tasks=None,
    e2_tasks_by_sat=None,
    vehicles_per_satellite=None,
    TFp=9999.0,
    time_limit=300,
    verbose=True,
    return_info=False,
    return_incumbent_on_time_limit=False,
    gurobi_params=None,
    strengthen=True,
    use_relaxation_certificate=True,
    use_global_relaxation_certificate=True,
    m4_formulation_options=None,
):
    """Solve the complete free-assignment/asynchronous-relay Model 7.

    The input task buckets are ignored because assignment is endogenous.  A
    global certificate is issued only after every assignment partition is
    proven optimal or proven infeasible.  Otherwise any returned route is a
    time-limited incumbent and ``proven_optimal`` remains false.
    """
    del e1_tasks, e2_tasks_by_sat
    if vehicles_per_satellite is None:
        vehicles_per_satellite = problem.fleet.e2_per_sat_count

    started = time.perf_counter()
    total = assignment_count(problem)
    best_routes = None
    best_assignment = None
    best_info = None
    best_objective = float("inf")
    summaries = []
    evaluated = 0
    resolved = 0
    proven_feasible = 0
    proven_infeasible = 0
    interrupted = False

    def remaining_time():
        if time_limit is None:
            return None
        return max(0.0, float(time_limit) - (time.perf_counter() - started))

    if verbose:
        print("\n" + "=" * 60)
        print("Model 7 联合精确求解：完整分仓枚举 + Model 6 联合 MILP")
        print(f"完整分仓数: {total}")
        print("=" * 60)

    if use_global_relaxation_certificate:
        certificate = _global_relaxation_certificate(
            problem,
            vehicles_per_satellite,
            TFp,
            remaining_time,
            gurobi_params,
            m4_formulation_options or {"prune_e2_arcs": True},
            verbose,
        )
        if certificate is not None:
            elapsed = time.perf_counter() - started
            info = {
                "status_code": int(GRB.OPTIMAL),
                "status": "OPTIMAL",
                "solution_type": "proven_optimal",
                "proven_optimal": True,
                "proof_scope": (
                    "metric_zero_container_load_global_certificate_model7"
                ),
                "formulation": (
                    "free_assignment_zero_container_load_relaxation_certificate"
                ),
                "runtime": float(elapsed),
                "sol_count": 1,
                "objective": certificate["objective"],
                "best_bound": certificate["lower_bound"],
                "mip_gap": 0.0,
                "assignment": certificate["assignment"],
                "assignment_partitions_total": int(total),
                "assignment_partitions_evaluated": 0,
                "assignment_partitions_resolved": 0,
                "assignment_partitions_proven_feasible": 0,
                "assignment_partitions_proven_infeasible": 0,
                "partition_summaries": [],
                "transshipments": [],
                "transshipment_count": 0,
                "transferred_container_count": 0,
                "relay_policy": (
                    "homogeneous_integer_single_async_handoff_at_existing_"
                    "service_location_with_endogenous_assignment"
                ),
                "certificate": {
                    "type": "metric_free_assignment_zero_container_load",
                    "direct_status": certificate["direct_info"].get("status"),
                    "relaxation_status": certificate["lower_info"].get("status"),
                    "lower_bound": certificate["lower_bound"],
                },
            }
            if verbose:
                print(
                    f"[CERTIFIED] LB=UB={certificate['objective']:.6f}，"
                    "Model 7 已严格证明最优"
                )
            if return_info:
                return (
                    certificate["routes_e1"], certificate["routes_e2"],
                    certificate["assignment"], info,
                )
            return (
                certificate["routes_e1"], certificate["routes_e2"],
                certificate["assignment"],
            )

    for index, assignment in enumerate(iter_assignments(problem), start=1):
        remaining = remaining_time()
        if remaining is not None and remaining <= 1e-6:
            interrupted = True
            break

        fixed_e1, fixed_e2 = build_assignment_subproblem(problem, assignment)
        routes_e1, routes_e2, sub_info = _solve_fixed_assignment(
            problem,
            fixed_e1,
            fixed_e2,
            vehicles_per_satellite,
            TFp=TFp,
            verbose=False,
            time_limit=remaining,
            mip_gap=0.0,
            return_incumbent_on_time_limit=True,
            return_info=True,
            gurobi_params=gurobi_params,
            strengthen=strengthen,
            compute_iis_on_infeasible=False,
            use_relaxation_certificate=use_relaxation_certificate,
        )
        evaluated += 1
        status = sub_info.get("status")
        sub_proven = bool(sub_info.get("proven_optimal"))
        sub_infeasible = status == "INFEASIBLE"
        if sub_proven or sub_infeasible:
            resolved += 1
        if sub_proven:
            proven_feasible += 1
        if sub_infeasible:
            proven_infeasible += 1

        objective = sub_info.get("objective")
        summary = {
            "index": index,
            "assignment": dict(assignment),
            "status": status,
            "proven_optimal": sub_proven,
            "objective": objective,
            "best_bound": sub_info.get("best_bound"),
            "runtime": sub_info.get("runtime"),
            "transshipment_count": sub_info.get("transshipment_count", 0),
        }
        summaries.append(summary)

        if (routes_e1 is not None and routes_e2 is not None
                and objective is not None
                and float(objective) < best_objective - 1e-9):
            best_objective = float(objective)
            best_routes = (routes_e1, routes_e2)
            best_assignment = dict(assignment)
            best_info = dict(sub_info)

        if verbose:
            incumbent_text = (
                f"{best_objective:.6f}"
                if math.isfinite(best_objective) else "none"
            )
            print(
                f"[{index}/{total}] status={status}, objective={objective}, "
                f"global_incumbent={incumbent_text}"
            )

        if not (sub_proven or sub_infeasible):
            interrupted = True
            break

    complete = evaluated == total and resolved == total and not interrupted
    globally_optimal = complete and best_routes is not None
    globally_infeasible = complete and best_routes is None
    elapsed = time.perf_counter() - started
    bound = (
        best_objective
        if globally_optimal
        else _global_bound(summaries, evaluated == total)
    )
    gap = None
    if best_routes is not None:
        gap = max(0.0, (best_objective - bound)
                  / max(abs(best_objective), 1e-10))

    if globally_optimal:
        status = "OPTIMAL"
        status_code = int(GRB.OPTIMAL)
        solution_type = "proven_optimal"
    elif globally_infeasible:
        status = "INFEASIBLE"
        status_code = int(GRB.INFEASIBLE)
        solution_type = "proven_infeasible"
    else:
        status = "TIME_LIMIT"
        status_code = int(GRB.TIME_LIMIT)
        solution_type = (
            "feasible_not_proven" if best_routes is not None else "none"
        )

    info = {
        "status_code": status_code,
        "status": status,
        "solution_type": solution_type,
        "proven_optimal": globally_optimal,
        "proof_scope": (
            "exhaustive_free_assignment_partition_plus_"
            "compact_async_event_handoff_model7"
        ),
        "formulation": (
            "complete_free_assignment_partition_of_compact_"
            "homogeneous_async_event_arc_flow"
        ),
        "runtime": float(elapsed),
        "sol_count": 1 if best_routes is not None else 0,
        "objective": (
            float(best_objective) if best_routes is not None else None
        ),
        "best_bound": float(bound) if bound is not None else None,
        "mip_gap": float(gap) if gap is not None else None,
        "assignment": best_assignment,
        "assignment_partitions_total": int(total),
        "assignment_partitions_evaluated": int(evaluated),
        "assignment_partitions_resolved": int(resolved),
        "assignment_partitions_proven_feasible": int(proven_feasible),
        "assignment_partitions_proven_infeasible": int(proven_infeasible),
        "partition_summaries": summaries,
        "transshipments": (
            list((best_info or {}).get("transshipments", []))
        ),
        "transshipment_count": int(
            (best_info or {}).get("transshipment_count", 0)
        ),
        "transferred_container_count": int(
            (best_info or {}).get("transferred_container_count", 0)
        ),
        "relay_policy": (
            "homogeneous_integer_single_async_handoff_at_existing_"
            "service_location_with_endogenous_assignment"
        ),
    }

    can_return = globally_optimal or (
        best_routes is not None and return_incumbent_on_time_limit
    )
    if verbose:
        print(
            f"Model 7 exact 完成: status={status}, "
            f"objective={info['objective']}, bound={info['best_bound']}, "
            f"partitions={evaluated}/{total}, proven={globally_optimal}"
        )

    routes_e1 = best_routes[0] if can_return else None
    routes_e2 = best_routes[1] if can_return else None
    assignment_out = best_assignment if can_return else None
    if return_info:
        return routes_e1, routes_e2, assignment_out, info
    return routes_e1, routes_e2, assignment_out


def solve_mip(problem, vehicles_per_satellite=None, **kwargs):
    """Return the best Model 7 incumbent even without a global certificate."""
    kwargs["return_incumbent_on_time_limit"] = True
    return solve_exact(
        problem,
        vehicles_per_satellite=vehicles_per_satellite,
        **kwargs,
    )
