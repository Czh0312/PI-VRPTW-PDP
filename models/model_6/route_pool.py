# -*- coding: utf-8 -*-
"""Restricted route-column master for Model 6 (free assignment).

Key difference from M2's route_pool_exact: the customer-satellite assignment
``w[c, s]`` is a decision variable, so E1 satellite visits become optional,
E1 loads are linear in ``w``, and E2 route coverage is linked to ``w`` instead
of being fixed to 1.

It replaces M6's E2 arc variables with a supplied pool of route columns.  A
Gurobi ``OPTIMAL`` status therefore proves optimality *only within that pool*,
not for the unrestricted Model 6.  It is useful for heuristics and incumbent
generation; use ``models.model_6.exact`` or a complete branch-price-and-cut
implementation for a global certificate.

The E1 constraint logic (optional visits, ``y[c,s,k]`` linearization, delivery
deadline implication) is adapted from ``models/model_6/exact.py``.
"""
import json
import time
import math

import gurobipy as gp
from gurobipy import GRB

from models.alns_base import is_tfp_active
from models.solver_utils import extract_routes_e1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _signed_q(w, kind):
    return w if kind == "d" else -w


def _signed_v(v, kind):
    return v if kind == "d" else -v


def _route_cost(problem, sat_id, path, task_map):
    """Cost of a path: sum of distances between consecutive physical nodes."""
    total = 0.0
    prev = sat_id
    for node_id in path[1:]:
        phys = task_map.get(node_id)
        phys_loc = phys.cust if phys is not None else node_id
        total += problem.distance(prev, phys_loc)
        prev = phys_loc
    return total


def _normalize_columns_m6(problem, all_task_map, route_columns):
    """Normalize route columns from raw dict / ALNS paths.

    Each column must have: sat_id, path (list with sat at both ends), tasks (covered task IDs).
    """
    normalized = []
    seen = set()
    for col in route_columns:
        if isinstance(col, dict):
            path = tuple(col.get("path", []))
            sat_id = col.get("sat_id") or (path[0] if path else None)
            tasks = tuple(col.get("tasks", []))
            cost = float(col.get("cost", 0.0)) if col.get("cost") else None
            source = col.get("source", "pool")
        else:
            continue
        if len(path) < 3 or path in seen:
            continue
        if sat_id is None:
            continue
        seen.add(path)
        # Filter tasks to those that exist in the task map
        valid_tasks = tuple(t for t in tasks if t in all_task_map)
        if not valid_tasks:
            continue
        if cost is None or cost == 0.0:
            cost = _route_cost(problem, sat_id, path, all_task_map)
        normalized.append({
            "sat_id": sat_id,
            "path": path,
            "tasks": valid_tasks,
            "cost": cost,
            "source": source,
        })
    return normalized


def _singleton_column_m6(problem, sat_id, c_id, d_task, p_task):
    """Pair-singleton route: sat -> {c}d -> {c}p -> sat (covers both d and p).

    This replaces the old per-task singletons to work with merged d/p coverage.
    LIFO is trivially satisfied (one customer, delivery before pickup).
    """
    cw = problem.fleet.container_weight
    cv = problem.fleet.container_volume
    inf = float("inf")

    # Check capacity: delivery load + container
    if d_task.w > problem.e2_weight_cap + 1e-6:
        return None
    if d_task.v > problem.e2_volume_cap + 1e-6:
        return None

    # Check time window feasibility
    travel_d = problem.travel_time(sat_id, d_task.cust)
    travel_p = problem.travel_time(d_task.cust, p_task.cust)
    travel_ret = problem.travel_time(p_task.cust, sat_id)

    # Delivery TW
    e_d, l_d = d_task.tw
    if l_d < inf and travel_d > l_d + 1e-6:
        return None

    # Pickup TW (delivery service + travel to pickup)
    svc_d = getattr(problem.nodes.get(d_task.cust), "service_time", 0.0)
    arrive_p = max(travel_d, e_d) + svc_d + travel_p
    e_p, l_p = p_task.tw
    if l_p < inf and arrive_p > l_p + 1e-6:
        return None

    path = (sat_id, f"{c_id}d", f"{c_id}p", sat_id)
    cost = (problem.distance(sat_id, d_task.cust)
            + problem.distance(d_task.cust, p_task.cust)
            + problem.distance(p_task.cust, sat_id))
    return {
        "sat_id": sat_id,
        "path": path,
        "tasks": (f"{c_id}d", f"{c_id}p"),
        "cost": cost,
        "source": "singleton",
    }


def _generate_singleton_columns(problem, customers, satellites):
    """Generate pair-singleton route columns for every (customer, satellite) pair.

    Each column covers BOTH delivery and pickup for one customer at one satellite.
    """
    cw = problem.fleet.container_weight
    cv = problem.fleet.container_volume
    all_task_map = {}
    for c in customers:
        all_task_map[f"{c.id}d"] = type("T", (), {
            "id": f"{c.id}d", "cust": c.id, "kind": "d",
            "w": c.weight_del + cw, "v": cv, "tw": c.tw_del,
        })()
        all_task_map[f"{c.id}p"] = type("T", (), {
            "id": f"{c.id}p", "cust": c.id, "kind": "p",
            "w": cw, "v": cv, "tw": c.tw_pick,
        })()
    cols = []
    for s in satellites:
        for c in customers:
            d_task = all_task_map[f"{c.id}d"]
            p_task = all_task_map[f"{c.id}p"]
            col = _singleton_column_m6(problem, s.id, c.id, d_task, p_task)
            if col is not None:
                cols.append(col)
    return cols, all_task_map


def _extract_route_columns_from_alns(problem, e2_routes_by_sat, assignment):
    """Extract route columns from ALNS solution.

    ALNS routes are lists of task IDs (without depot). We reconstruct full
    paths: sat -> task1 -> ... -> taskN -> sat.
    """
    cw = problem.fleet.container_weight
    cv = problem.fleet.container_volume
    cols = []
    for sat_id, routes in e2_routes_by_sat.items():
        for route_path in routes:
            # route_path is full path: [sat, task1, ..., taskN, sat]
            if not route_path or len(route_path) < 3:
                continue
            # Extract task IDs (non-satellite nodes)
            task_ids = [n for n in route_path[1:-1]]
            if not task_ids:
                continue
            # Compute cost
            path = tuple(route_path)
            # Build task map for cost computation
            tmp_map = {}
            for tid in task_ids:
                cid = tid[:-1]
                if tid.endswith("d"):
                    c = next((c for c in problem.customers if c.id == cid), None)
                    if c:
                        tmp_map[tid] = type("T", (), {
                            "id": tid, "cust": cid, "kind": "d",
                            "w": c.weight_del + cw, "v": cv, "tw": c.tw_del,
                        })()
                elif tid.endswith("p"):
                    tmp_map[tid] = type("T", (), {
                        "id": tid, "cust": cid, "kind": "p",
                        "w": cw, "v": cv, "tw": None,  # not needed for cost
                    })()
            cost = _route_cost(problem, sat_id, path, tmp_map)
            cols.append({
                "sat_id": sat_id,
                "path": path,
                "tasks": tuple(task_ids),
                "cost": cost,
                "source": "alns",
            })
    return cols


