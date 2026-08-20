# -*- coding: utf-8 -*-
"""Lagrangian decomposition of the Model 2 E1<->E2 delivery time coupling.

The full Model 2 lower bound decomposes as

    LB_baseline = E1_independent_optimal + E2_independent_BCP_sum
                = 792.3468 + 1031.8799 = 1824.2267   (100_customers/C101)

which treats E1 and E2 as completely decoupled. The remaining gap to the
incumbent 1829.0094 (~4.78 units, 0.2615%) is the cost of the E1->E2 delivery
time linking: E2 routes cannot leave a satellite until E1 has delivered there.
Arc-flow branch-and-bound cannot close this (the Gurobi bound is pinned at
1824.2267 from root to 2702 nodes).

Only the **delivery** coupling is relaxed. The pickup linking
``A[pick_s] >= R[rid]`` is trivially satisfied in these instances because pickup
tasks carry the TFp>=9000 sentinel time window ``tw=(9999, inf)``, so
``A[pick_s] >= 9999`` dominates any finite E2 return time ``R[rid]``. Relaxing
it would only inject numerical noise (a free ``T_pick`` pushed to its ``M``
fallback), so it is dropped.

This module relaxes the aggregated per-satellite delivery constraint

    (D_s)  S_min[s] >= T_del[s] + service_del[s]

with non-negative multipliers ``lam_del[s]``, where

    T_del[s] = E1 arrival time at the satellite-s delivery task,
    S_min[s] = earliest E2 route departure from satellite s.

For any ``lam_del >= 0`` the Lagrangian bound

    L(lam) = E1_sub(lam) + E2_sub(lam) + sum_s lam_del[s]*service_del[s]

is a valid global lower bound on Model 2. At ``lam = 0`` it reduces to the
baseline 1824.2267. A subgradient method raises it toward the optimum.

The E1 subproblem keeps the exact arc-flow E1 formulation (integer, strong).
The E2 subproblem uses an integer route-pool master so ``S_min`` is pinned to
the actual earliest selected-route departure (the LP relaxation leaves it
free). Both reuse the constraint builders used by ``route_pool_exact``.
"""

import gurobipy as gp
from gurobipy import GRB

from models.model_2.route_pool_exact import (
    _add_e1_constraints,
    _add_route_pool_constraints,
    _normalize_columns,
    _phys,
)


def _setup_e1_geometry(problem, e1_tasks):
    """dist/travel/service tables over E1 nodes (depot + E1 satellite tasks)."""
    depot_id = problem.depot.id
    F = [s.id for s in problem.satellites]
    e1_nodes = [t.id for t in e1_tasks]
    n1e = [depot_id] + e1_nodes
    e1_task_map = {t.id: t for t in e1_tasks}

    def loc(node_id):
        if node_id == depot_id or node_id in F:
            return node_id
        task = e1_task_map.get(node_id)
        return task.cust if task is not None else node_id

    ids = set([depot_id] + e1_nodes)
    dist, travel = {}, {}
    for i in ids:
        for j in ids:
            dist[i, j] = 0.0 if i == j else problem.distance(loc(i), loc(j))
            travel[i, j] = 0.0 if i == j else problem.travel_time(loc(i), loc(j))

    e1_service = {depot_id: 0.0}
    for node in e1_nodes:
        e1_service[node] = getattr(problem.nodes.get(loc(node)), "service_time", 0.0)

    horizon = max(
        (t.tw[1] for t in e1_tasks if t.kind == "d" and t.tw[1] < float("inf")),
        default=2000.0,
    )
    # ``_add_e1_constraints`` pins non-visiting trucks' arrival times with
    # M=1e5, so the T_del disjunctive link must use an M at least that large.
    ctx = {
        "depot_id": depot_id,
        "F": F,
        "e1_nodes": e1_nodes,
        "n1e": n1e,
        "e1_task_map": e1_task_map,
        "dist": dist,
        "travel": travel,
        "e1_service": e1_service,
        "horizon": horizon,
        "M": 100000.0 + horizon,
        "sat_ids": sorted({t.cust for t in e1_tasks}),
    }
    return ctx


