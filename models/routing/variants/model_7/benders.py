"""Logic-based Benders: assignment master and complete Model 6 recourse.

The default master retains delivery/pickup service routing and relaxes E2
pickup loads. Integer container handoffs remain in the fixed-assignment subproblem.
All objective values use the same total E1 + E2 distance convention.
"""
from __future__ import annotations

import math
import time
import copy

import gurobipy as gp
import numpy as np
from gurobipy import GRB

from .model import assignment_count, build_assignment_subproblem


def _shortest_paths(problem, getter):
    """Directed metric closure, also valid for supplied nonmetric matrices."""
    ids = list(problem.nodes)
    matrix = np.array([[getter(i, j) for j in ids] for i in ids], dtype=float)
    if not np.isfinite(matrix).all() or (matrix < 0.0).any():
        raise ValueError("Benders requires finite nonnegative distances and travel times")
    np.fill_diagonal(matrix, 0.0)
    for k in range(len(ids)):
        matrix = np.minimum(matrix, matrix[:, k, None] + matrix[None, k, :])
    return {(i, j): float(matrix[a, b])
            for a, i in enumerate(ids) for b, j in enumerate(ids)}


def _routing_relaxation(model, root, visits, loads, capacities, fleet_size,
                        distance, name):
    """Continuous degree/commodity-flow relaxation of delivery-only routes.

    Delete pickup/meet-only visits and shortcut a complete route to obtain a
    feasible point here. Connectivity flow also covers zero-demand visits.
    """
    customers = list(visits)
    nodes = [root] + customers
    arcs = [(i, j) for i in nodes for j in nodes if i != j]
    x = model.addVars(arcs, lb=0.0, ub=1.0, name=f"{name}_arc")
    incoming = {j: gp.quicksum(x[i, j] for i in nodes if i != j) for j in nodes}
    outgoing = {i: gp.quicksum(x[i, j] for j in nodes if j != i) for i in nodes}
    for node in customers:
        model.addConstr(incoming[node] == visits[node])
        model.addConstr(outgoing[node] == visits[node])
    model.addConstr(incoming[root] == outgoing[root])
    model.addConstr(outgoing[root] <= fleet_size)
    for index, (demand, capacity) in enumerate(
            [({i: visits[i] for i in customers}, len(customers))]
            + list(zip(loads, capacities))):
        flow = model.addVars(arcs, lb=0.0, name=f"{name}_flow{index}")
        for i, j in arcs:
            model.addConstr(flow[i, j] <= capacity * x[i, j])
        for i in customers:
            model.addConstr(
                gp.quicksum(flow[j, i] - flow[i, j] for j in nodes if j != i)
                == demand[i])
        for i in customers:
            flow[i, root].UB = 0.0
    return gp.quicksum(distance[i, j] * x[i, j] for i, j in arcs)