# ---------------------------------------------------------------------------
# Geometry setup
# ---------------------------------------------------------------------------

def _setup_geometry(problem, TFp, vehicles_per_satellite):
    """Build distance/time maps and node sets for the M6 route-pool model."""
    inf = float("inf")
    depot_id = problem.depot.id
    F = [s.id for s in problem.satellites]
    customers = list(problem.customers)
    C = [c.id for c in customers]
    cw = problem.fleet.container_weight
    cv = problem.fleet.container_volume
    K = list(range(problem.e1_vehicle_count))

    cust_w = {c.id: c.weight_del + cw for c in customers}
    cust_tw_del = {c.id: c.tw_del for c in customers}
    cust_tw_pick = {c.id: c.tw_pick for c in customers}
    cust_svc = {c.id: c.service_time for c in customers}

    # E1 nodes: depot + satellite del/pick nodes
    e1_del_nodes = [f"{s}d" for s in F]
    e1_pick_nodes = [f"{s}p" for s in F]
    e1_nodes = [depot_id] + e1_del_nodes + e1_pick_nodes

    # E2 task IDs: each customer has d and p
    e2_task_ids = []
    for c in C:
        e2_task_ids.append(f"{c}d")
        e2_task_ids.append(f"{c}p")

    # E2 task map (customer-level info)
    e2_task_map = {}
    for c in customers:
        e2_task_map[f"{c.id}d"] = type("T", (), {
            "id": f"{c.id}d", "cust": c.id, "kind": "d",
            "w": c.weight_del + cw, "v": cv, "tw": c.tw_del,
        })()
        e2_task_map[f"{c.id}p"] = type("T", (), {
            "id": f"{c.id}p", "cust": c.id, "kind": "p",
            "w": cw, "v": cv, "tw": c.tw_pick,
        })()

    # E1 service times
    e1_svc = {depot_id: 0.0}
    for s in F:
        e1_svc[f"{s}d"] = getattr(problem.nodes[s], "service_time", 0.0)
        e1_svc[f"{s}p"] = getattr(problem.nodes[s], "service_time", 0.0)

    e2_svc = {tid: cust_svc[tid[:-1]] for tid in e2_task_ids}

    # Distance/time maps
    all_ids = set([depot_id] + F + e1_del_nodes + e1_pick_nodes + e2_task_ids)

    def phys(node_id):
        if node_id in F or node_id == depot_id:
            return node_id
        if len(node_id) > 1 and node_id[-1] in ("d", "p"):
            base = node_id[:-1]
            if base in F or base in set(C):
                return base
        return node_id

    dist_map = {}
    time_map = {}
    for i in all_ids:
        pi = phys(i)
        for j in all_ids:
            pj = phys(j)
            dist_map[i, j] = 0.0 if i == j else problem.distance(pi, pj)
            time_map[i, j] = 0.0 if i == j else problem.travel_time(pi, pj)

    # Time horizon
    finite_times = [0.0]
    for c in customers:
        for tw in (c.tw_del, c.tw_pick):
            if tw[0] < inf:
                finite_times.append(tw[0])
            if tw[1] < inf:
                finite_times.append(tw[1])
    if is_tfp_active(TFp):
        finite_times.append(TFp)
    max_arc = max(time_map.values(), default=0.0)
    total_svc = sum(e1_svc.values()) + sum(e2_svc.values())
    task_count = len(e1_nodes) - 1 + len(F) * len(e2_task_ids)
    time_horizon = max(finite_times) + (task_count + 2) * max_arc + total_svc + 1.0
    M_time = time_horizon + max_arc

    # E2 infeasible arcs and arc Big-M (for route-level timing)
    e2_infeasible = set()
    e2_arc_m = {}
    for s in F:
        for i in [s] + e2_task_ids:
            for j in e2_task_ids:
                if i == j:
                    continue
                e_j, l_j = e2_task_map[j].tw
                if l_j < inf and e_j + time_map[i, j] > l_j + 1e-6:
                    e2_infeasible.add((s, i, j))
                if i in e2_task_map:
                    tw_i = e2_task_map[i].tw
                    latest_i = tw_i[1] if tw_i and tw_i[1] < inf else 0.0
                else:
                    latest_i = 0.0
                e2_arc_m[i, j] = max(
                    e2_svc.get(i, 0.0) + time_map[i, j],
                    latest_i + e2_svc.get(i, 0.0) + time_map[i, j],
                )

    V_by_sat = {s: [(s, v) for v in range(vehicles_per_satellite)] for s in F}

    return {
        "depot_id": depot_id, "F": F, "C": C, "K": K,
        "customers": customers,
        "e1_nodes": e1_nodes, "e1_del_nodes": e1_del_nodes,
        "e1_pick_nodes": e1_pick_nodes,
        "e2_task_ids": e2_task_ids, "e2_task_map": e2_task_map,
        "e1_svc": e1_svc, "e2_svc": e2_svc,
        "dist_map": dist_map, "time_map": time_map,
        "M_time": M_time, "time_horizon": time_horizon,
        "V_by_sat": V_by_sat, "vehicles_per_satellite": vehicles_per_satellite,
        "cust_w": cust_w, "cust_tw_del": cust_tw_del,
        "cust_tw_pick": cust_tw_pick, "cust_svc": cust_svc,
        "cw": cw, "cv": cv,
        "e2_infeasible": e2_infeasible, "e2_arc_m": e2_arc_m,
        "TFp": TFp, "inf": inf,
        "problem": problem,
    }


# ---------------------------------------------------------------------------
# Column generation: per-satellite CG for M6
# ---------------------------------------------------------------------------

def _solve_satellite_cg_m6(problem, sat_id, tasks, vehicles,
                            max_iter=50, pricing_time=30.0):
    """Run CG for one satellite to generate route columns (M6 style).

    Uses M2's e2_route_cg since the E2 routing per satellite is identical
    once the assignment is fixed — only the customer set differs.
    """
    from models.model_2.e2_route_cg import (
        solve_satellite_route_cg, _singleton_column, _columns_from_paths,
        E2RouteColumn,
    )

    result = solve_satellite_route_cg(
        problem, sat_id, tasks, vehicles,
        max_iterations=max_iter,
        pricing_time_limit=pricing_time,
        pricing_pool_solutions=10,
        pricing_method="mip",
        integer_master=True,
        return_columns=True,
        output_flag=False,
    )
    cols = []
    for cd in result.get("columns_data", []):
        cols.append({
            "sat_id": sat_id,
            "path": tuple(cd["path"]),
            "tasks": tuple(cd["tasks"]),
            "cost": float(cd["cost"]),
            "source": "cg",
        })
    return cols, result