def solve_e1_subproblem(problem, e1_tasks, lam_del, time_limit=30.0,
                        gurobi_params=None, log_file=None, t_del_cap=None):
    """Minimize E1 arc-flow cost + lam_del*T_del over E1 decisions.

    Returns the E1 routing cost (``obj_e1_cost``) and the per-satellite
    delivery arrival times ``T_del`` used by the subgradient update.
    """
    g = _setup_e1_geometry(problem, e1_tasks)
    depot_id = g["depot_id"]
    K = list(range(problem.e1_vehicle_count))
    n1e, e1_nodes = g["n1e"], g["e1_nodes"]
    e1_task_map, dist = g["e1_task_map"], g["dist"]
    e1_service, M = g["e1_service"], g["M"]
    sat_ids = g["sat_ids"]

    m = gp.Model("Lagrangian_E1")
    m.Params.OutputFlag = 1 if log_file else 0
    if log_file:
        m.Params.LogFile = str(log_file)
    m.Params.TimeLimit = time_limit
    for name, value in (gurobi_params or {}).items():
        m.setParam(name, value)

    x = m.addVars(n1e, n1e, K, vtype=GRB.BINARY, name="x")
    A = m.addVars(n1e, K, lb=0.0, name="A")
    Q = m.addVars(n1e, K, lb=0.0, ub=problem.e1_weight_cap, name="Q")
    V = m.addVars(n1e, K, lb=0.0, ub=problem.e1_volume_cap, name="Vol")

    _add_e1_constraints(
        m, problem, e1_tasks, x, A, Q, V, n1e, e1_nodes, K, depot_id,
        e1_task_map, dist, g["travel"], e1_service,
    )

    T_del = {}
    for s in sat_ids:
        dnode = f"{s}d"
        if dnode in e1_task_map:
            e_j, l_j = e1_task_map[dnode].tw
            T_del[s] = m.addVar(
                lb=max(0.0, e_j),
                ub=l_j if l_j < float("inf") else g["horizon"],
                name=f"Tdel_{s}",
            )
    m.update()
    for s in sat_ids:
        if s in T_del:
            dnode = f"{s}d"
            for k in K:
                visits = gp.quicksum(x[i, dnode, k] for i in n1e if i != dnode)
                m.addConstr(T_del[s] >= A[dnode, k] - M * (1 - visits),
                            f"TdelLink_{s}_{k}")
            if t_del_cap and s in t_del_cap:
                m.addConstr(T_del[s] <= float(t_del_cap[s]), f"TdelCap_{s}")

    obj_e1 = gp.quicksum(
        dist[i, j] * x[i, j, k]
        for k in K for i in n1e for j in n1e if i != j
    )
    mult = gp.LinExpr()
    for s, var in T_del.items():
        coef = lam_del.get(s, 0.0)
        if coef:
            mult += coef * var
    m.setObjective(obj_e1 + mult, GRB.MINIMIZE)
    m.optimize()

    result = {
        "status": int(m.status),
        "status_name": _status_name(m.status),
        "obj": float(m.ObjVal) if m.SolCount else None,
        "obj_e1_cost": None,
        "T_del": {},
        "runtime": float(m.Runtime),
    }
    if m.SolCount:
        result["obj_e1_cost"] = float(sum(
            dist[i, j] * x[i, j, k].X
            for k in K for i in n1e for j in n1e if i != j
        ))
        for s, var in T_del.items():
            result["T_del"][s] = float(var.X)
    return result


