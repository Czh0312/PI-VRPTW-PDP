"""Model 7 exact entry: assignment partitioning or logic-based Benders."""

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
        CUTOFF = 6
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


def _partition_relaxation_screen(problem, e1_tasks, e2_tasks, vehicles,
                                 *, TFp, cutoff, time_limit, gurobi_params=None):
    """Prove non-improvement with the metric zero-pickup-load relaxation.

    A no-relay M3 *upper* bound cannot exclude an M6 partition. Its relaxed
    lower bound can. Never use a relaxed incumbent as an M7 feasible route.
    """
    from gurobipy import GRB as SolverStatus
    from models.routing.variants.model_3.model import build_model
    from models.routing.variants.model_5.exact_solver import (
        _has_metric_distances, _zero_pickup_load_relaxation,
    )

    started = time.perf_counter()
    if not _has_metric_distances(problem, e1_tasks, e2_tasks):
        return None
    model, _variables, _ctx = build_model(
        problem, e1_tasks, _zero_pickup_load_relaxation(e2_tasks), vehicles,
        TFp=TFp)
    try:
        model.setParam("OutputFlag", 0)
        if gurobi_params:
            for name, value in gurobi_params.items():
                model.setParam(name, value)
        # This proof probe has its own objective threshold and time budget;
        # an unrelated caller Cutoff must not produce a false certificate.
        model.setParam("Cutoff", cutoff)
        model.setParam("MIPGap", 0.0)
        model.setParam("IntFeasTol", 1e-9)
        model.setParam("FeasibilityTol", 1e-9)
        remaining = time_limit - (time.perf_counter() - started)
        if remaining <= 0:
            return {"status": "TIME_LIMIT", "best_bound": None,
                    "runtime": time.perf_counter() - started, "pruned": False}
        model.setParam("TimeLimit", remaining)
        model.optimize()
        status = int(model.Status)
        bound = float(model.ObjBound)
        bound = bound if math.isfinite(bound) else None
        infeasible = status == SolverStatus.INFEASIBLE
        pruned = (infeasible or status == SolverStatus.CUTOFF
                  or (bound is not None and bound >= cutoff))
        if status == SolverStatus.CUTOFF:
            bound = max(cutoff, bound if bound is not None else -math.inf)
        return {
            "status": "INFEASIBLE" if infeasible else "CUTOFF" if pruned else "UNRESOLVED",
            "status_code": status, "best_bound": bound,
            "runtime": time.perf_counter() - started, "pruned": pruned,
            "cutoff": cutoff,
            "certificate_type": "metric_fixed_assignment_zero_pickup_load_bound",
        }
    finally:
        model.dispose()


def _solve_partition(problem, e1_tasks, e2_tasks, vehicles, *, screen=False,
                     screen_time_limit=1.0, incumbent=math.inf, **kwargs):
    """Screen one partition, retaining the full handoff MIP as fallback."""
    started = time.perf_counter()
    allotted = kwargs.get("time_limit")
    probe = None
    if screen and math.isfinite(incumbent):
        probe_time = screen_time_limit
        if allotted is not None:
            # The compact handoff MILP has a much weaker relaxation and very
            # expensive nodes.  Give the proof relaxation enough of a normal
            # five-second partition slice to finish hard incumbent partitions,
            # while retaining two seconds for a better-incumbent search when
            # the relaxation cannot prune.
            probe_time = min(probe_time, allotted * 0.6)
        if probe_time > 0:
            probe = _partition_relaxation_screen(
                problem, e1_tasks, e2_tasks, vehicles, TFp=kwargs.get("TFp", 9999.0),
                cutoff=incumbent - 1e-9, time_limit=probe_time,
                gurobi_params=kwargs.get("gurobi_params"))
    if probe is not None and probe["pruned"]:
        return None, None, {
            "status": probe["status"], "proven_optimal": False, "objective": None,
            "best_bound": probe["best_bound"], "runtime": time.perf_counter() - started,
            "transshipment_count": 0, "partition_relaxation": probe,
        }
    if probe is not None:
        # Do not rerun the two-solve M6 certificate after this lower-bound
        # probe. Spend the rest of this partition slice on the complete MIP.
        kwargs["use_relaxation_certificate"] = False
        if allotted is not None:
            kwargs["time_limit"] = max(0.0, allotted - (time.perf_counter() - started))
            if kwargs["time_limit"] <= 1e-6:
                return None, None, {"status": "TIME_LIMIT", "proven_optimal": False,
                    "objective": None, "best_bound": probe["best_bound"],
                    "runtime": time.perf_counter() - started,
                    "transshipment_count": 0, "partition_relaxation": probe}
    e1, e2, info = _solve_fixed_assignment(problem, e1_tasks, e2_tasks, vehicles, **kwargs)
    if probe is not None:
        info = dict(info)
        bounds = [float(value) for value in (probe["best_bound"], info.get("best_bound"))
                  if value is not None and math.isfinite(float(value))]
        info["best_bound"] = max(bounds) if bounds else None
        info["runtime"] = time.perf_counter() - started
        info["partition_relaxation"] = probe
    return e1, e2, info