# ---------------------------------------------------------------------------
# Main model builder
# ---------------------------------------------------------------------------

def build_m6_route_master(problem, route_columns, vehicles_per_satellite,
                           TFp=9999.0, relax=False, incumbent_cost=None,
                           partial_relax=False):
    """Build the M6 route-column master with free assignment w[c,s].

    Variables:
      w[c,s]     -- binary: customer c assigned to satellite s
      y[rid]     -- binary: route column rid selected
      x[i,j,k]   -- binary: E1 arc (i,j) on truck k
      served_s[s] -- binary: satellite s has any assigned customer
      y_lin[c,s,k] -- binary: w[c,s] ∧ truck k visits S{s}d (E1 load linearization)
      A_e1[i,k]  -- E1 arrival time
      Q_k[i,k]   -- E1 load (weight)
      Vol_k[i,k] -- E1 load (volume)
      S[rid]     -- route start time
      R[rid]     -- route return time
      A2[t]      -- E2 task arrival time (absolute)
    """
    geo = _setup_geometry(problem, TFp, vehicles_per_satellite)
    depot_id = geo["depot_id"]
    F = geo["F"]
    C = geo["C"]
    K = geo["K"]
    customers = geo["customers"]
    e1_nodes = geo["e1_nodes"]
    e1_del_nodes = geo["e1_del_nodes"]
    e1_pick_nodes = geo["e1_pick_nodes"]
    e2_task_ids = geo["e2_task_ids"]
    e2_task_map = geo["e2_task_map"]
    e1_svc = geo["e1_svc"]
    e2_svc = geo["e2_svc"]
    dist_map = geo["dist_map"]
    time_map = geo["time_map"]
    M_time = geo["M_time"]
    time_horizon = geo["time_horizon"]
    V_by_sat = geo["V_by_sat"]
    cust_w = geo["cust_w"]
    cust_tw_del = geo["cust_tw_del"]
    cust_tw_pick = geo["cust_tw_pick"]
    cust_svc = geo["cust_svc"]
    cw = geo["cw"]
    cv = geo["cv"]
    e2_infeasible = geo["e2_infeasible"]
    e2_arc_m = geo["e2_arc_m"]
    inf = geo["inf"]

    # Normalize columns
    cols = _normalize_columns_m6(problem, e2_task_map, route_columns)
    route_ids = list(range(len(cols)))

    m = gp.Model("M6_RoutePool_Master")
    if not relax:
        m.setParam("OutputFlag", 1)

    # ======================== Variables ========================
    # In partial_relax mode: keep w (assignment) as BINARY, relax only route/flow
    # This avoids LP degeneracy from fractional assignments while still
    # allowing column generation on the route variables.
    if relax and partial_relax:
        vt_route = GRB.CONTINUOUS   # y_route relaxed for CG
        vt_assign = GRB.BINARY      # w kept binary to avoid degeneracy
        vt_flow = GRB.BINARY        # x kept binary (E1 routing)
        vt_aux = GRB.BINARY         # served_s, y_lin kept binary
    elif relax:
        vt_route = GRB.CONTINUOUS
        vt_assign = GRB.CONTINUOUS
        vt_flow = GRB.CONTINUOUS
        vt_aux = GRB.CONTINUOUS
    else:
        vt_route = GRB.BINARY
        vt_assign = GRB.BINARY
        vt_flow = GRB.BINARY
        vt_aux = GRB.BINARY

    w = m.addVars(C, F, vtype=vt_assign, lb=0.0, ub=1.0, name="w")
    y_route = m.addVars(route_ids, vtype=vt_route, lb=0.0, ub=1.0, name="route")
    x = m.addVars(e1_nodes, e1_nodes, K, vtype=vt_flow, lb=0.0, ub=1.0, name="x")
    served_s = m.addVars(F, vtype=vt_aux, lb=0.0, ub=1.0, name="served_s")
    y_lin = m.addVars(C, F, K, vtype=vt_aux, lb=0.0, ub=1.0, name="y_lin")

    A_e1 = m.addVars(e1_nodes, K, lb=0.0, ub=time_horizon, name="A_e1")
    Q_k = m.addVars(e1_nodes, K, lb=0.0, ub=problem.e1_weight_cap, name="Q_k")
    Vol_k = m.addVars(e1_nodes, K, lb=0.0, ub=problem.e1_volume_cap, name="Vol_k")

    S = m.addVars(route_ids, lb=0.0, name="route_start")
    R = m.addVars(route_ids, lb=0.0, name="route_return")
    A2 = m.addVars(e2_task_ids, lb=0.0, ub=time_horizon, name="e2_arrival")

    # ======================== Objective ========================
    obj_e1 = gp.quicksum(
        dist_map[i, j] * x[i, j, k]
        for k in K for i in e1_nodes for j in e1_nodes if i != j)
    obj_e2 = gp.quicksum(cols[rid]["cost"] * y_route[rid] for rid in route_ids)
    m.setObjective(obj_e1 + obj_e2, GRB.MINIMIZE)

    if incumbent_cost is not None:
        m.addConstr(obj_e1 + obj_e2 <= float(incumbent_cost), "IncumbentCap")

    # ======================== Assignment constraints ========================
    # Each customer assigned to exactly one satellite
    for c in C:
        m.addConstr(gp.quicksum(w[c, s] for s in F) == 1, f"Assign_{c}")

    # ======================== E1 arc-flow (optional satellite visits) ========================
    for i in e1_nodes:
        for k in K:
            m.addConstr(x[i, i, k] == 0, f"E1_NoSelf_{i}_{k}")

    for k in K:
        m.addConstr(A_e1[depot_id, k] == 0.0, f"E1_DepotTime_{k}")
        m.addConstr(
            gp.quicksum(x[depot_id, j, k]
                        for j in e1_del_nodes + e1_pick_nodes) <= 1,
            f"E1_Start_{k}")
        m.addConstr(
            gp.quicksum(x[i, depot_id, k]
                        for i in e1_del_nodes + e1_pick_nodes) <= 1,
            f"E1_End_{k}")
        for h in e1_del_nodes + e1_pick_nodes:
            m.addConstr(
                gp.quicksum(x[i, h, k] for i in e1_nodes if i != h)
                == gp.quicksum(x[h, j, k] for j in e1_nodes if j != h),
                f"E1_Flow_{h}_{k}")
        depot_out = gp.quicksum(
            x[depot_id, jj, k] for jj in e1_del_nodes + e1_pick_nodes)
        for j in e1_del_nodes + e1_pick_nodes:
            m.addConstr(
                gp.quicksum(x[i, j, k] for i in e1_nodes if i != j) <= depot_out,
                f"E1_Subtour_{j}_{k}")

    # Vehicle symmetry
    for idx in range(len(K) - 1):
        m.addConstr(
            gp.quicksum(x[depot_id, j, K[idx]]
                        for j in e1_del_nodes + e1_pick_nodes)
            >= gp.quicksum(x[depot_id, j, K[idx + 1]]
                           for j in e1_del_nodes + e1_pick_nodes),
            f"E1_VehicleSym_{idx}")

    # Forbid same-satellite pick -> del
    for s in F:
        for k in K:
            m.addConstr(x[f"{s}p", f"{s}d", k] == 0, f"E1_ForbidPD_{s}_{k}")

    # Optional satellite visits: served_s[s] = 1 iff any customer assigned to s
    for s in F:
        for c in C:
            m.addConstr(served_s[s] >= w[c, s], f"Served_ge_{c}_{s}")
        m.addConstr(served_s[s] <= gp.quicksum(w[c, s] for c in C),
                    f"Served_le_{s}")
        # Delivery node visited iff satellite is served
        m.addConstr(
            gp.quicksum(x[i, f"{s}d", k]
                        for k in K for i in e1_nodes if i != f"{s}d")
            == served_s[s], f"E1_DelVisit_{s}")
        # Pickup node visited iff satellite is served
        m.addConstr(
            gp.quicksum(x[i, f"{s}p", k]
                        for k in K for i in e1_nodes if i != f"{s}p")
            == served_s[s], f"E1_PickVisit_{s}")

    # ======================== E1 initial load linearization ========================
    # y_lin[c, s, k] = w[c, s] ∧ (truck k visits S{s}d)
    visits_d = {}
    for s in F:
        for k in K:
            visits_d[s, k] = gp.quicksum(
                x[i, f"{s}d", k] for i in e1_nodes if i != f"{s}d")
    for c in C:
        for s in F:
            for k in K:
                m.addConstr(y_lin[c, s, k] <= w[c, s], f"Y_ub1_{c}_{s}_{k}")
                m.addConstr(y_lin[c, s, k] <= visits_d[s, k], f"Y_ub2_{c}_{s}_{k}")
                m.addConstr(
                    y_lin[c, s, k] >= w[c, s] + visits_d[s, k] - 1,
                    f"Y_lb_{c}_{s}_{k}")

    total_del_w = sum(cust_w[c] for c in C)
    total_del_v = len(C) * cv

    for k in K:
        m.addConstr(
            Q_k[depot_id, k]
            == gp.quicksum(cust_w[c] * y_lin[c, s, k] for c in C for s in F),
            f"E1_InitW_{k}")
        m.addConstr(
            Vol_k[depot_id, k]
            == gp.quicksum(cv * y_lin[c, s, k] for c in C for s in F),
            f"E1_InitV_{k}")

    m.addConstr(gp.quicksum(Q_k[depot_id, k] for k in K) == total_del_w,
                "E1_TotalWeight")
    m.addConstr(gp.quicksum(Vol_k[depot_id, k] for k in K) == total_del_v,
                "E1_TotalVol")

    for idx in range(len(K) - 1):
        m.addConstr(Q_k[depot_id, K[idx]] >= Q_k[depot_id, K[idx + 1]],
                    f"E1_LoadSym_{idx}")

    # ======================== E1 load MTZ ========================
    def e1_q_expr(node_id):
        if node_id.endswith("d") and node_id[:-1] in F:
            s = node_id[:-1]
            return gp.quicksum(cust_w[c] * w[c, s] for c in C)
        if node_id.endswith("p") and node_id[:-1] in F:
            s = node_id[:-1]
            return -gp.quicksum(cw * w[c, s] for c in C)
        return None

    for i in e1_nodes:
        for j in e1_del_nodes + e1_pick_nodes:
            if i == j:
                continue
            q_j = e1_q_expr(j)
            if j.endswith("d"):
                v_j = gp.quicksum(cv * w[c, j[:-1]] for c in C)
            else:
                v_j = -gp.quicksum(cv * w[c, j[:-1]] for c in C)
            M_w = problem.e1_weight_cap + max(total_del_w, 1.0)
            M_v = problem.e1_volume_cap + max(total_del_v, 1.0)
            for k in K:
                m.addConstr(
                    Q_k[j, k] <= Q_k[i, k] - q_j + M_w * (1 - x[i, j, k]),
                    f"E1_W_up_{i}_{j}_{k}")
                m.addConstr(
                    Q_k[j, k] >= Q_k[i, k] - q_j - M_w * (1 - x[i, j, k]),
                    f"E1_W_lo_{i}_{j}_{k}")
                m.addConstr(
                    Vol_k[j, k] <= Vol_k[i, k] - v_j + M_v * (1 - x[i, j, k]),
                    f"E1_V_up_{i}_{j}_{k}")
                m.addConstr(
                    Vol_k[j, k] >= Vol_k[i, k] - v_j - M_v * (1 - x[i, j, k]),
                    f"E1_V_lo_{i}_{j}_{k}")

    # ======================== E1 time MTZ ========================
    for i in e1_nodes:
        for j in e1_del_nodes + e1_pick_nodes:
            if i == j or (j.endswith("d") and i == f"{j[:-1]}p"):
                continue
            for k in K:
                m.addConstr(
                    A_e1[j, k] >= (A_e1[i, k] + e1_svc.get(i, 0.0)
                                   + time_map[i, j])
                    - M_time * (1 - x[i, j, k]),
                    f"E1_Time_{i}_{j}_{k}")

    # ======================== E1 delivery deadline implication ========================
    # w[c,s]=1 => A_e1[S{s}d] <= tw_del[c] (for the truck visiting S{s}d)
    for s in F:
        for c in C:
            l_d = cust_tw_del[c][1]
            if l_d >= inf:
                continue
            for k in K:
                m.addConstr(
                    A_e1[f"{s}d", k]
                    <= l_d * visits_d[s, k] + M_time * (1 - w[c, s]),
                    f"E1_Due_{c}_{s}_{k}")

    # ======================== TFp ========================
    if is_tfp_active(TFp):
        for s in F:
            for k in K:
                pick_visits = gp.quicksum(
                    x[i, f"{s}p", k] for i in e1_nodes if i != f"{s}p")
                m.addConstr(
                    A_e1[f"{s}p", k] >= TFp - M_time * (1 - pick_visits),
                    f"E1_TFp_{s}_{k}")

    # ======================== E2 route coverage linked to w[c,s] ========================
    # MERGED coverage: one constraint per (c, s) instead of per-task.
    # A route "covers" customer c if it visits {c}d OR {c}p (LIFO guarantees both).
    # This eliminates dual degeneracy from the d/p split (single dual per c,s).
    route_by_cust_sat = {}  # (cust_id, sat_id) -> [route_ids]
    for rid, col in enumerate(cols):
        sat_id = col["sat_id"]
        # Extract unique customer IDs from tasks (deduplicate d/p pairs)
        seen_custs = set()
        for task_id in col["tasks"]:
            if task_id.endswith("d") or task_id.endswith("p"):
                cust_id = task_id[:-1]
                if cust_id not in seen_custs:
                    seen_custs.add(cust_id)
                    route_by_cust_sat.setdefault((cust_id, sat_id), []).append(rid)

    for c in C:
        for s in F:
            rids = route_by_cust_sat.get((c, s), [])
            m.addConstr(
                gp.quicksum(y_route[rid] for rid in rids) == w[c, s],
                f"E2_Cov_{c}_{s}")

    # ======================== E2 vehicle count per satellite ========================
    for s in F:
        sat_routes = [rid for rid, col in enumerate(cols) if col["sat_id"] == s]
        m.addConstr(
            gp.quicksum(y_route[rid] for rid in sat_routes)
            <= vehicles_per_satellite,
            f"E2_VehicleLimit_{s}")

    # ======================== E2 route timing ========================
    M_e2 = M_time
    for rid, col in enumerate(cols):
        path = col["path"]
        sat_id = col["sat_id"]
        m.addConstr(S[rid] <= M_e2 * y_route[rid], f"E2_StartUsed_{rid}")
        m.addConstr(R[rid] <= M_e2 * y_route[rid], f"E2_ReturnUsed_{rid}")
        prev = sat_id
        for node in path[1:-1]:
            travel = problem.travel_time(
                sat_id if prev == sat_id else e2_task_map[prev].cust,
                e2_task_map[node].cust)
            if prev == sat_id:
                m.addConstr(
                    A2[node] >= S[rid] + travel - M_e2 * (1 - y_route[rid]),
                    f"E2_RouteTime_{rid}_{node}")
            else:
                svc_prev = e2_svc.get(prev, 0.0)
                m.addConstr(
                    A2[node] >= A2[prev] + svc_prev + travel
                    - M_e2 * (1 - y_route[rid]),
                    f"E2_RouteTime_{rid}_{prev}_{node}")
            prev = node
        if len(path) > 2:
            last = path[-2]
            return_time = problem.travel_time(e2_task_map[last].cust, sat_id)
            svc_last = e2_svc.get(last, 0.0)
            m.addConstr(
                R[rid] >= A2[last] + svc_last + return_time
                - M_e2 * (1 - y_route[rid]),
                f"E2_ReturnLo_{rid}")

    # ======================== E2 time windows ========================
    for task_id in e2_task_ids:
        task = e2_task_map[task_id]
        e_j, l_j = task.tw
        m.addConstr(A2[task_id] >= e_j, f"E2_TWlo_{task_id}")
        if l_j < inf:
            m.addConstr(A2[task_id] <= l_j, f"E2_TWhi_{task_id}")

    # ======================== E2 customer precedence (d before p) ========================
    for c in C:
        d_id, p_id = f"{c}d", f"{c}p"
        if d_id not in e2_task_map or p_id not in e2_task_map:
            continue
        svc_d = e2_svc.get(d_id, 0.0)
        m.addConstr(A2[p_id] >= A2[d_id] + svc_d, f"E2_CustPrec_{c}")

    # ======================== E1-E2 linking ========================
    routes_by_sat = {}
    for rid, col in enumerate(cols):
        routes_by_sat.setdefault(col["sat_id"], []).append(rid)

    for sat_id, route_ids_sat in routes_by_sat.items():
        del_node = f"{sat_id}d"
        pick_node = f"{sat_id}p"
        service_del = e1_svc.get(del_node, 0.0)
        for rid in route_ids_sat:
            for k in K:
                visits_del = gp.quicksum(
                    x[i, del_node, k] for i in e1_nodes if i != del_node)
                m.addConstr(
                    S[rid] >= A_e1[del_node, k] + service_del
                    - M_time * (2 - y_route[rid] - visits_del),
                    f"Link_Start_{sat_id}_{rid}_{k}")
            if pick_node in e1_svc:
                for k in K:
                    visits_pick = gp.quicksum(
                        x[i, pick_node, k] for i in e1_nodes if i != pick_node)
                    m.addConstr(
                        A_e1[pick_node, k] >= R[rid]
                        - M_time * (2 - y_route[rid] - visits_pick),
                        f"Link_Pickup_{sat_id}_{rid}_{k}")

    # ======================== Warm start ========================
    vars_dict = {
        "w": w, "route": y_route, "x": x, "served_s": served_s,
        "y_lin": y_lin, "A_e1": A_e1, "Q_k": Q_k, "Vol_k": Vol_k,
        "route_start": S, "route_return": R, "e2_arrival": A2,
    }
    ctx = {
        "depot_id": depot_id, "F": F, "C": C, "K": K,
        "e1_nodes": e1_nodes, "e1_del_nodes": e1_del_nodes,
        "e1_pick_nodes": e1_pick_nodes, "e2_task_ids": e2_task_ids,
        "e2_task_map": e2_task_map, "V_by_sat": V_by_sat,
        "cols": cols, "M_time": M_time,
    }
    return m, vars_dict, ctx


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------