def build_master(problem, vehicles_per_satellite=None, *, env=None,
                 relaxation="service", TFp=9999.0):
    """Build an assignment MILP without enumerating customer partitions."""
    if vehicles_per_satellite is None:
        vehicles_per_satellite = problem.fleet.e2_per_sat_count
    if int(vehicles_per_satellite) != vehicles_per_satellite or vehicles_per_satellite < 0:
        raise ValueError("vehicles_per_satellite must be a nonnegative integer")
    fleet = problem.fleet
    cw, cv = fleet.container_weight, fleet.container_volume
    parameters = [cw, cv, problem.e1_weight_cap, problem.e1_volume_cap,
                  problem.e2_weight_cap, problem.e2_volume_cap]
    if any(not math.isfinite(value) or value < 0.0 for value in parameters):
        raise ValueError("Benders requires finite nonnegative loads and capacities")
    if any(c.weight_del < 0.0 or not math.isfinite(c.weight_del)
           or not math.isfinite(c.service_time) or c.service_time < 0.0
           for c in problem.customers):
        raise ValueError("Benders requires nonnegative customer demands and service times")
    if any(not math.isfinite(s.service_time) or s.service_time < 0.0
           for s in problem.satellites):
        raise ValueError("Benders requires nonnegative satellite service times")
    distance = _shortest_paths(problem, problem.distance)
    travel = _shortest_paths(problem, problem.travel_time)
    if relaxation not in {"service", "flow"}:
        raise ValueError("master_relaxation must be 'service' or 'flow'")
    # Keeping service-route integrality provides a substantially tighter bound
    # than the small flow master when delayed pickups force repeated travel.
    # M4's task catalogue assumes positive goods demand; the flow master also
    # supports zero-demand customer rows used by the M7 task builder.
    if relaxation == "service" and problem.customers and problem.satellites and all(
            c.weight_del > 1e-9 for c in problem.customers):
        from models.routing.variants.model_4.model import build_model

        relaxed = copy.deepcopy(problem)
        ids = relaxed.index_node
        relaxed.dist = [[distance[i, j] for j in ids] for i in ids]
        relaxed.time = [[travel[i, j] for j in ids] for i in ids]
        model, variables, _ = build_model(
            relaxed, TFp=TFp, vehicles_per_satellite=vehicles_per_satellite,
            formulation_options={"prune_e2_arcs": True},
            relax_e2_pickup_load=True, env=env)
        model.update()
        theta = model.addVar(lb=0.0, name="total_distance")
        model.addConstr(theta >= model.getObjective(), name="service_lower_bound")
        model.setObjective(theta, GRB.MINIMIZE)
        model.update()
        return model, variables["w"], theta
    customers = [c.id for c in problem.customers]
    satellites = [s.id for s in problem.satellites]
    depot = problem.depot.id
    model = gp.Model("Model7_Logic_Benders_Master", env=env)
    assignment = model.addVars(customers, satellites, vtype=GRB.BINARY, name="assign")
    active = model.addVars(satellites, lb=0.0, ub=1.0, name="active")
    theta = model.addVar(lb=0.0, name="total_distance")
    model.setObjective(theta, GRB.MINIMIZE)
    weights = {c.id: c.weight_del + cw for c in problem.customers}
    for c in problem.customers:
        model.addConstr(gp.quicksum(assignment[c.id, s] for s in satellites) == 1)
        for s in satellites:
            model.addConstr(assignment[c.id, s] <= active[s])
            earliest = travel[depot, s] + travel[s, c.id]
            if (earliest > c.tw_del[1] + 1e-9
                    or max(earliest, c.tw_del[0]) + c.service_time > c.tw_pick[1] + 1e-9
                    or weights[c.id] > problem.e2_weight_cap + 1e-9
                    or cv > problem.e2_volume_cap + 1e-9):
                assignment[c.id, s].UB = 0.0
    sat_weight, sat_volume = {}, {}
    for s in satellites:
        count = gp.quicksum(assignment[c, s] for c in customers)
        model.addConstr(active[s] <= count)
        sat_weight[s] = gp.quicksum(weights[c] * assignment[c, s] for c in customers)
        sat_volume[s] = cv * count
        model.addConstr(sat_weight[s] <= min(
            problem.e1_weight_cap, vehicles_per_satellite * problem.e2_weight_cap))
        model.addConstr(sat_volume[s] <= min(
            problem.e1_volume_cap, vehicles_per_satellite * problem.e2_volume_cap))
        model.addConstr(cw * count <= problem.e1_weight_cap)
    lower_cost = _routing_relaxation(
        model, depot, active, [sat_weight, sat_volume],
        [problem.e1_weight_cap, problem.e1_volume_cap], problem.e1_vehicle_count,
        distance, "e1")
    for s in satellites:
        visits = {c: assignment[c, s] for c in customers}
        lower_cost += _routing_relaxation(
            model, s, visits,
            [{c: weights[c] * visits[c] for c in customers},
             {c: cv * visits[c] for c in customers}],
            [problem.e2_weight_cap, problem.e2_volume_cap],
            vehicles_per_satellite, distance, f"e2_{s}")
    model.addConstr(theta >= lower_cost, name="routing_lower_bound")
    model.update()
    return model, assignment, theta