def _global_bound(partition_summaries, all_partitions_started,
                  global_relaxation_bound=None):
    bounds = []
    for summary in partition_summaries:
        candidate = summary.get("best_bound")
        if candidate is not None and math.isfinite(float(candidate)):
            bounds.append(float(candidate))
        elif summary.get("status") != "INFEASIBLE":
            # A started but unresolved partition without a solver bound still
            # has the nonnegative-distance lower bound zero.  Omitting it would
            # overstate the global bound once every partition has been started.
            bounds.append(0.0)
    # Distance is nonnegative. Unvisited partitions have no stronger valid
    # lower bound, so zero must participate in the global minimum.
    if not all_partitions_started:
        bounds.append(0.0)
    partition_bound = min(bounds) if bounds else 0.0
    if (global_relaxation_bound is not None
            and math.isfinite(float(global_relaxation_bound))):
        # This bound is valid for the complete free-assignment relaxation, so
        # it strengthens even partitions that were never started.
        return max(partition_bound, float(global_relaxation_bound))
    return partition_bound


def _assignment_early_due_conflicts(problem, assignment, tolerance=1e-9):
    """Return proof-safe direct-travel conflicts for one assignment."""
    speed = float(problem.speed)
    depot_id = problem.depot.id
    conflicts = []
    for customer in problem.customers:
        sat_id = assignment.get(customer.id)
        if sat_id is None:
            continue
        due = float(customer.tw_del[1])
        if not math.isfinite(due):
            continue
        earliest = (
            problem.distance(depot_id, sat_id)
            + problem.distance(sat_id, customer.id)
        ) / speed
        if earliest > due + tolerance:
            conflicts.append({
                "customer": customer.id,
                "assigned_satellite": sat_id,
                "earliest_direct_delivery": float(earliest),
                "delivery_due": due,
                "lateness_lower_bound": float(earliest - due),
            })
    return conflicts