def _apply_warm_start(m, vars_dict, ctx, e1_routes, e2_routes_by_sat,
                      assignment, verbose=True):
    """Apply ALNS solution as Gurobi MIP start."""
    from models.model_2.route_pool_exact import _normalize_columns
    w = vars_dict["w"]
    y_route = vars_dict["route"]
    x = vars_dict["x"]
    served_s = vars_dict["served_s"]
    cols = ctx["cols"]
    depot_id = ctx["depot_id"]
    e1_nodes = ctx["e1_nodes"]
    K = ctx["K"]
    F = ctx["F"]

    # Build column lookup: path -> rid
    path_to_rid = {col["path"]: rid for rid, col in enumerate(cols)}

    # Assignment warm start
    for c, s in assignment.items():
        w[c, s].Start = 1.0
        for s2 in F:
            if s2 != s:
                w[c, s2].Start = 0.0

    # E1 routes warm start
    for k_idx, route in enumerate(e1_routes):
        if k_idx >= len(K):
            break
        k = K[k_idx]
        if len(route) < 2:
            continue
        for i in range(len(route) - 1):
            ni, nj = route[i], route[i + 1]
            if (ni, nj, k) in x:
                x[ni, nj, k].Start = 1.0

    # served_s warm start
    served_sats = set(assignment.values())
    for s in F:
        served_s[s].Start = 1.0 if s in served_sats else 0.0

    # E2 routes warm start: match ALNS routes to columns
    matched = 0
    for sat_id, routes in e2_routes_by_sat.items():
        for route_path in routes:
            path = tuple(route_path)
            if path in path_to_rid:
                y_route[path_to_rid[path]].Start = 1.0
                matched += 1

    if verbose:
        print(f"  Warm start: {len(assignment)} assignments, "
              f"{matched}/{sum(len(r) for r in e2_routes_by_sat.values())} "
              f"E2 routes matched")