def _finite(value):
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _solve_subproblem(problem, assignment, vehicles, **kwargs):
    from .exact_solver import _solve_partition

    e1, e2 = build_assignment_subproblem(problem, assignment)
    return _solve_partition(problem, e1, e2, vehicles, **kwargs)


def solve_benders(problem, e1_tasks=None, e2_tasks_by_sat=None,
                  vehicles_per_satellite=None, TFp=9999.0, time_limit=300,
                  verbose=True, return_info=False,
                  return_incumbent_on_time_limit=False, gurobi_params=None,
                  subproblem_time_limit=10.0, max_iterations=None,
                  absolute_tolerance=1e-6, strengthen=True,
                  use_relaxation_certificate=True,
                  partition_relaxation_screen=True,
                  warm_start_routes=None, warm_start_assignment=None,
                  warm_start_objective=None, master_relaxation="service",
                  master_time_limit=10.0):
    """Solve Model 7 by iterative assignment cuts and fixed-assignment M6.

    ``subproblem_time_limit`` is an initial slice; repeated assignments get
    exponentially larger slices. ``time_limit`` includes master construction.
    External starts must be no-handoff M4 routes and pass independent auditing.
    """
    del e1_tasks, e2_tasks_by_sat
    started = time.perf_counter()
    if time_limit is not None and (not math.isfinite(time_limit) or time_limit < 0):
        raise ValueError("time_limit must be finite and nonnegative, or None")
    if not math.isfinite(subproblem_time_limit) or subproblem_time_limit <= 0:
        raise ValueError("subproblem_time_limit must be finite and positive")
    if not math.isfinite(master_time_limit) or master_time_limit <= 0:
        raise ValueError("master_time_limit must be finite and positive")
    if not math.isfinite(absolute_tolerance) or absolute_tolerance < 0:
        raise ValueError("absolute_tolerance must be finite and nonnegative")
    if master_relaxation not in {"service", "flow"}:
        raise ValueError("master_relaxation must be 'service' or 'flow'")
    if max_iterations is not None and (int(max_iterations) != max_iterations or max_iterations < 1):
        raise ValueError("max_iterations must be a positive integer or None")
    # A caller cutoff or positive gap could change the meaning of a proof.
    controlled = {"Cutoff", "TimeLimit", "MIPGap", "MIPGapAbs", "BestObjStop", "BestBdStop"}
    if controlled.intersection(gurobi_params or {}):
        raise ValueError("Benders controls proof/time parameters: "
                         + ", ".join(sorted(controlled.intersection(gurobi_params))))
    if vehicles_per_satellite is None:
        vehicles_per_satellite = problem.fleet.e2_per_sat_count
    best_routes = best_assignment = None
    best_details = {}
    upper, lower = math.inf, 0.0
    supplied = [warm_start_routes is not None, warm_start_assignment is not None,
                warm_start_objective is not None]
    if any(supplied):
        if not all(supplied) or _finite(warm_start_objective) is None:
            raise ValueError("Benders warm start requires routes, assignment and finite objective")
        from models.routing.common.solution_audit import audit_exact_solution

        audit = audit_exact_solution(
            problem, 4, *warm_start_routes, assignment=warm_start_assignment,
            objective=warm_start_objective, tfp=TFp)
        if (not audit["valid"] or any(len(routes) > vehicles_per_satellite
                                     for routes in warm_start_routes[1].values())):
            raise ValueError(f"Benders warm start failed M4 route audit: {audit['errors']}")
        best_routes = warm_start_routes
        best_assignment = dict(warm_start_assignment)
        upper = float(warm_start_objective)
        best_details = {"incumbent_source": "audited_external_m4", "route_audit": audit}

    def remaining():
        return math.inf if time_limit is None else max(
            0.0, time_limit - (time.perf_counter() - started))

    history, attempts = [], {}
    optimality_cuts = feasibility_cuts = 0
    master_runtime = subproblem_runtime = 0.0
    proven = infeasible = False
    termination = "TIME_LIMIT"
    master_statistics = {}
    if verbose:
        print("Model 7 逻辑型 Benders：分仓主问题 + 完整 Model 6 子问题")
    if remaining() > 0.0:
        with gp.Env(empty=True) as env:
            env.setParam("OutputFlag", 0)
            env.start()
            master, w, theta = build_master(
                problem, vehicles_per_satellite, env=env,
                relaxation=master_relaxation, TFp=TFp)
            try:
                for name, value in (gurobi_params or {}).items():
                    master.setParam(name, value)
                master.setParam("MIPGap", 0.0)
                master.setParam("MIPGapAbs", 0.0)
                master.setParam("IntFeasTol", 1e-9)
                master.setParam("FeasibilityTol", 1e-9)
                master_statistics = {"variables": master.NumVars,
                                     "binary_variables": master.NumBinVars,
                                     "constraints": master.NumConstrs}
                preferred = best_assignment or {c.id: c.assigned_sat for c in problem.customers}
                for (c, s), var in w.items():
                    var.Start = float(preferred.get(c) == s)
                while remaining() > 1e-6:
                    if max_iterations is not None and len(history) >= max_iterations:
                        termination = "ITERATION_LIMIT"
                        break
                    master.setParam("TimeLimit", min(master_time_limit, remaining()))
                    master.optimize()
                    master_runtime += master.Runtime
                    bound = _finite(master.ObjBound)
                    if bound is not None:
                        lower = max(lower, bound)
                    if master.Status == GRB.INFEASIBLE:
                        infeasible = not math.isfinite(upper)
                        proven = not infeasible
                        termination = "INFEASIBLE" if infeasible else "OPTIMAL"
                        break
                    if math.isfinite(upper) and lower >= upper - absolute_tolerance:
                        proven, termination = True, "OPTIMAL"
                        break
                    if not master.SolCount and master.Status == GRB.TIME_LIMIT and remaining() > 1e-6:
                        continue
                    if not master.SolCount or remaining() <= 1e-6:
                        termination = "TIME_LIMIT" if remaining() <= 1e-6 else "MASTER_LIMIT"
                        break
                    assignment = {c.id: next(s.id for s in problem.satellites
                                              if w[c.id, s.id].X > 0.5)
                                  for c in problem.customers}
                    key = tuple(assignment[c.id] for c in problem.customers)
                    attempts[key] = attempts.get(key, 0) + 1
                    budget = min(remaining(), subproblem_time_limit * 2 ** min(20, attempts[key] - 1))
                    sub_started = time.perf_counter()
                    r1, r2, sub = _solve_subproblem(
                        problem, assignment, vehicles_per_satellite,
                        TFp=TFp, time_limit=budget, verbose=False, return_info=True,
                        return_incumbent_on_time_limit=True, mip_gap=0.0,
                        gurobi_params=dict(gurobi_params or {}), strengthen=strengthen,
                        compute_iis_on_infeasible=False,
                        use_relaxation_certificate=use_relaxation_certificate,
                        screen=partition_relaxation_screen,
                        screen_time_limit=min(3.0, budget * 0.5), incumbent=upper)
                    subproblem_runtime += time.perf_counter() - sub_started
                    objective = _finite(sub.get("objective"))
                    if objective is not None and r1 is not None and r2 is not None and objective < upper:
                        upper, best_routes, best_assignment = objective, (r1, r2), dict(assignment)
                        best_details = dict(sub)
                        best_details["incumbent_source"] = "model6_subproblem"
                    mismatch = gp.quicksum(1 - w[c, s] for c, s in assignment.items())
                    cut_bound = _finite(sub.get("best_bound"))
                    # The recourse value strengthens the assignment-specific
                    # distance cut as soon as a feasible completion is found.
                    if objective is not None:
                        cut_bound = max(cut_bound or 0.0, objective)
                    if sub.get("status") == "INFEASIBLE":
                        master.addConstr(mismatch >= 1, name=f"feasibility_{len(history)}")
                        feasibility_cuts += 1
                        cut_kind = "feasibility"
                    elif cut_bound is not None and cut_bound > 0.0:
                        master.addConstr(theta >= cut_bound * (1 - mismatch),
                                         name=f"optimality_{len(history)}")
                        optimality_cuts += 1
                        cut_kind = "optimality"
                    else:
                        cut_kind = None
                    history.append({
                        "iteration": len(history) + 1, "assignment": assignment,
                        "attempt": attempts[key], "subproblem_status": sub.get("status"),
                        "subproblem_objective": objective,
                        "subproblem_best_bound": _finite(sub.get("best_bound")),
                        "cut_type": cut_kind, "cut_bound": cut_bound,
                        "global_lower_bound": lower,
                        "global_upper_bound": upper if math.isfinite(upper) else None,
                        "elapsed": time.perf_counter() - started})
                    if verbose:
                        print(f"[Benders {len(history)}] {sub.get('status')} "
                              f"LB={lower:.6f} UB={upper:.6f} cut={cut_kind}")
                    if sub.get("status") in {"NUMERIC", "INTERRUPTED", "INF_OR_UNBD", "UNBOUNDED"}:
                        termination = "SUBPROBLEM_LIMIT"
                        break
            finally:
                master.dispose()
    if proven:
        lower = upper
    gap = (max(0.0, upper - lower) / abs(upper) if upper != 0.0 else 0.0) if math.isfinite(upper) else None
    info = {
        "status": termination,
        "status_code": {"OPTIMAL": GRB.OPTIMAL, "INFEASIBLE": GRB.INFEASIBLE,
                        "TIME_LIMIT": GRB.TIME_LIMIT, "ITERATION_LIMIT": GRB.ITERATION_LIMIT}.get(termination),
        "proven_optimal": proven,
        "solution_type": "proven_optimal" if proven else "feasible_not_proven" if best_routes is not None else "none",
        "objective": upper if math.isfinite(upper) else None,
        "best_bound": None if infeasible else lower,
        "mip_gap": gap, "runtime": time.perf_counter() - started,
        "sol_count": int(best_routes is not None), "assignment": best_assignment,
        "formulation": "logic_based_benders_assignment_model6",
        "proof_scope": "full_free_assignment_async_handoff_model7",
        "algorithm": "benders", "iterations": len(history),
        "optimality_cuts": optimality_cuts, "feasibility_cuts": feasibility_cuts,
        "assignment_partitions_total": assignment_count(problem),
        "assignment_partitions_evaluated": len(attempts),
        "assignment_partition_retries": sum(attempts.values()) - len(attempts),
        "master_runtime": master_runtime, "subproblem_runtime": subproblem_runtime,
        "master_statistics": master_statistics, "iteration_history": history,
        "master_relaxation": master_relaxation,
        "absolute_tolerance": absolute_tolerance,
        "incumbent_source": best_details.get("incumbent_source"),
    }
    for field, default in (("transshipments", []), ("transshipment_count", 0),
                           ("transferred_container_count", 0), ("route_vehicle_indices", {})):
        info[field] = best_details.get(field, default)
    routes = (*best_routes, best_assignment) if best_routes is not None and (
        proven or return_incumbent_on_time_limit) else (None, None, None)
    return (*routes, info) if return_info else routes


__all__ = ["build_master", "solve_benders"]