def _global_relaxation_certificate(
    problem,
    vehicles_per_satellite,
    TFp,
    remaining_time,
    gurobi_params,
    formulation_options,
    verbose,
    warm_start_routes=None,
    warm_start_assignment=None,
    warm_start_objective=None,
    direct_time_fraction=0.35,
):
    """Try to close Model 7 with a metric zero-container-load bound.

    The direct M4 solution is returned even when the certificate does not
    close.  It is a valid zero-handoff Model 7 incumbent and must not be lost
    merely because the relaxed lower-bound solve uses the remaining budget.
    """
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
    has_external_incumbent = (
        warm_start_routes is not None
        and warm_start_assignment is not None
        and warm_start_objective is not None
    )
    if has_external_incumbent:
        # The caller already solved and audited M4. Re-solving the same upper
        # bound can starve the more valuable lower-bound certificate.
        direct_e1, direct_e2 = warm_start_routes
        direct_assignment = dict(warm_start_assignment)
        direct_info = {
            "status": "EXTERNAL_INCUMBENT",
            "objective": float(warm_start_objective),
            "proven_optimal": False,
        }
    else:
        initial_certificate_time = remaining_time()
        direct_time = initial_certificate_time
        if initial_certificate_time is not None:
            direct_time = max(
                0.0,
                initial_certificate_time * float(direct_time_fraction),
            )
        direct_e1, direct_e2, direct_assignment, direct_info = solve_m4(
            problem,
            vehicles_per_satellite,
            TFp=TFp,
            time_limit=direct_time,
            verbose=False,
            return_info=True,
            return_incumbent_on_time_limit=True,
            gurobi_params=gurobi_params,
            formulation_options=formulation_options,
            warm_start_routes=warm_start_routes,
        )
    incumbent = direct_info.get("objective")

    relaxed_time = remaining_time()
    lower_info = None
    lower_bound = None
    if relaxed_time is not None and relaxed_time <= 1e-6:
        return {
            "routes_e1": direct_e1,
            "routes_e2": direct_e2,
            "assignment": direct_assignment,
            "objective": incumbent,
            "lower_bound": None,
            "certified": False,
            "direct_info": direct_info,
            "lower_info": None,
        }

    relaxed_problem = copy.deepcopy(problem)
    relaxed_problem.fleet.container_weight = 0.0
    relaxed_problem.fleet.container_volume = 0.0
    warm_start = (
        (direct_e1, direct_e2)
        if direct_e1 is not None and direct_e2 is not None else None
    )
    _lb_e1, _lb_e2, _lb_assignment, lower_info = solve_m4(
        relaxed_problem,
        vehicles_per_satellite,
        TFp=TFp,
        time_limit=remaining_time(),
        verbose=False,
        return_info=True,
        return_incumbent_on_time_limit=True,
        warm_start_routes=warm_start,
        gurobi_params=gurobi_params,
        formulation_options=formulation_options,
    )
    certified_lower_bound = (
        lower_info.get("objective")
        if lower_info.get("proven_optimal") else None
    )
    diagnostic_bound = lower_info.get("best_bound")
    if (diagnostic_bound is not None
            and math.isfinite(float(diagnostic_bound))):
        lower_bound = float(diagnostic_bound)
    if certified_lower_bound is not None:
        lower_bound = max(
            float(certified_lower_bound),
            float(lower_bound) if lower_bound is not None else -math.inf,
        )
    tolerance = 1e-6 * max(1.0, abs(float(incumbent or 0.0)))
    certified = (
        incumbent is not None
        and certified_lower_bound is not None
        and float(certified_lower_bound) >= float(incumbent) - tolerance
    )

    return {
        "routes_e1": direct_e1,
        "routes_e2": direct_e2,
        "assignment": direct_assignment,
        "objective": float(incumbent) if incumbent is not None else None,
        "lower_bound": lower_bound,
        "certified": certified,
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
    global_certificate_time_fraction=0.5,
    global_certificate_direct_fraction=0.35,
    partition_sweep_threshold=128,
    partition_time_limit=5.0,
    partition_relaxation_screen=True,
    partition_relaxation_time_limit=3.0,
    warm_start_routes=None,
    warm_start_assignment=None,
    warm_start_objective=None,
    algorithm="partition",
    benders_options=None,
):
    """Solve the complete free-assignment/asynchronous-relay Model 7.

    The input task buckets are ignored because assignment is endogenous.
    ``algorithm='partition'`` uses enumeration and relaxation certificates;
    ``algorithm='benders'`` selects assignments through a lower-bound master.
    ``benders_options`` configures its master and subproblem time slices.
    """
    if algorithm == "benders":
        from .benders import solve_benders

        return solve_benders(
            problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
            TFp=TFp, time_limit=time_limit, verbose=verbose,
            return_info=return_info,
            return_incumbent_on_time_limit=return_incumbent_on_time_limit,
            gurobi_params=gurobi_params, strengthen=strengthen,
            use_relaxation_certificate=use_relaxation_certificate,
            partition_relaxation_screen=partition_relaxation_screen,
            warm_start_routes=warm_start_routes,
            warm_start_assignment=warm_start_assignment,
            warm_start_objective=warm_start_objective,
            **(benders_options or {}),
        )
    if algorithm != "partition":
        raise ValueError(f"Unknown Model 7 exact algorithm: {algorithm}")
    if benders_options:
        raise ValueError("benders_options requires algorithm='benders'")
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
    global_relaxation_bound = None
    incumbent_source = None

    if warm_start_routes is not None:
        if warm_start_assignment is None or warm_start_objective is None:
            raise ValueError(
                "Model 7 external warm start requires routes, assignment, "
                "and objective together"
            )
        warm_e1, warm_e2 = warm_start_routes
        if warm_e1 is None or warm_e2 is None:
            raise ValueError("Model 7 external warm-start routes cannot be None")
        best_routes = (warm_e1, warm_e2)
        best_assignment = dict(warm_start_assignment)
        best_objective = float(warm_start_objective)
        best_info = {
            "objective": best_objective,
            "transshipments": [],
            "transshipment_count": 0,
            "transferred_container_count": 0,
            "incumbent_source": "external_m4",
        }
        incumbent_source = "external_m4"

    def remaining_time():
        if time_limit is None:
            return None
        return max(0.0, float(time_limit) - (time.perf_counter() - started))

    if verbose:
        print("\n" + "=" * 60)
        print("Model 7 联合精确求解：完整分仓枚举 + Model 6 联合 MILP")
        print(f"完整分仓数: {total}")
        print("=" * 60)

    if not 0.0 <= float(global_certificate_time_fraction) <= 1.0:
        raise ValueError("global_certificate_time_fraction must be in [0, 1]")
    if not 0.0 <= float(global_certificate_direct_fraction) <= 1.0:
        raise ValueError("global_certificate_direct_fraction must be in [0, 1]")
    if partition_time_limit is not None and float(partition_time_limit) <= 0:
        raise ValueError("partition_time_limit must be positive or None")
    if float(partition_relaxation_time_limit) <= 0:
        raise ValueError("partition_relaxation_time_limit must be positive")
    if int(partition_sweep_threshold) < 0:
        raise ValueError("partition_sweep_threshold must be nonnegative")

    # With an audited incumbent, small assignment spaces are certified faster
    # by fixed-assignment relaxation screens than by spending half the budget
    # on the much larger free-assignment relaxation.  Keep the global route for
    # large spaces, where exhaustive screening is not competitive.
    prefer_partition_sweep = (
        partition_relaxation_screen
        and warm_start_routes is not None
        and warm_start_assignment is not None
        and warm_start_objective is not None
        and total <= int(partition_sweep_threshold)
    )
    global_certificate_attempted = False

    if (use_global_relaxation_certificate
            and not prefer_partition_sweep
            and (time_limit is None
                 or global_certificate_time_fraction > 0.0)):
        global_certificate_attempted = True
        certificate_budget = (
            None if time_limit is None
            else remaining_time() * float(global_certificate_time_fraction)
        )
        certificate_deadline = (
            None if certificate_budget is None
            else time.perf_counter() + certificate_budget
        )

        def certificate_remaining_time():
            global_remaining = remaining_time()
            if certificate_deadline is None:
                return global_remaining
            certificate_remaining = max(
                0.0, certificate_deadline - time.perf_counter())
            if global_remaining is None:
                return certificate_remaining
            return min(global_remaining, certificate_remaining)

        certificate = _global_relaxation_certificate(
            problem,
            vehicles_per_satellite,
            TFp,
            certificate_remaining_time,
            gurobi_params,
            m4_formulation_options or {"prune_e2_arcs": True},
            verbose,
            warm_start_routes=warm_start_routes,
            warm_start_assignment=warm_start_assignment,
            warm_start_objective=warm_start_objective,
            direct_time_fraction=global_certificate_direct_fraction,
        )
        if certificate is not None:
            global_relaxation_bound = certificate.get("lower_bound")
        if certificate is not None and certificate.get("certified"):
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
                "assignment_partition_retries": 0,
                "assignment_partitions_unresolved": 0,
                "weakest_unresolved_bound": None,
                "weakest_unresolved_assignment": None,
                "weakest_unresolved_status": None,
                "partition_summaries": [],
                "partition_relaxation_screened": 0,
                "partition_relaxation_pruned": 0,
                "transshipments": [],
                "transshipment_count": 0,
                "transferred_container_count": 0,
                "relay_policy": (
                    "homogeneous_integer_single_async_handoff_at_existing_"
                    "service_location_with_endogenous_assignment"
                ),
                "external_m4_incumbent": warm_start_routes is not None,
                "incumbent_source": (
                    "external_m4_certificate"
                    if warm_start_routes is not None
                    else "internal_m4_certificate"
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

        # A direct M4 route is feasible in M7 with zero handoffs.  Preserve it
        # as the initial incumbent even if the relaxation did not certify it.
        if (certificate is not None
                and certificate.get("routes_e1") is not None
                and certificate.get("routes_e2") is not None
                and certificate.get("assignment") is not None
                and certificate.get("objective") is not None
                and float(certificate["objective"]) < best_objective - 1e-9):
            best_routes = (
                certificate["routes_e1"], certificate["routes_e2"])
            best_assignment = dict(certificate["assignment"])
            best_objective = float(certificate["objective"])
            best_info = dict(certificate.get("direct_info") or {})
            best_info["incumbent_source"] = "internal_m4"
            incumbent_source = "internal_m4"

    preferred_assignments = (
        [best_assignment] if best_assignment is not None else None
    )
    for index, assignment in enumerate(
            iter_assignments(problem, preferred_assignments), start=1):
        remaining = remaining_time()
        if remaining is not None and remaining <= 1e-6:
            interrupted = True
            break

        due_conflicts = _assignment_early_due_conflicts(problem, assignment)
        if due_conflicts:
            evaluated += 1
            resolved += 1
            proven_infeasible += 1
            summaries.append({
                "index": index,
                "assignment": dict(assignment),
                "status": "INFEASIBLE",
                "proven_optimal": False,
                "objective": None,
                "best_bound": None,
                "runtime": 0.0,
                "transshipment_count": 0,
                "presolve_certificate": {
                    "type": "earliest_direct_delivery_exceeds_due_time",
                    "conflicts": due_conflicts,
                },
            })
            if verbose:
                print(f"[{index}/{total}] status=INFEASIBLE "
                      "(direct-time certificate)")
            continue

        fixed_e1, fixed_e2 = build_assignment_subproblem(problem, assignment)
        subproblem_time = remaining
        if subproblem_time is not None and partition_time_limit is not None:
            subproblem_time = min(
                subproblem_time, float(partition_time_limit))
        partition_params = dict(gurobi_params or {})
        if (math.isfinite(best_objective)
                and "Cutoff" not in partition_params):
            partition_params["Cutoff"] = best_objective - 1e-9
        routes_e1, routes_e2, sub_info = _solve_partition(
            problem,
            fixed_e1,
            fixed_e2,
            vehicles_per_satellite,
            screen=partition_relaxation_screen,
            screen_time_limit=float(partition_relaxation_time_limit),
            incumbent=best_objective,
            TFp=TFp,
            verbose=False,
            time_limit=subproblem_time,
            mip_gap=0.0,
            return_incumbent_on_time_limit=True,
            return_info=True,
            gurobi_params=partition_params,
            strengthen=strengthen,
            compute_iis_on_infeasible=False,
            use_relaxation_certificate=use_relaxation_certificate,
        )
        evaluated += 1
        status = sub_info.get("status")
        sub_proven = bool(sub_info.get("proven_optimal"))
        sub_infeasible = status == "INFEASIBLE"
        sub_cutoff = status == "CUTOFF"
        if sub_proven or sub_infeasible or sub_cutoff:
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
            "partition_relaxation": sub_info.get("partition_relaxation"),
        }
        summaries.append(summary)

        if (routes_e1 is not None and routes_e2 is not None
                and objective is not None
                and float(objective) < best_objective - 1e-9):
            best_objective = float(objective)
            best_routes = (routes_e1, routes_e2)
            best_assignment = dict(assignment)
            best_info = dict(sub_info)
            best_info["incumbent_source"] = "model7_partition"
            incumbent_source = "model7_partition"

        if verbose:
            incumbent_text = (
                f"{best_objective:.6f}"
                if math.isfinite(best_objective) else "none"
            )
            print(
                f"[{index}/{total}] status={status}, objective={objective}, "
                f"global_incumbent={incumbent_text}"
            )

        # Do not let one difficult partition monopolize a finite proof budget.
        # Its valid lower bound remains in ``summaries`` while later partitions
        # can still improve the incumbent.  With no global time limit, retain
        # the former fail-fast behaviour because an unresolved exact subproblem
        # is then an exceptional solver condition rather than normal slicing.
        if (not (sub_proven or sub_infeasible or sub_cutoff)
                and time_limit is None):
            interrupted = True
            break

    # A finite first pass deliberately gives every partition only a small
    # slice so that one hard assignment cannot hide later improving ones.
    # When every assignment has been visited, spend any residual budget on a
    # proof-oriented second pass over unresolved partitions, weakest bound
    # first.  Previously this residual time was discarded (notably on 7-customer
    # cases where all 128 partitions had been visited but one remained open).
    partition_retries = 0
    if (time_limit is not None and evaluated == total
            and remaining_time() > 1e-6):
        resolved_statuses = {"OPTIMAL", "INFEASIBLE", "CUTOFF"}

        def _retry_priority(summary):
            candidate = summary.get("best_bound")
            if candidate is None or not math.isfinite(float(candidate)):
                return -math.inf
            return float(candidate)

        def _retry_partition(summary_index, retry_time):
            nonlocal best_objective, best_routes, best_assignment
            nonlocal best_info, incumbent_source, partition_retries
            summary = summaries[summary_index]
            assignment = summary["assignment"]
            fixed_e1, fixed_e2 = build_assignment_subproblem(
                problem, assignment)
            partition_params = dict(gurobi_params or {})
            if (math.isfinite(best_objective)
                    and "Cutoff" not in partition_params):
                partition_params["Cutoff"] = best_objective - 1e-9
            routes_e1, routes_e2, sub_info = _solve_fixed_assignment(
                problem,
                fixed_e1,
                fixed_e2,
                vehicles_per_satellite,
                TFp=TFp,
                verbose=False,
                time_limit=retry_time,
                mip_gap=0.0,
                return_incumbent_on_time_limit=True,
                return_info=True,
                gurobi_params=partition_params,
                strengthen=strengthen,
                compute_iis_on_infeasible=False,
                # The first pass already ran the fixed-assignment certificate.
                # Repeating it would spend half the retry on the same two M3
                # solves, so devote the retry entirely to the compact handoff
                # MIP and its bound.
                use_relaxation_certificate=False,
            )
            partition_retries += 1
            status = sub_info.get("status")
            objective = sub_info.get("objective")
            if (routes_e1 is not None and routes_e2 is not None
                    and objective is not None
                    and float(objective) < best_objective - 1e-9):
                best_objective = float(objective)
                best_routes = (routes_e1, routes_e2)
                best_assignment = dict(assignment)
                best_info = dict(sub_info)
                best_info["incumbent_source"] = "model7_partition_retry"
                incumbent_source = "model7_partition_retry"

            old_bound = summary.get("best_bound")
            new_bound = sub_info.get("best_bound")
            valid_bounds = [
                float(value) for value in (old_bound, new_bound)
                if value is not None and math.isfinite(float(value))
            ]
            old_objective = summary.get("objective")
            valid_objectives = [
                float(value) for value in (old_objective, objective)
                if value is not None and math.isfinite(float(value))
            ]
            summary.update({
                "status": status,
                "proven_optimal": bool(sub_info.get("proven_optimal")),
                "objective": min(valid_objectives) if valid_objectives else None,
                "best_bound": max(valid_bounds) if valid_bounds else None,
                "runtime": (
                    float(summary.get("runtime") or 0.0)
                    + float(sub_info.get("runtime") or 0.0)
                ),
                "transshipment_count": sub_info.get(
                    "transshipment_count",
                    summary.get("transshipment_count", 0),
                ),
                "attempts": int(summary.get("attempts", 1)) + 1,
            })
            return status in resolved_statuses

        # Repeat rounds while solvers make material progress or consume their
        # allotted time.  A quick no-progress guard keeps mocked or immediately
        # failing solvers from spinning forever.
        while remaining_time() > 1e-6:
            unresolved_indices = [
                idx for idx, summary in enumerate(summaries)
                if summary.get("status") not in resolved_statuses
            ]
            if not unresolved_indices:
                break
            unresolved_indices.sort(
                key=lambda idx: _retry_priority(summaries[idx]))
            round_started = time.perf_counter()
            newly_resolved = 0
            for retry_position, summary_index in enumerate(
                    unresolved_indices, start=1):
                remaining = remaining_time()
                if remaining <= 1e-6:
                    break
                partitions_left = (
                    len(unresolved_indices) - retry_position + 1)
                fair_share = remaining / max(1, partitions_left)
                retry_time = remaining
                if partition_time_limit is not None:
                    retry_time = min(
                        remaining,
                        max(float(partition_time_limit), fair_share),
                    )
                newly_resolved += int(_retry_partition(
                    summary_index, retry_time))
            if (newly_resolved == 0
                    and time.perf_counter() - round_started < 0.01):
                break

    # Recompute proof counters because a retry can close a formerly unresolved
    # partition without changing the number of unique assignments evaluated.
    resolved = sum(
        summary.get("status") in {"OPTIMAL", "INFEASIBLE", "CUTOFF"}
        for summary in summaries
    )
    proven_feasible = sum(
        bool(summary.get("proven_optimal")) for summary in summaries
    )
    proven_infeasible = sum(
        summary.get("status") == "INFEASIBLE" for summary in summaries
    )

    complete = evaluated == total and resolved == total and not interrupted
    globally_optimal = complete and best_routes is not None
    globally_infeasible = complete and best_routes is None
    elapsed = time.perf_counter() - started
    bound = (
        best_objective
        if globally_optimal
        else _global_bound(
            summaries, evaluated == total, global_relaxation_bound)
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

    unresolved_summaries = [
        summary for summary in summaries
        if summary.get("status") not in {"OPTIMAL", "INFEASIBLE", "CUTOFF"}
    ]

    def _summary_bound(summary):
        candidate = summary.get("best_bound")
        if candidate is None or not math.isfinite(float(candidate)):
            return -math.inf
        return float(candidate)

    weakest_unresolved = (
        min(unresolved_summaries, key=_summary_bound)
        if unresolved_summaries else None
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
        "assignment_partition_retries": int(partition_retries),
        "assignment_partitions_unresolved": len(unresolved_summaries),
        "weakest_unresolved_bound": (
            None if weakest_unresolved is None
            else weakest_unresolved.get("best_bound")
        ),
        "weakest_unresolved_assignment": (
            None if weakest_unresolved is None
            else dict(weakest_unresolved["assignment"])
        ),
        "weakest_unresolved_status": (
            None if weakest_unresolved is None
            else weakest_unresolved.get("status")
        ),
        "partition_summaries": summaries,
        "partition_relaxation_screened": sum(
            summary.get("partition_relaxation") is not None for summary in summaries),
        "partition_relaxation_pruned": sum(
            bool((summary.get("partition_relaxation") or {}).get("pruned"))
            for summary in summaries),
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
        "external_m4_incumbent": warm_start_routes is not None,
        "incumbent_source": incumbent_source,
        "global_relaxation_certificate_attempted": global_certificate_attempted,
        "proof_time_strategy": (
            "fixed_assignment_relaxation_sweep"
            if prefer_partition_sweep
            else "global_relaxation_then_partition_sweep"
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