def solve_m6_exact(problem, vehicles_per_satellite, *,
                    incumbent_cost, e1_routes=None, e2_routes_by_sat=None,
                    assignment=None, time_limit=600, gurobi_params=None,
                    verbose=True, return_info=False):
    """Solve the supplied M6 route pool to MIP optimality.

    Generates singleton columns + ALNS-extracted columns, builds the
    route-pool master with free assignment, and solves that restricted master.
    This legacy function name is retained for compatibility; its metadata
    never labels the result as a global exact optimum.

    Returns (e1_routes, e2_routes_by_sat, assignment, info).
    """
    started = __import__("time").time()

    # 1. Generate columns: singletons + ALNS-extracted
    satellites = list(problem.satellites)
    singleton_cols, _ = _generate_singleton_columns(
        problem, problem.customers, satellites)
    print(f"Generated {len(singleton_cols)} singleton columns")

    alns_cols = []
    if e2_routes_by_sat:
        alns_cols = _extract_route_columns_from_alns(
            problem, e2_routes_by_sat, assignment or {})
        print(f"Extracted {len(alns_cols)} columns from ALNS solution")

    all_cols = singleton_cols + alns_cols
    print(f"Total columns: {len(all_cols)}")

    # 2. Build model
    m, vars_dict, ctx = build_m6_route_master(
        problem, all_cols, vehicles_per_satellite,
        TFp=9999.0, relax=False, incumbent_cost=incumbent_cost * 1.05)

    # 3. Apply warm start
    if e1_routes and e2_routes_by_sat and assignment:
        _apply_warm_start(m, vars_dict, ctx, e1_routes, e2_routes_by_sat,
                          assignment, verbose=verbose)

    # 4. Set Gurobi params
    if time_limit:
        m.setParam("TimeLimit", time_limit)
    m.setParam("MIPGap", 0.0)
    m.setParam("Presolve", 2)
    m.setParam("Cuts", 2)
    if gurobi_params:
        for name, value in gurobi_params.items():
            m.setParam(name, value)

    if verbose:
        print(f"\nM6 Route-Pool Master: {len(vars_dict['w'])} w-vars, "
              f"{len(vars_dict['route'])} route-vars, "
              f"{len(vars_dict['x'])} x-vars")
        print("Starting optimization...")

    m.optimize()

    elapsed = __import__("time").time() - started
    if m.status == GRB.OPTIMAL:
        solution_type = "restricted_route_pool_optimal"
    elif getattr(m, "SolCount", 0) > 0:
        solution_type = "restricted_route_pool_feasible_not_proven"
    else:
        solution_type = "none"

    info = {
        "status_code": m.status,
        "status": _status_name(m.status),
        "solution_type": solution_type,
        "proven_optimal": False,
        "proof_scope": "restricted_route_pool_only",
        "route_universe_complete": False,
        "runtime": getattr(m, "Runtime", None),
        "sol_count": getattr(m, "SolCount", 0),
        "objective": None,
        "best_bound": None,
        "mip_gap": None,
        "elapsed": elapsed,
        "columns": len(all_cols),
    }
    if m.SolCount > 0:
        info["objective"] = m.ObjVal
        info["mip_gap"] = m.MIPGap
        try:
            info["best_bound"] = m.ObjBound
        except Exception:
            pass

    if m.SolCount == 0:
        if verbose:
            print(f"M6 route-pool: no feasible solution (status={info['status']})")
        if return_info:
            return {"e1_routes": None, "e2_routes": None,
                    "assignment": None, "info": info}
        return None, None, None, info

    # 5. Extract solution
    depot_id = ctx["depot_id"]
    F = ctx["F"]
    K = ctx["K"]
    cols = ctx["cols"]

    sol_x = {key: var.X for key, var in vars_dict["x"].items()}
    e1_routes = [route for _, route in extract_routes_e1(
        sol_x, depot_id, ctx["e1_del_nodes"] + ctx["e1_pick_nodes"], K)]

    # E2 routes from selected columns
    e2_routes_by_sat = {s: [] for s in F}
    for rid, col in enumerate(cols):
        if vars_dict["route"][rid].X > 0.5:
            e2_routes_by_sat[col["sat_id"]].append(list(col["path"]))

    # Assignment
    w = vars_dict["w"]
    assignment = {}
    for c in ctx["C"]:
        for s in F:
            if w[c, s].X > 0.5:
                assignment[c] = s
                break

    info["assignment"] = assignment

    if verbose:
        print(f"\nM6 Route-Pool Result:")
        print(f"  Status: {info['status']}")
        print(f"  Objective: {info['objective']:.4f}" if info["objective"] else "  Objective: N/A")
        print("  Route-pool optimal: "
              f"{info['status'] == 'OPTIMAL'} (not a global proof)")
        print(f"  MIP Gap: {info['mip_gap']:.4%}" if info.get("mip_gap") else "")
        print(f"  Best bound: {info['best_bound']:.4f}" if info.get("best_bound") else "")
        print(f"  Runtime: {info['runtime']:.1f}s" if info.get("runtime") else "")
        print(f"  Assignment: " + ", ".join(
            f"{s}:{sum(1 for v in assignment.values() if v == s)}"
            for s in F))

    if return_info:
        return {
            "e1_routes": e1_routes,
            "e2_routes": e2_routes_by_sat,
            "assignment": assignment,
            "info": info,
        }
    return e1_routes, e2_routes_by_sat, assignment, info