def solve_e2_subproblem(problem, e2_tasks_by_sat, route_columns, lam_del,
                        vehicles_per_satellite, time_limit=30.0,
                        gurobi_params=None, log_file=None, integer_master=True,
                        satellite_cost_lbs=None, initial_route_ids=None):
    """Minimize E2 route-pool cost - lam_del*S_min over E2 decisions.

    ``integer_master=True`` with ``initial_route_ids`` warm-start: the previous
    iteration's route set is used as a MIP start so the route set changes
    gradually as lam changes (instead of jumping, which caused subgradient
    oscillation). eps pushes pin S[rid]/S_min to physical values. Satellite LB
    cuts keep the E2 cost at the BCP optimum.
    """
    e2_task_map = {
        t.id: t for tasks in e2_tasks_by_sat.values() for t in tasks
    }
    e2_task_to_sat = {
        t.id: sat_id
        for sat_id, tasks in e2_tasks_by_sat.items()
        for t in tasks
    }
    cols = _normalize_columns(problem, e2_task_map, route_columns)
    route_ids = list(range(len(cols)))

    # Physical bound for S_min: use delivery-task time windows (pickup tasks
    # carry the 9999 sentinel and must be excluded).
    horizon = max(
        (t.tw[1] for t in e2_task_map.values()
         if t.kind == "d" and t.tw[1] < float("inf")),
        default=2000.0,
    )
    M = 100000.0 + horizon

    # S_max[rid] = the route's latest feasible departure (must not arrive at
    # the first customer after its time window). This is a FIXED route
    # attribute, not a free variable, so basing S_min on S_max physicalizes it
    # without polluting route selection.
    s_max = {}
    for rid, col in enumerate(cols):
        path = col["path"]
        sat_id = col["sat_id"]
        sm = horizon
        if len(path) > 2:
            first_node = path[1]
            if first_node in e2_task_map:
                l_first = e2_task_map[first_node].tw[1]
                if l_first < float("inf"):
                    travel_first = problem.travel_time(
                        _phys(e2_task_map, sat_id), e2_task_map[first_node].cust
                    )
                    sm = l_first - travel_first
        s_max[rid] = sm

    m = gp.Model("Lagrangian_E2")
    m.Params.OutputFlag = 1 if log_file else 0
    if log_file:
        m.Params.LogFile = str(log_file)
    m.Params.TimeLimit = time_limit
    for name, value in (gurobi_params or {}).items():
        m.setParam(name, value)

    y_vtype = GRB.BINARY if integer_master else GRB.CONTINUOUS
    y = m.addVars(route_ids, lb=0.0, ub=1.0, vtype=y_vtype, name="route")
    S = m.addVars(route_ids, lb=0.0, name="route_start")
    R = m.addVars(route_ids, lb=0.0, name="route_return")
    A2 = m.addVars(sorted(e2_task_map), lb=0.0, name="e2_task_arrival")

    _add_route_pool_constraints(
        m, problem, e2_tasks_by_sat, cols, y, S, R, A2,
        vehicles_per_satellite, e2_task_map, e2_task_to_sat,
        satellite_cost_lbs=satellite_cost_lbs,
    )

    routes_by_sat = {}
    for rid, col in enumerate(cols):
        routes_by_sat.setdefault(col["sat_id"], []).append(rid)
    sat_ids = sorted(routes_by_sat)

    # Pin S[rid] to a physical latest departure (must not arrive at the first
    # customer after its time window). Without this the route-start time is
    # free in the E2 objective and S_min drifts to a bound, producing a
    # meaningless subgradient.
    for rid, col in enumerate(cols):
        path = col["path"]
        if len(path) > 2:
            first_node = path[1]
            if first_node in e2_task_map:
                l_first = e2_task_map[first_node].tw[1]
                if l_first < float("inf"):
                    sat_id = col["sat_id"]
                    travel_first = problem.travel_time(
                        _phys(e2_task_map, sat_id), e2_task_map[first_node].cust
                    )
                    m.addConstr(
                        S[rid] <= l_first - travel_first + M * (1 - y[rid]),
                        f"Sphys_{rid}",
                    )

    S_min = {}
    for s in sat_ids:
        S_min[s] = m.addVar(lb=0.0, ub=horizon, name=f"Smin_{s}")
    m.update()
    for s, rids in routes_by_sat.items():
        for rid in rids:
            m.addConstr(S_min[s] <= s_max[rid] + M * (1 - y[rid]),
                        f"SminLink_{s}_{rid}")

    obj_e2 = gp.quicksum(cols[rid]["cost"] * y[rid] for rid in route_ids)
    mult = gp.LinExpr()
    for s in sat_ids:
        coef = lam_del.get(s, 0.0)
        if coef:
            mult -= coef * S_min[s]
    # post_S_min is bounded above by the FIXED s_max[rid] attribute (not a free
    # S[rid] variable), so -lam*post_S_min alone drives it to min selected s_max.
    # No eps hack (it pollutes LP cost and is too weak for integer). lam must be
    # > 0 (the driver starts the bundle at a small positive lam).
    m.setObjective(obj_e2 + mult, GRB.MINIMIZE)
    if initial_route_ids and integer_master:
        sel = set(initial_route_ids)
        for rid in route_ids:
            y[rid].Start = 1.0 if rid in sel else 0.0
    m.optimize()

    result = {
        "status": int(m.status),
        "status_name": _status_name(m.status),
        "obj": float(m.ObjVal) if m.SolCount else None,
        "obj_e2_cost": None,
        "S_min": {},
        "route_columns": len(cols),
        "selected_route_ids": [],
        "runtime": float(m.Runtime),
    }
    if m.SolCount:
        result["obj_e2_cost"] = float(sum(
            cols[rid]["cost"] * y[rid].X for rid in route_ids
        ))
        for s in sat_ids:
            result["S_min"][s] = float(S_min[s].X)
        result["selected_route_ids"] = [
            rid for rid in route_ids if y[rid].X > 0.5
        ]
    return result


def _status_name(status_code):
    names = {
        GRB.OPTIMAL: "OPTIMAL",
        GRB.TIME_LIMIT: "TIME_LIMIT",
        GRB.INFEASIBLE: "INFEASIBLE",
        GRB.INF_OR_UNBD: "INF_OR_UNBD",
        GRB.INTERRUPTED: "INTERRUPTED",
        GRB.SUBOPTIMAL: "SUBOPTIMAL",
        GRB.NUMERIC: "NUMERIC",
    }
    return names.get(status_code, str(status_code))