# ===========================================================================
# Column Generation for M6 (true exact solving with pickup & delivery)
# ===========================================================================

def _solve_m6_pricing_mip(problem, sat_id, customers, cover_duals, vehicle_dual,
                          TFp=9999.0, pricing_time_limit=30.0, pool_solutions=10,
                          output_flag=False):
    """Solve the pricing subproblem for satellite s (M6 with pickup & delivery).

    Find a feasible E2 route starting/ending at sat_id that visits a subset
    of customer delivery+pickup task pairs, satisfying:
      - Time windows
      - Capacity (weight + volume, with pickup decreasing load)
      - Precedence: delivery before pickup for same customer (LIFO)
      - Single satellite depot

    Minimizes reduced cost:
        rc = route_cost - Σ_{t in route} π[t,s] - σ_s

    where π[t,s] = cover_duals[t] (dual of E2_Cov_{t}_{s}, t = {c}d or {c}p)
          σ_s     = vehicle_dual  (dual of E2_VehicleLimit_{s})
    """
    started = time.time()
    cw = problem.fleet.container_weight
    cv = problem.fleet.container_volume
    inf = float("inf")
    eps = 1e-6

    # Build tasks: each customer has d and p
    task_ids = []
    task_map = {}
    for c in customers:
        task_ids.append(f"{c.id}d")
        task_map[f"{c.id}d"] = type("T", (), {
            "id": f"{c.id}d", "cust": c.id, "kind": "d",
            "w": c.weight_del + cw, "v": cv, "tw": c.tw_del,
        })()
        task_ids.append(f"{c.id}p")
        task_map[f"{c.id}p"] = type("T", (), {
            "id": f"{c.id}p", "cust": c.id, "kind": "p",
            "w": cw, "v": cv, "tw": c.tw_pick,
        })()

    nodes = [sat_id] + task_ids
    max_latest = max(
        [t.tw[1] for t in task_map.values() if t.tw[1] < inf] + [0.0]
    )
    m_time = max(100000.0, max_latest + 10000.0)

    m = gp.Model(f"M6_Pricing_{sat_id}")
    m.setParam("OutputFlag", 1 if output_flag else 0)
    m.setParam("TimeLimit", pricing_time_limit)
    m.setParam("MIPGap", 0.0)
    if pool_solutions and pool_solutions > 1:
        m.setParam("PoolSearchMode", 2)
        m.setParam("PoolSolutions", int(pool_solutions))

    # Variables
    y = m.addVars(task_ids, vtype=GRB.BINARY, name="y")
    z = m.addVars(
        [(i, j) for i in nodes for j in nodes if i != j],
        vtype=GRB.BINARY, name="z",
    )
    A = {node: m.addVar(lb=0.0, ub=m_time, name=f"A[{node}]") for node in nodes}
    Q = m.addVars(nodes, lb=0.0, ub=problem.e2_weight_cap, name="Q")
    Vol = m.addVars(nodes, lb=0.0, ub=problem.e2_volume_cap, name="Vol")
    order = m.addVars(task_ids, lb=0.0, ub=len(task_ids), name="ord")

    # Depot departure time = 0
    m.addConstr(A[sat_id] == 0.0, name="sat_time")

    # Route structure
    m.addConstr(gp.quicksum(z[sat_id, j] for j in task_ids) == 1, name="start_once")
    m.addConstr(gp.quicksum(z[i, sat_id] for i in task_ids) == 1, name="return_once")

    for h in task_ids:
        incoming = gp.quicksum(z[i, h] for i in nodes if i != h)
        outgoing = gp.quicksum(z[h, j] for j in nodes if j != h)
        m.addConstr(incoming == y[h], name=f"in_{h}")
        m.addConstr(outgoing == y[h], name=f"out_{h}")

        earliest, latest = task_map[h].tw
        m.addConstr(A[h] >= float(earliest) * y[h], name=f"tw_lb_{h}")
        ub = float(latest) if latest < inf else m_time
        m.addConstr(A[h] <= ub * y[h], name=f"tw_ub_{h}")
        m.addConstr(order[h] >= y[h], name=f"ord_lb_{h}")
        m.addConstr(order[h] <= len(task_ids) * y[h], name=f"ord_used_{h}")
        m.addConstr(Q[h] <= problem.e2_weight_cap * y[h], name=f"q_used_{h}")
        m.addConstr(Vol[h] <= problem.e2_volume_cap * y[h], name=f"vol_used_{h}")

    # MTZ subtour elimination
    for i in task_ids:
        for j in task_ids:
            if i == j:
                continue
            m.addConstr(
                order[j] >= order[i] + 1 - len(task_ids) * (1 - z[i, j]),
                name=f"mtz_{i}_{j}",
            )
    for j in task_ids:
        m.addConstr(
            order[j] >= 1 - len(task_ids) * (1 - z[sat_id, j]),
            name=f"start_order_{j}",
        )

    # Initial load: sum of delivery demands
    del_tasks = [t for t in task_ids if task_map[t].kind == "d"]
    pick_tasks = [t for t in task_ids if task_map[t].kind == "p"]
    m.addConstr(
        Q[sat_id] == gp.quicksum(task_map[t].w * y[t] for t in del_tasks),
        name="initial_weight",
    )
    m.addConstr(
        Vol[sat_id] == gp.quicksum(task_map[t].v * y[t] for t in del_tasks),
        name="initial_volume",
    )
    # Total pickup capacity check
    m.addConstr(
        gp.quicksum(task_map[t].w * y[t] for t in pick_tasks)
        <= problem.e2_weight_cap,
        name="total_pickup_weight_cap",
    )
    m.addConstr(
        gp.quicksum(task_map[t].v * y[t] for t in pick_tasks)
        <= problem.e2_volume_cap,
        name="total_pickup_volume_cap",
    )

    # Time propagation + capacity along arcs
    for i_node in nodes:
        s_i = 0.0 if i_node == sat_id else getattr(
            problem.nodes.get(task_map[i_node].cust), "service_time", 0.0)
        for j in task_ids:
            if i_node == j:
                continue
            phys_i = sat_id if i_node == sat_id else task_map[i_node].cust
            phys_j = task_map[j].cust
            travel = problem.travel_time(phys_i, phys_j)

            earliest_i = 0.0 if i_node == sat_id else task_map[i_node].tw[0]
            latest_j = task_map[j].tw[1]
            if latest_j < inf and earliest_i + s_i + travel > latest_j + eps:
                z[i_node, j].UB = 0.0
                continue

            arc_m = max(0.0, m_time + s_i + travel)
            m.addConstr(
                A[j] >= A[i_node] + s_i + travel - arc_m * (1 - z[i_node, j]),
                name=f"time_{i_node}_{j}",
            )
            q_j = task_map[j].w if task_map[j].kind == "d" else -task_map[j].w
            v_j = task_map[j].v if task_map[j].kind == "d" else -task_map[j].v
            m_w = problem.e2_weight_cap + abs(q_j)
            m_v = problem.e2_volume_cap + abs(v_j)
            m.addConstr(
                Q[j] <= Q[i_node] - q_j + m_w * (1 - z[i_node, j]),
                name=f"q_up_{i_node}_{j}",
            )
            m.addConstr(
                Q[j] >= Q[i_node] - q_j - m_w * (1 - z[i_node, j]),
                name=f"q_lo_{i_node}_{j}",
            )
            m.addConstr(
                Vol[j] <= Vol[i_node] - v_j + m_v * (1 - z[i_node, j]),
                name=f"vol_up_{i_node}_{j}",
            )
            m.addConstr(
                Vol[j] >= Vol[i_node] - v_j - m_v * (1 - z[i_node, j]),
                name=f"vol_lo_{i_node}_{j}",
            )

    # Precedence: delivery before pickup for same customer
    for c in customers:
        d_id = f"{c.id}d"
        p_id = f"{c.id}p"
        if d_id in task_map and p_id in task_map:
            svc_d = getattr(problem.nodes.get(c.id), "service_time", 0.0)
            prec_m = max(0.0, m_time + svc_d)
            m.addConstr(
                A[p_id] >= A[d_id] + svc_d - prec_m * (2 - y[d_id] - y[p_id]),
                name=f"prec_{c.id}",
            )
            m.addConstr(
                order[p_id] >= order[d_id] + 1
                - len(task_ids) * (2 - y[d_id] - y[p_id]),
                name=f"prec_order_{c.id}",
            )
            # Can't go directly p→d (same customer, LIFO)
            if (p_id, d_id) in z:
                m.addConstr(z[p_id, d_id] == 0, name=f"forbid_pd_{c.id}")

    # Per-customer visit variables (for merged d/p coverage duals)
    # y_visit[c] = 1 iff customer c is on this route (both d and p visited)
    # Since LIFO guarantees d and p are both visited, y_visit[c] = y[{c}d] = y[{c}p]
    for c in customers:
        d_id = f"{c.id}d"
        p_id = f"{c.id}p"
        if d_id in y and p_id in y:
            m.addConstr(y[d_id] == y[p_id], name=f"visit_pair_{c.id}")

    # Objective: reduced cost (per-customer merged duals)
    route_cost = gp.quicksum(
        problem.distance(
            sat_id if i_node == sat_id else task_map[i_node].cust,
            sat_id if j == sat_id else task_map[j].cust,
        ) * z[i_node, j]
        for (i_node, j) in z.keys()
    )
    # Use delivery task y as proxy for customer visit (equals pickup y due to
    # the visit_pair constraint above)
    dual_reward = gp.quicksum(
        cover_duals.get(c.id, 0.0) * y[f"{c.id}d"] for c in customers
    )
    reduced_cost_expr = route_cost - dual_reward - vehicle_dual
    m.setObjective(reduced_cost_expr, GRB.MINIMIZE)

    m.optimize()

    info = {
        "status": _status_name(m.status),
        "status_code": int(m.status),
        "reduced_cost": float(m.ObjVal) if m.SolCount else None,
        "reduced_cost_bound": (float(m.ObjBound) if m.SolCount
                               else float("inf")),
        "runtime": float(m.Runtime),
        "pool_count": int(m.SolCount),
        "pricing_status_optimal": (m.status == GRB.OPTIMAL),
    }

    if not m.SolCount:
        return [], info

    # Extract route(s)
    cols = []
    seen_paths = set()
    sol_count = min(int(m.SolCount), int(pool_solutions or 1))
    for sol_idx in range(sol_count):
        if sol_idx:
            m.setParam("SolutionNumber", sol_idx)
            sol_z = {key: var.Xn for key, var in z.items()}
        else:
            sol_z = {key: var.X for key, var in z.items()}

        path = [sat_id]
        current = sat_id
        visited = {sat_id}
        while True:
            nxt = None
            for j in nodes:
                if j != current and sol_z.get((current, j), 0.0) > 0.5:
                    nxt = j
                    break
            if nxt is None or nxt == sat_id:
                path.append(sat_id)
                break
            if nxt in visited:
                path.append(sat_id)
                break
            path.append(nxt)
            visited.add(nxt)
            current = nxt

        path_tuple = tuple(path)
        if path_tuple in seen_paths:
            continue
        seen_paths.add(path_tuple)

        covered = tuple(n for n in path[1:-1] if n in task_map)
        if not covered:
            continue
        actual_cost = _route_cost_m6(problem, sat_id, path, task_map)
        # Extract unique customer IDs from covered tasks
        covered_custs = set()
        for t in covered:
            if t.endswith("d") or t.endswith("p"):
                covered_custs.add(t[:-1])
        rc = (actual_cost
              - sum(cover_duals.get(c, 0.0) for c in covered_custs)
              - vehicle_dual)

        if rc >= -eps:
            continue

        cols.append({
            "sat_id": sat_id,
            "path": path_tuple,
            "tasks": covered,
            "cost": actual_cost,
            "source": "cg_pricing",
            "reduced_cost": rc,
        })

    return cols, info


def _route_cost_m6(problem, sat_id, path, task_map):
    """Compute total distance of an E2 route."""
    total = 0.0
    prev = sat_id
    for node_id in path[1:]:
        phys = task_map.get(node_id)
        phys_loc = phys.cust if phys is not None else node_id
        total += problem.distance(prev, phys_loc)
        prev = phys_loc
    return total


def _extract_m6_duals(m, vars_dict, ctx):
    """Extract LP duals from solved M6 master for column generation.

    Returns per-customer duals (merged d/p coverage):
        cover_duals: dict {(c, s): π[c,s]} — dual of E2_Cov_{c}_{s}
        vehicle_duals: dict {s: σ_s} — dual of E2_VehicleLimit_{s}
    """
    F = ctx["F"]
    C = ctx["C"]

    cover_duals = {}
    for c in C:
        for s in F:
            cname = f"E2_Cov_{c}_{s}"
            constr = m.getConstrByName(cname)
            if constr is not None:
                cover_duals[(c, s)] = constr.Pi
            else:
                cover_duals[(c, s)] = 0.0

    vehicle_duals = {}
    for s in F:
        cname = f"E2_VehicleLimit_{s}"
        constr = m.getConstrByName(cname)
        if constr is not None:
            vehicle_duals[s] = constr.Pi
        else:
            vehicle_duals[s] = 0.0

    return cover_duals, vehicle_duals


def solve_m6_column_generation(*args, **kwargs):
    """Deprecated diagnostic routine: DO NOT use as an exact method.

    The full M6 route master couples route variables to E1 through timing and
    linking constraints.  Consequently, a pricing problem that only uses
    coverage/vehicle duals does not compute the true reduced cost of a route
    column.  This implementation is retained only as a historical diagnostic.

    For an exact solve, use ``solve_m6_exact`` on a certified complete route
    universe, or the compact arc-flow model for small instances.
    """
    raise NotImplementedError(
        "M6 column generation is not a valid exact method for the full master; "
        "use solve_m6_exact with a certified complete route universe or the "
        "small-instance arc-flow model"
    )
