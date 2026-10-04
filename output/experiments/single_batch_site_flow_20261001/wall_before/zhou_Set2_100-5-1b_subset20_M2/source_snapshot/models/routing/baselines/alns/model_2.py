# -*- coding: utf-8 -*-
"""Restricted route-column master for Model 2 (pure delivery + assignment).

Model 2 = Model 1 (pure delivery, no pickup/return) + Model 4 (w[c,s] is a
decision variable).  This module replaces the arc-flow E2 variables with
route columns and adds free-assignment constraints, exactly as
the historical Model 4 route-pool experiment did for M4, but **without pickup tasks**.

Key differences from M4 route_pool:
  - No pickup tasks: each customer has only a delivery task ``{c}d``
  - No E1 pickup nodes: satellites have only delivery nodes ``S{s}d``
  - No E1-E2 pickup linking: no ``A_e1[S{s}p] >= R[rid]``
  - No E2 customer precedence (d-before-p): no ``A2[p] >= A2[d] + svc``
  - E1 route: depot -> S{s1}d -> S{s2}d -> ... -> depot (pure delivery)

This dramatically reduces the model size, but the generated singleton/ALNS
columns are not a complete route universe.  Thus an ``OPTIMAL`` status proves
only the restricted master, never the global Model 2 optimum:
  M4: 100 tasks (50d + 50p), M2: 50 tasks (50d only)
"""
import time

try:
    import gurobipy as gp
    from gurobipy import GRB
except ModuleNotFoundError:  # ALNS remains available without the optional exact solver.
    gp = None
    GRB = None

from models.routing.common.exact_utils import extract_routes_e1


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


def _route_cost(problem, sat_id, path, task_map):
    total = 0.0
    prev = sat_id
    for node_id in path[1:]:
        phys = task_map.get(node_id)
        phys_loc = phys.cust if phys is not None else node_id
        total += problem.distance(prev, phys_loc)
        prev = phys_loc
    return total


def _normalize_columns_m2(problem, all_task_map, route_columns):
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


def _singleton_column_m2(problem, sat_id, task_id, task):
    """Single-delivery-task route: sat -> {c}d -> sat."""
    path = (sat_id, task_id, sat_id)
    travel = problem.travel_time(sat_id, task.cust)
    e, l = task.tw
    if l < float("inf") and travel > l + 1e-6:
        return None
    if task.w > problem.e2_weight_cap + 1e-6 or task.v > problem.e2_volume_cap + 1e-6:
        return None
    cost = 2.0 * problem.distance(sat_id, task.cust)
    return {
        "sat_id": sat_id,
        "path": path,
        "tasks": (task_id,),
        "cost": cost,
        "source": "singleton",
    }


def _generate_singleton_columns(problem, customers, satellites):
    """Generate single-task route columns for every (customer, satellite) pair."""
    cw = problem.fleet.container_weight
    cv = problem.fleet.container_volume
    all_task_map = {}
    for c in customers:
        all_task_map[f"{c.id}d"] = type("T", (), {
            "id": f"{c.id}d", "cust": c.id, "kind": "d",
            "w": c.weight_del + cw, "v": cv, "tw": c.tw_del,
        })()
    cols = []
    for s in satellites:
        for c in customers:
            task_id = f"{c.id}d"
            col = _singleton_column_m2(problem, s.id, task_id, all_task_map[task_id])
            if col is not None:
                cols.append(col)
    return cols, all_task_map


def _extract_route_columns_from_alns(problem, e2_routes_by_sat):
    """Extract route columns from ALNS solution (pure delivery, no pickup)."""
    cw = problem.fleet.container_weight
    cv = problem.fleet.container_volume
    cols = []
    for sat_id, routes in e2_routes_by_sat.items():
        for route_path in routes:
            if not route_path or len(route_path) < 3:
                continue
            task_ids = [n for n in route_path[1:-1]]
            if not task_ids:
                continue
            path = tuple(route_path)
            tmp_map = {}
            for tid in task_ids:
                cid = tid[:-1]
                c = next((c for c in problem.customers if c.id == cid), None)
                if c:
                    tmp_map[tid] = type("T", (), {
                        "id": tid, "cust": cid, "kind": "d",
                        "w": c.weight_del + cw, "v": cv, "tw": c.tw_del,
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
# Geometry
# ---------------------------------------------------------------------------

def _setup_geometry(problem, vehicles_per_satellite):
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
    cust_svc = {c.id: c.service_time for c in customers}

    # E1 nodes: depot + satellite delivery nodes only (no pickup!)
    e1_del_nodes = [f"{s}d" for s in F]
    e1_nodes = [depot_id] + e1_del_nodes

    # E2 task IDs: delivery only (no pickup!)
    e2_task_ids = [f"{c}d" for c in C]

    e2_task_map = {}
    for c in customers:
        e2_task_map[f"{c.id}d"] = type("T", (), {
            "id": f"{c.id}d", "cust": c.id, "kind": "d",
            "w": c.weight_del + cw, "v": cv, "tw": c.tw_del,
        })()

    e1_svc = {depot_id: 0.0}
    for s in F:
        e1_svc[f"{s}d"] = getattr(problem.nodes[s], "service_time", 0.0)

    e2_svc = {tid: cust_svc[tid[:-1]] for tid in e2_task_ids}

    all_ids = set([depot_id] + F + e1_del_nodes + e2_task_ids)

    def phys(node_id):
        if node_id in F or node_id == depot_id:
            return node_id
        if len(node_id) > 1 and node_id[-1] == "d":
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

    finite_times = [0.0]
    for c in customers:
        tw = c.tw_del
        if tw[0] < inf:
            finite_times.append(tw[0])
        if tw[1] < inf:
            finite_times.append(tw[1])
    max_arc = max(time_map.values(), default=0.0)
    total_svc = sum(e1_svc.values()) + sum(e2_svc.values())
    task_count = len(e1_nodes) - 1 + len(F) * len(e2_task_ids)
    time_horizon = max(finite_times) + (task_count + 2) * max_arc + total_svc + 1.0
    M_time = time_horizon + max_arc

    V_by_sat = {s: [(s, v) for v in range(vehicles_per_satellite)] for s in F}

    return {
        "depot_id": depot_id, "F": F, "C": C, "K": K,
        "customers": customers,
        "e1_nodes": e1_nodes, "e1_del_nodes": e1_del_nodes,
        "e2_task_ids": e2_task_ids, "e2_task_map": e2_task_map,
        "e1_svc": e1_svc, "e2_svc": e2_svc,
        "dist_map": dist_map, "time_map": time_map,
        "M_time": M_time, "time_horizon": time_horizon,
        "V_by_sat": V_by_sat, "vehicles_per_satellite": vehicles_per_satellite,
        "cust_w": cust_w, "cust_tw_del": cust_tw_del,
        "cust_svc": cust_svc, "cw": cw, "cv": cv,
        "inf": inf, "problem": problem,
    }


# ---------------------------------------------------------------------------
# Model builder
# ---------------------------------------------------------------------------

def build_m2_route_master(problem, route_columns, vehicles_per_satellite,
                           relax=False, incumbent_cost=None):
    """Build the M2 route-column master with free assignment w[c,s].

    M2 = pure delivery (no pickup) + free assignment.
    """
    geo = _setup_geometry(problem, vehicles_per_satellite)
    depot_id = geo["depot_id"]
    F = geo["F"]
    C = geo["C"]
    K = geo["K"]
    customers = geo["customers"]
    e1_nodes = geo["e1_nodes"]
    e1_del_nodes = geo["e1_del_nodes"]
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
    cw = geo["cw"]
    cv = geo["cv"]
    inf = geo["inf"]

    cols = _normalize_columns_m2(problem, e2_task_map, route_columns)
    route_ids = list(range(len(cols)))

    m = gp.Model("M2_RoutePool_Master")
    if not relax:
        m.setParam("OutputFlag", 1)

    vt = GRB.CONTINUOUS if relax else GRB.BINARY

    # Variables
    w = m.addVars(C, F, vtype=vt, lb=0.0, ub=1.0, name="w")
    y_route = m.addVars(route_ids, vtype=vt, lb=0.0, ub=1.0, name="route")
    x = m.addVars(e1_nodes, e1_nodes, K, vtype=vt, lb=0.0, ub=1.0, name="x")
    served_s = m.addVars(F, vtype=vt, lb=0.0, ub=1.0, name="served_s")
    y_lin = m.addVars(C, F, K, vtype=vt, lb=0.0, ub=1.0, name="y_lin")

    A_e1 = m.addVars(e1_nodes, K, lb=0.0, ub=time_horizon, name="A_e1")
    Q_k = m.addVars(e1_nodes, K, lb=0.0, ub=problem.e1_weight_cap, name="Q_k")
    Vol_k = m.addVars(e1_nodes, K, lb=0.0, ub=problem.e1_volume_cap, name="Vol_k")

    S = m.addVars(route_ids, lb=0.0, name="route_start")
    R = m.addVars(route_ids, lb=0.0, name="route_return")
    A2 = m.addVars(e2_task_ids, lb=0.0, ub=time_horizon, name="e2_arrival")

    # Objective
    obj_e1 = gp.quicksum(
        dist_map[i, j] * x[i, j, k]
        for k in K for i in e1_nodes for j in e1_nodes if i != j)
    obj_e2 = gp.quicksum(cols[rid]["cost"] * y_route[rid] for rid in route_ids)
    m.setObjective(obj_e1 + obj_e2, GRB.MINIMIZE)

    if incumbent_cost is not None:
        m.addConstr(obj_e1 + obj_e2 <= float(incumbent_cost), "IncumbentCap")

    # ---- Assignment: each customer to exactly one satellite ----
    for c in C:
        m.addConstr(gp.quicksum(w[c, s] for s in F) == 1, f"Assign_{c}")

    # ---- E1 arc-flow (optional satellite visits, delivery only) ----
    for i in e1_nodes:
        for k in K:
            m.addConstr(x[i, i, k] == 0, f"E1_NoSelf_{i}_{k}")

    for k in K:
        m.addConstr(A_e1[depot_id, k] == 0.0, f"E1_DepotTime_{k}")
        m.addConstr(
            gp.quicksum(x[depot_id, j, k] for j in e1_del_nodes) <= 1,
            f"E1_Start_{k}")
        m.addConstr(
            gp.quicksum(x[i, depot_id, k] for i in e1_del_nodes) <= 1,
            f"E1_End_{k}")
        for h in e1_del_nodes:
            m.addConstr(
                gp.quicksum(x[i, h, k] for i in e1_nodes if i != h)
                == gp.quicksum(x[h, j, k] for j in e1_nodes if j != h),
                f"E1_Flow_{h}_{k}")
        depot_out = gp.quicksum(x[depot_id, jj, k] for jj in e1_del_nodes)
        for j in e1_del_nodes:
            m.addConstr(
                gp.quicksum(x[i, j, k] for i in e1_nodes if i != j) <= depot_out,
                f"E1_Subtour_{j}_{k}")

    for idx in range(len(K) - 1):
        m.addConstr(
            gp.quicksum(x[depot_id, j, K[idx]] for j in e1_del_nodes)
            >= gp.quicksum(x[depot_id, j, K[idx + 1]] for j in e1_del_nodes),
            f"E1_VehicleSym_{idx}")

    # ---- served_s[s] = 1 iff any customer assigned to s ----
    for s in F:
        for c in C:
            m.addConstr(served_s[s] >= w[c, s], f"Served_ge_{c}_{s}")
        m.addConstr(served_s[s] <= gp.quicksum(w[c, s] for c in C),
                    f"Served_le_{s}")
        m.addConstr(
            gp.quicksum(x[i, f"{s}d", k]
                        for k in K for i in e1_nodes if i != f"{s}d")
            == served_s[s], f"E1_DelVisit_{s}")

    # ---- E1 initial load linearization ----
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

    # ---- E1 load MTZ ----
    def e1_q_expr(node_id):
        if node_id.endswith("d") and node_id[:-1] in F:
            s = node_id[:-1]
            return gp.quicksum(cust_w[c] * w[c, s] for c in C)
        return None

    for i in e1_nodes:
        for j in e1_del_nodes:
            if i == j:
                continue
            q_j = e1_q_expr(j)
            v_j = gp.quicksum(cv * w[c, j[:-1]] for c in C)
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

    # ---- E1 time MTZ ----
    for i in e1_nodes:
        for j in e1_del_nodes:
            if i == j:
                continue
            for k in K:
                m.addConstr(
                    A_e1[j, k] >= (A_e1[i, k] + e1_svc.get(i, 0.0)
                                   + time_map[i, j])
                    - M_time * (1 - x[i, j, k]),
                    f"E1_Time_{i}_{j}_{k}")

    # ---- E1 delivery deadline implication ----
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

    # ---- E2 route coverage linked to w[c,s] ----
    route_by_task_sat = {}
    for rid, col in enumerate(cols):
        sat_id = col["sat_id"]
        for task_id in col["tasks"]:
            route_by_task_sat.setdefault((task_id, sat_id), []).append(rid)

    for c in C:
        task_id = f"{c}d"
        if task_id not in e2_task_map:
            continue
        for s in F:
            rids = route_by_task_sat.get((task_id, s), [])
            m.addConstr(
                gp.quicksum(y_route[rid] for rid in rids) == w[c, s],
                f"E2_Cov_{task_id}_{s}")

    # ---- E2 vehicle count per satellite ----
    for s in F:
        sat_routes = [rid for rid, col in enumerate(cols) if col["sat_id"] == s]
        m.addConstr(
            gp.quicksum(y_route[rid] for rid in sat_routes)
            <= vehicles_per_satellite,
            f"E2_VehicleLimit_{s}")

    # ---- E2 route timing ----
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

    # ---- E2 time windows ----
    for task_id in e2_task_ids:
        task = e2_task_map[task_id]
        e_j, l_j = task.tw
        m.addConstr(A2[task_id] >= e_j, f"E2_TWlo_{task_id}")
        if l_j < inf:
            m.addConstr(A2[task_id] <= l_j, f"E2_TWhi_{task_id}")

    # ---- E1-E2 delivery linking (no pickup!) ----
    routes_by_sat = {}
    for rid, col in enumerate(cols):
        routes_by_sat.setdefault(col["sat_id"], []).append(rid)

    for sat_id, route_ids_sat in routes_by_sat.items():
        del_node = f"{sat_id}d"
        service_del = e1_svc.get(del_node, 0.0)
        for rid in route_ids_sat:
            for k in K:
                visits_del = gp.quicksum(
                    x[i, del_node, k] for i in e1_nodes if i != del_node)
                m.addConstr(
                    S[rid] >= A_e1[del_node, k] + service_del
                    - M_time * (2 - y_route[rid] - visits_del),
                    f"Link_Start_{sat_id}_{rid}_{k}")

    vars_dict = {
        "w": w, "route": y_route, "x": x, "served_s": served_s,
        "y_lin": y_lin, "A_e1": A_e1, "Q_k": Q_k, "Vol_k": Vol_k,
        "route_start": S, "route_return": R, "e2_arrival": A2,
    }
    ctx = {
        "depot_id": depot_id, "F": F, "C": C, "K": K,
        "e1_nodes": e1_nodes, "e1_del_nodes": e1_del_nodes,
        "e2_task_ids": e2_task_ids, "e2_task_map": e2_task_map,
        "V_by_sat": V_by_sat, "cols": cols, "M_time": M_time,
    }
    return m, vars_dict, ctx


"""New M2 ALNS solver: pure-delivery + free assignment.

Adapted from M4 ALNS but with only delivery tasks (no pickup).
"""
import random
import time

from models.routing.common.domain.node import Task
from models.routing.alns.types import (
    Route,
    Solution,
    FEAS_TOL,
    clean_empty_routes,
    is_tfp_active,
)
from models.routing.alns.engine import (
    ALNSConfig,
    ALNSHooks,
    evaluate_solution,
    run_alns_search,
)
from models.routing.alns.assignment_operators import (
    assignment_boundary_removal,
    two_satellite_removal,
)
from models.routing.alns.initial_solution import (
    _build_e1_delivery_routes,
    build_strong_initial_solution,
)
from models.routing.common.task_builder import build_tasks_for_model
from models.routing.baselines.alns.model_1 import Evaluator as _M1Evaluator


class Evaluator:
    """Pure-delivery assignment-aware evaluator for Model 2.

    Same as M4 Evaluator but only delivery tasks (no pickup).
    """

    def __init__(self, problem, vehicles_per_satellite=4):
        self.problem = problem
        self.vehicles_per_satellite = vehicles_per_satellite
        self.sat_ids = [s.id for s in problem.satellites]

        cw = problem.fleet.container_weight
        cv = problem.fleet.container_volume
        self.catalog = {}
        for c in problem.customers:
            self.catalog[c.id] = {
                "d": Task(f"{c.id}d", c.id, "d",
                          c.weight_del + cw, cv, c.tw_del)
            }
        self._reset_maps()
        from models.routing.alns.evaluation_cache import cache_static_evaluator
        cache_static_evaluator(self, catalogue=self.catalog)

    def _reset_maps(self):
        self._active_assignment = None
        self.e1_task_map = {}
        self.e2_task_map = {}
        self.e2_task_sat = {}
        self.e2_tasks_by_sat = {s: [] for s in self.sat_ids}
        self.e1_del_nodes = []
        self.task_to_phys = {}

    def _catalog_task(self, node_id):
        if len(node_id) > 1 and node_id[-1] == "d":
            pair = self.catalog.get(node_id[:-1])
            if pair is not None:
                return pair.get("d")
        return None

    def assignment(self, sol):
        assign = {}
        for sat_id, routes in sol.e2_routes.items():
            for r in routes:
                for nid in r.nodes:
                    task = self._catalog_task(nid)
                    if task is not None:
                        assign[task.cust] = sat_id
        return assign

    def rebuild_from_solution(self, sol):
        assign = self.assignment(sol)
        if assign == self._active_assignment:
            self._canonicalize_e1_routes(sol)
            return
        cw = self.problem.fleet.container_weight
        cv = self.problem.fleet.container_volume

        agg = {
            s: {"count": 0, "del_w": 0.0, "del_v": 0.0,
                "due": float("inf"), "e2_tasks": []}
            for s in self.sat_ids
        }
        for cust_id, sat_id in assign.items():
            pair = self.catalog[cust_id]
            a = agg[sat_id]
            a["count"] += 1
            a["del_w"] += pair["d"].w
            a["del_v"] += pair["d"].v
            a["due"] = min(a["due"], pair["d"].tw[1])
            a["e2_tasks"].append(pair["d"])

        self.e2_tasks_by_sat = {s: [] for s in self.sat_ids}
        self.e2_task_map = {}
        self.e2_task_sat = {}
        for s in self.sat_ids:
            for t in agg[s]["e2_tasks"]:
                self.e2_tasks_by_sat[s].append(t)
                self.e2_task_map[t.id] = t
                self.e2_task_sat[t.id] = s

        self.e1_task_map = {}
        self.e1_del_nodes = []
        for s in self.sat_ids:
            a = agg[s]
            if a["count"] <= 0:
                continue
            d_node = Task(f"{s}d", s, "d", a["del_w"], a["del_v"],
                          (0.0, a["due"]))
            self.e1_task_map[d_node.id] = d_node
            self.e1_del_nodes.append(d_node.id)

        self.task_to_phys = {t.id: t.cust for t in self.e1_task_map.values()}
        for pair in self.catalog.values():
            self.task_to_phys[pair["d"].id] = pair["d"].cust

        self._active_assignment = dict(assign)
        self._canonicalize_e1_routes(sol)

    def _canonicalize_e1_routes(self, sol):
        depot_id = self.problem.depot.id
        required_del = {nid: self.e1_task_map[nid] for nid in self.e1_del_nodes}

        current = {}
        for r in sol.e1_routes:
            for nid in r.nodes:
                t = self.e1_task_map.get(nid)
                if t is not None and t.kind == "d":
                    current[nid] = t

        keep = set(current) == set(required_del)

        sat_urgency = {}
        for s in self.sat_ids:
            due = [t.tw[1] for t in self.e2_tasks_by_sat[s] if t.kind == "d"]
            sat_urgency[s] = min(due) if due else float("inf")

        if keep:
            for r in sol.e1_routes:
                r.nodes = [n for n in r.nodes if n in self.e1_task_map
                           and self.e1_task_map[n].kind == "d"]
            kept_ok = all(self.check_e1_route_feasible(r) for r in sol.e1_routes)
            if not kept_ok:
                del_tasks = [self.e1_task_map[n] for n in self.e1_del_nodes]
                sol.e1_routes = _build_e1_delivery_routes(
                    self.problem, self, depot_id, del_tasks, sat_urgency)
        else:
            del_tasks = [self.e1_task_map[n] for n in self.e1_del_nodes]
            sol.e1_routes = _build_e1_delivery_routes(
                self.problem, self, depot_id, del_tasks, sat_urgency)

        sol.e1_routes = [r for r in sol.e1_routes if r.nodes]

    def e2_task_satellite(self, task_id):
        return self.e2_task_sat.get(task_id)

    def e2_start_times(self, sol):
        e2_start_by_sat, _ = self.e1_schedule(sol)
        return e2_start_by_sat or {}

    def phys(self, node_id):
        return self.task_to_phys.get(node_id, node_id)

    def dist(self, a, b):
        return self.problem.distance(self.phys(a), self.phys(b))

    def travel(self, a, b):
        return self.problem.travel_time(self.phys(a), self.phys(b))

    def service_time(self, node_id):
        phys_id = self.phys(node_id)
        node_obj = self.problem.nodes.get(phys_id)
        return getattr(node_obj, "service_time", 0.0) if node_obj else 0.0

    def signed_demand(self, task):
        return task.w if task.kind == "d" else -task.w

    def signed_volume(self, task):
        return task.v if task.kind == "d" else -task.v

    def route_distance(self, route):
        path = route.full_path()
        total = 0.0
        for i in range(len(path) - 1):
            total += self.dist(path[i], path[i + 1])
        return total

    def total_cost(self, sol, _rebuild=True):
        if _rebuild:
            self.rebuild_from_solution(sol)
        cost = 0.0
        for r in sol.e1_routes:
            cost += self.route_distance(r)
        for sat_id, routes in sol.e2_routes.items():
            for r in routes:
                cost += self.route_distance(r)
        return cost

    def evaluate(self, sol):
        """Return cost and feasibility after one assignment rebuild."""
        self.rebuild_from_solution(sol)
        cost = self.total_cost(sol, _rebuild=False)
        return cost, self.is_feasible(sol, _rebuild=False)

    def check_e1_route_feasible(self, route):
        init_w = 0.0
        init_v = 0.0
        for nid in route.nodes:
            t = self.e1_task_map.get(nid)
            if t and t.kind == "d":
                init_w += t.w
                init_v += t.v
        if init_w > self.problem.e1_weight_cap + FEAS_TOL:
            return False
        if init_v > self.problem.e1_volume_cap + FEAS_TOL:
            return False

        Q_load, V_load = init_w, init_v
        current_time = 0.0
        prev = route.base

        for nid in route.nodes:
            travel = self.travel(prev, nid)
            current_time += travel
            t = self.e1_task_map.get(nid)
            if t:
                if current_time < t.tw[0]:
                    current_time = t.tw[0]
                if current_time > t.tw[1] + FEAS_TOL:
                    return False
                Q_load -= self.signed_demand(t)
                V_load -= self.signed_volume(t)
                if Q_load < -FEAS_TOL or Q_load > self.problem.e1_weight_cap + FEAS_TOL:
                    return False
                if V_load < -FEAS_TOL or V_load > self.problem.e1_volume_cap + FEAS_TOL:
                    return False
                current_time += self.service_time(nid)
            prev = nid
        return True

    def e1_schedule(self, sol):
        delivery_departures = {}
        for route in sol.e1_routes:
            current_time = 0.0
            prev = route.base
            for nid in route.nodes:
                current_time += self.travel(prev, nid)
                task = self.e1_task_map.get(nid)
                if task:
                    if current_time < task.tw[0]:
                        current_time = task.tw[0]
                    if current_time > task.tw[1] + FEAS_TOL:
                        return None, None
                    if task.kind == "d":
                        current_time += self.service_time(nid)
                        delivery_departures[task.cust] = current_time
                prev = nid
        return delivery_departures, {}

    def simulate_e2_route(self, route, sat_id, start_time=0.0):
        init_w = 0.0
        init_v = 0.0
        for nid in route.nodes:
            t = self.e2_task_map.get(nid) or self._catalog_task(nid)
            if t and t.kind == "d":
                init_w += t.w
                init_v += t.v
        if init_w > self.problem.e2_weight_cap + FEAS_TOL:
            return False, {}, None
        if init_v > self.problem.e2_volume_cap + FEAS_TOL:
            return False, {}, None

        Q_load, V_load = init_w, init_v
        current_time = 0.0
        prev = sat_id
        arrivals = {}

        for nid in route.nodes:
            travel = self.travel(prev, nid)
            current_time += travel
            abs_arrival = start_time + current_time
            t = self.e2_task_map.get(nid) or self._catalog_task(nid)
            if t:
                if abs_arrival < t.tw[0]:
                    current_time = t.tw[0] - start_time
                    abs_arrival = t.tw[0]
                if abs_arrival > t.tw[1] + FEAS_TOL:
                    return False, {}, None
                arrivals[nid] = abs_arrival
                Q_load -= self.signed_demand(t)
                V_load -= self.signed_volume(t)
                if Q_load < -FEAS_TOL or Q_load > self.problem.e2_weight_cap + FEAS_TOL:
                    return False, {}, None
                if V_load < -FEAS_TOL or V_load > self.problem.e2_volume_cap + FEAS_TOL:
                    return False, {}, None
                current_time += self.service_time(nid)
            prev = nid

        return_time = start_time + current_time + self.travel(prev, sat_id)
        return True, arrivals, return_time

    def check_e2_route_feasible(self, route, sat_id, start_time):
        feasible, _, _ = self.simulate_e2_route(route, sat_id, start_time)
        return feasible

    def is_feasible(self, sol, _rebuild=True):
        if _rebuild:
            self.rebuild_from_solution(sol)
        assign = self.assignment(sol)
        required_customers = {c.id for c in self.problem.customers}
        if set(assign) != required_customers:
            return False

        if len(sol.e1_routes) > self.problem.e1_vehicle_count:
            return False

        visited_e1 = []
        for r in sol.e1_routes:
            if not self.check_e1_route_feasible(r):
                return False
            visited_e1.extend(r.nodes)
        required_e1 = set(self.e1_task_map)
        if set(visited_e1) != required_e1 or len(visited_e1) != len(required_e1):
            return False

        e1_departures, _ = self.e1_schedule(sol)
        if e1_departures is None:
            return False

        for sat_id, routes in sol.e2_routes.items():
            if len(routes) > self.vehicles_per_satellite:
                return False
            start_time = e1_departures.get(sat_id, 0.0)
            req = {t.id for t in self.e2_tasks_by_sat.get(sat_id, [])}
            has_delivery = any(t.kind == "d" for t in self.e2_tasks_by_sat.get(sat_id, []))
            if routes and req and has_delivery and sat_id not in e1_departures:
                return False
            visited_e2 = []
            for r in routes:
                feasible, _, _ = self.simulate_e2_route(r, sat_id, start_time)
                if not feasible:
                    return False
                visited_e2.extend(r.nodes)
            if set(visited_e2) != req or len(visited_e2) != len(req):
                return False

        return True


def build_tasks_from_assignment(problem, assignment):
    """Given {customer_id: satellite_id}, build pure-delivery e1/e2 tasks."""
    cw = problem.fleet.container_weight
    cv = problem.fleet.container_volume
    sat_ids = [s.id for s in problem.satellites]

    e2_tasks_by_sat = {s: [] for s in sat_ids}
    agg = {
        s: {"count": 0, "del_w": 0.0, "del_v": 0.0, "due": float("inf")}
        for s in sat_ids
    }
    for c in problem.customers:
        sat = assignment.get(c.id)
        if sat is None or sat not in agg:
            continue
        a = agg[sat]
        a["count"] += 1
        a["del_w"] += c.weight_del + cw
        a["del_v"] += cv
        a["due"] = min(a["due"], c.tw_del[1])
        e2_tasks_by_sat[sat].append(
            Task(f"{c.id}d", c.id, "d", c.weight_del + cw, cv, c.tw_del))

    e1_tasks = []
    for s in sat_ids:
        a = agg[s]
        if a["count"] <= 0:
            continue
        e1_tasks.append(Task(f"{s}d", s, "d", a["del_w"], a["del_v"],
                             (0.0, a["due"])))
    return e1_tasks, e2_tasks_by_sat


def _fixed_assignment(problem):
    return {c.id: c.assigned_sat for c in problem.customers if c.assigned_sat}


def _deadline_repaired_assignment(problem, fixed):
    """Repair assignments that cannot meet a delivery deadline even directly.

    The lower-bound path depot->satellite->customer ignores all other work.  If
    it already misses the due time, keeping that assignment cannot be feasible.
    Reassigning only those customers preserves the input partition as much as
    possible while providing a useful large-instance seed.
    """
    repaired = dict(fixed)
    depot_id = problem.depot.id

    def direct_arrival(customer, satellite):
        service = getattr(problem.nodes[satellite.id], "service_time", 0.0)
        return (
            problem.travel_time(depot_id, satellite.id)
            + service
            + problem.travel_time(satellite.id, customer.id)
        )

    for customer in problem.customers:
        current = repaired.get(customer.id)
        current_sat = next(
            (sat for sat in problem.satellites if sat.id == current), None)
        if current_sat is None:
            continue
        due = customer.tw_del[1]
        if direct_arrival(customer, current_sat) <= due + FEAS_TOL:
            continue
        repaired[customer.id] = min(
            problem.satellites,
            key=lambda sat: direct_arrival(customer, sat),
        ).id
    return repaired


def _candidate_seeds(problem):
    seeds = []
    fixed = _fixed_assignment(problem)
    if fixed:
        seeds.append(fixed)
        seeds.append(_deadline_repaired_assignment(problem, fixed))

    nearest = {}
    second = {}
    for c in problem.customers:
        sats = sorted(problem.satellites, key=lambda s: problem.distance(c.id, s.id))
        nearest[c.id] = sats[0].id
        if len(sats) > 1:
            second[c.id] = sats[1].id
    seeds.append(nearest)
    if second and len(problem.customers) <= 20:
        seeds.append(second)
    # Concentrated assignments are important when opening an extra satellite
    # creates more E1 distance than it saves on E2 routes.
    if len(problem.customers) <= 20:
        for satellite in problem.satellites:
            seeds.append({c.id: satellite.id for c in problem.customers})
    else:
        # Large free-assignment instances still need a geometry-based escape
        # seed.  Keeping only the input assignment made the solver start from
        # a structurally infeasible partition on some 200-customer cases.
        # These deterministic seeds are cheap relative to the ALNS budget and
        # make feasibility recovery much more reliable.
        seeds = ([fixed, _deadline_repaired_assignment(problem, fixed), nearest]
                 if fixed else [nearest])

    seen = set()
    out = []
    for s in seeds:
        key = tuple(sorted(s.items()))
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def build_initial_solution(problem, evaluator):
    """Build a feasible initial solution from multiple assignment seeds."""
    vps = evaluator.vehicles_per_satellite
    best_feasible = None
    best_any = None
    for seed in _candidate_seeds(problem):
        e1_tasks, e2_tasks_by_sat = build_tasks_from_assignment(problem, seed)
        m1_eval = _M1Evaluator(problem, e1_tasks, e2_tasks_by_sat, vps)
        sol = build_strong_initial_solution(
            problem, e1_tasks, e2_tasks_by_sat, m1_eval)
        evaluator.rebuild_from_solution(sol)
        sol.cost = evaluator.total_cost(sol)
        if evaluator.is_feasible(sol):
            if (best_feasible is None
                    or sol.cost < best_feasible.cost - FEAS_TOL):
                best_feasible = sol.copy()
        if best_any is None or sol.cost < best_any.cost - FEAS_TOL:
            best_any = sol.copy()
    return best_feasible or best_any


# =====================================================================
#  Cross-satellite destroy/repair operators (delivery only)
# =====================================================================

def _remove_customer(sol, cust_id):
    d_id = f"{cust_id}d"
    for routes in sol.e2_routes.values():
        for r in routes:
            r.nodes = [n for n in r.nodes if n != d_id]
    clean_empty_routes(sol)


def _customers(sol, evaluator):
    return sorted(evaluator.assignment(sol))


def random_customer_removal(sol, evaluator, num_remove, echelon="e2"):
    custs = _customers(sol, evaluator)
    random.shuffle(custs)
    removed = []
    for cust in custs[:num_remove]:
        _remove_customer(sol, cust)
        removed.append(cust)
    return removed


def worst_customer_removal(sol, evaluator, num_remove, echelon="e2"):
    savings = []
    for cust in _customers(sol, evaluator):
        d_id = f"{cust}d"
        saving = 0.0
        for routes in sol.e2_routes.values():
            for r in routes:
                path = r.full_path()
                for idx, nid in enumerate(r.nodes):
                    if nid != d_id:
                        continue
                    pos = idx + 1
                    prev_n = path[pos - 1]
                    next_n = path[pos + 1]
                    saving += (evaluator.dist(prev_n, nid)
                               + evaluator.dist(nid, next_n)
                               - evaluator.dist(prev_n, next_n))
        savings.append((saving, cust))
    savings.sort(key=lambda x: -x[0])
    removed = []
    for saving, cust in savings[:num_remove]:
        _remove_customer(sol, cust)
        removed.append(cust)
    return removed


def shaw_customer_removal(sol, evaluator, num_remove, echelon="e2"):
    custs = _customers(sol, evaluator)
    if not custs:
        return []
    seed = random.choice(custs)
    seed_d = evaluator.catalog[seed]["d"]

    scored = []
    for cust in custs:
        if cust == seed:
            continue
        t = evaluator.catalog[cust]["d"]
        dist_sim = evaluator.dist(f"{seed}d", f"{cust}d")
        tw_sim = abs(seed_d.tw[0] - t.tw[0])
        scored.append((dist_sim + 0.1 * tw_sim, cust))
    scored.sort(key=lambda x: x[0])
    chosen = [seed] + [cust for _, cust in scored[:num_remove - 1]]
    for cust in chosen:
        _remove_customer(sol, cust)
    return chosen


def satellite_cluster_removal(sol, evaluator, num_remove, echelon="e2"):
    """Remove every customer assigned to one nonempty satellite."""
    assignment = evaluator.assignment(sol)
    by_sat = {}
    for cust, sat_id in assignment.items():
        by_sat.setdefault(sat_id, []).append(cust)
    if not by_sat:
        return []
    sat_id = random.choice(sorted(by_sat))
    removed = by_sat[sat_id]
    for cust in removed:
        _remove_customer(sol, cust)
    return removed


def _best_task_insert(sol, evaluator, sat_id, task, start_time):
    """Find best insertion position for a delivery task at given satellite."""
    routes = sol.e2_routes.get(sat_id, [])
    best = None
    deadline = getattr(evaluator, "_alns_deadline", float("inf"))
    for r in routes:
        if time.time() >= deadline:
            break
        old_cost = evaluator.route_distance(r)
        for pos in range(len(r.nodes) + 1):
            if time.time() >= deadline:
                break
            r.nodes.insert(pos, task.id)
            feasible, _, _ = evaluator.simulate_e2_route(r, sat_id, start_time)
            new_cost = evaluator.route_distance(r) if feasible else None
            r.nodes.pop(pos)
            if feasible:
                inc = new_cost - old_cost
                if best is None or inc < best[0] - FEAS_TOL:
                    best = (inc, r, pos)
    if len(routes) < evaluator.vehicles_per_satellite:
        new_route = Route(sat_id, [task.id])
        ok = evaluator.check_e2_route_feasible(new_route, sat_id, start_time)
        if ok:
            inc = evaluator.route_distance(new_route)
            if best is None or inc < best[0] - FEAS_TOL:
                best = (inc, None, None)
    return best


def multi_sat_insertion(sol, removed, evaluator, echelon="e2", k=2):
    """Insert removed customers (delivery only) to best satellite position."""
    pending = list(removed)
    random.shuffle(pending)
    for cust in pending:
        if time.time() >= getattr(
                evaluator, "_alns_deadline", float("inf")):
            break
        evaluator.rebuild_from_solution(sol)
        start_by_sat = evaluator.e2_start_times(sol)
        d_task = evaluator.catalog[cust]["d"]

        best = None
        for sat_id in evaluator.sat_ids:
            if time.time() >= getattr(
                    evaluator, "_alns_deadline", float("inf")):
                break
            start_time = start_by_sat.get(sat_id, 0.0)
            d_best = _best_task_insert(sol, evaluator, sat_id, d_task, start_time)
            if d_best is not None:
                inc, route, pos = d_best
                if best is None or inc < best[0] - FEAS_TOL:
                    best = (inc, sat_id, route, pos)

        if best is not None:
            inc, sat_id, route, pos = best
            if route is None:
                sol.e2_routes.setdefault(sat_id, []).append(
                    Route(sat_id, [d_task.id]))
            else:
                route.nodes.insert(pos, d_task.id)
        else:
            sat_id = min(evaluator.sat_ids,
                         key=lambda s: evaluator.dist(s, f"{cust}d"))
            sol.e2_routes.setdefault(sat_id, []).append(
                Route(sat_id, [d_task.id]))

    evaluator.rebuild_from_solution(sol)
    clean_empty_routes(sol)


def _partial_routes_feasible(sol, evaluator):
    """Check all currently inserted tasks without requiring full coverage."""
    starts, _ = evaluator.e1_schedule(sol)
    if starts is None:
        return False
    for sat_id, routes in sol.e2_routes.items():
        if len(routes) > evaluator.vehicles_per_satellite:
            return False
        start_time = starts.get(sat_id, 0.0)
        for route in routes:
            feasible, _, _ = evaluator.simulate_e2_route(
                route, sat_id, start_time)
            if not feasible:
                return False
    return True


def global_cost_insertion(sol, removed, evaluator, echelon="e2", k=2):
    """Repair deliveries using complete E1+E2 marginal cost.

    The original greedy repair compares only second-echelon distance.  That can
    repeatedly select a locally cheap satellite whose induced first-echelon
    route is globally expensive.  This small-instance intensification evaluates
    the full partial solution for every feasible insertion.
    """
    pending = list(removed)
    random.shuffle(pending)
    for cust in pending:
        deadline = getattr(evaluator, "_alns_deadline", float("inf"))
        if time.time() >= deadline:
            break
        task = evaluator.catalog[cust]["d"]
        best_candidate = None
        best_cost = float("inf")
        options = []
        for sat_id in evaluator.sat_ids:
            if time.time() >= deadline:
                break
            routes = sol.e2_routes.get(sat_id, [])
            for route_idx, route in enumerate(routes):
                for pos in range(len(route.nodes) + 1):
                    prev_id = sat_id if pos == 0 else route.nodes[pos - 1]
                    next_id = (sat_id if pos == len(route.nodes)
                               else route.nodes[pos])
                    delta = (
                        evaluator.dist(prev_id, task.id)
                        + evaluator.dist(task.id, next_id)
                        - evaluator.dist(prev_id, next_id)
                    )
                    options.append((delta, sat_id, route_idx, pos))
            if len(routes) < evaluator.vehicles_per_satellite:
                options.append((2.0 * evaluator.dist(sat_id, task.id),
                                sat_id, None, 0))

        # Full E1+E2 reconstruction for every insertion position dominates
        # large-instance runtime.  Keep the globally best geometric options
        # plus the best option per satellite so reassignment diversity is not
        # lost.  Small validation instances retain exhaustive enumeration.
        if len(evaluator.catalog) > 20 and len(options) > 32:
            ranked = sorted(options, key=lambda item: item[0])
            selected = ranked[:32]
            seen_sat = {item[1] for item in selected}
            for item in ranked[32:]:
                if item[1] not in seen_sat:
                    selected.append(item)
                    seen_sat.add(item[1])
            options = selected

        for _delta, sat_id, route_idx, pos in options:
            if time.time() >= deadline:
                break
            candidate = sol.copy()
            if route_idx is None:
                candidate.e2_routes.setdefault(sat_id, []).append(
                    Route(sat_id, [task.id]))
            else:
                candidate.e2_routes[sat_id][route_idx].nodes.insert(
                    pos, task.id)
            candidate.cost = evaluator.total_cost(candidate)
            if (candidate.cost < best_cost - FEAS_TOL
                    and _partial_routes_feasible(candidate, evaluator)):
                best_candidate = candidate
                best_cost = candidate.cost

        if best_candidate is None:
            sat_id = min(evaluator.sat_ids,
                         key=lambda s: evaluator.dist(s, task.id))
            sol.e2_routes.setdefault(sat_id, []).append(
                Route(sat_id, [task.id]))
        else:
            sol.e1_routes = [route.copy()
                             for route in best_candidate.e1_routes]
            sol.e2_routes = {
                sat: [route.copy() for route in routes]
                for sat, routes in best_candidate.e2_routes.items()
            }
            sol.cost = best_candidate.cost

    evaluator.rebuild_from_solution(sol)
    clean_empty_routes(sol)


def randomized_satellite_insertion(sol, removed, evaluator,
                                   echelon="e2", k=2):
    """Diversification repair that does not greedily undo reassignment moves."""
    pending = list(removed)
    random.shuffle(pending)
    for cust in pending:
        if time.time() >= getattr(
                evaluator, "_alns_deadline", float("inf")):
            break
        evaluator.rebuild_from_solution(sol)
        starts = evaluator.e2_start_times(sol)
        task = evaluator.catalog[cust]["d"]
        satellites = list(evaluator.sat_ids)
        random.shuffle(satellites)
        inserted = False
        for sat_id in satellites:
            if time.time() >= getattr(
                    evaluator, "_alns_deadline", float("inf")):
                break
            best = _best_task_insert(
                sol, evaluator, sat_id, task, starts.get(sat_id, 0.0))
            if best is None:
                continue
            _inc, route, pos = best
            if route is None:
                sol.e2_routes.setdefault(sat_id, []).append(
                    Route(sat_id, [task.id]))
            else:
                route.nodes.insert(pos, task.id)
            inserted = True
            break
        if not inserted:
            sat_id = min(evaluator.sat_ids,
                         key=lambda sat: evaluator.dist(sat, task.id))
            sol.e2_routes.setdefault(sat_id, []).append(
                Route(sat_id, [task.id]))
    evaluator.rebuild_from_solution(sol)
    clean_empty_routes(sol)


def consolidated_satellite_insertion(sol, removed, evaluator,
                                     echelon="e2", k=2):
    """Insert the whole removed cluster into one satellite."""
    satellites = list(evaluator.sat_ids)
    random.shuffle(satellites)
    pending = list(removed)
    random.shuffle(pending)
    for target_sat in satellites:
        if time.time() >= getattr(
                evaluator, "_alns_deadline", float("inf")):
            break
        candidate = sol.copy()
        feasible = True
        for cust in pending:
            if time.time() >= getattr(
                    evaluator, "_alns_deadline", float("inf")):
                feasible = False
                break
            evaluator.rebuild_from_solution(candidate)
            starts = evaluator.e2_start_times(candidate)
            task = evaluator.catalog[cust]["d"]
            best = _best_task_insert(
                candidate, evaluator, target_sat, task,
                starts.get(target_sat, 0.0))
            if best is None:
                feasible = False
                break
            _inc, route, pos = best
            if route is None:
                candidate.e2_routes.setdefault(target_sat, []).append(
                    Route(target_sat, [task.id]))
            else:
                route.nodes.insert(pos, task.id)
        if feasible:
            _, candidate_feasible = evaluate_solution(evaluator, candidate)
            if candidate_feasible:
                sol.e1_routes = [route.copy()
                                 for route in candidate.e1_routes]
                sol.e2_routes = {
                    sat: [route.copy() for route in routes]
                    for sat, routes in candidate.e2_routes.items()
                }
                sol.cost = candidate.cost
                return
    multi_sat_insertion(sol, pending, evaluator, echelon=echelon, k=k)


def _e1_local_improve(sol, evaluator, max_rounds=3, deadline=None):
    rounds = 0
    while rounds < max_rounds:
        rounds += 1
        improved = False
        for r in sol.e1_routes:
            if deadline is not None and time.time() >= deadline:
                return
            nodes = r.nodes
            n = len(nodes)
            if n < 2:
                continue
            for i in range(n - 1):
                for j in range(i + 1, n):
                    if deadline is not None and time.time() >= deadline:
                        return
                    old_cost = evaluator.route_distance(r)
                    nodes[i], nodes[j] = nodes[j], nodes[i]
                    ok = evaluator.check_e1_route_feasible(r)
                    new_cost = evaluator.route_distance(r) if ok else None
                    if ok and new_cost < old_cost - FEAS_TOL:
                        improved = True
                    else:
                        nodes[i], nodes[j] = nodes[j], nodes[i]
        if not improved:
            break


def _adopt_solution(target, source):
    target.e1_routes = [route.copy() for route in source.e1_routes]
    target.e2_routes = {
        sat: [route.copy() for route in routes]
        for sat, routes in source.e2_routes.items()
    }
    target.cost = source.cost


def _assignment_first_improvement(sol, evaluator):
    """Try one complete-cost customer reassignment or cross-satellite swap."""
    deadline = getattr(evaluator, "_alns_deadline", float("inf"))
    base_cost = evaluator.total_cost(sol)
    locations = []
    for sat_id, routes in sol.e2_routes.items():
        for route_idx, route in enumerate(routes):
            for node_id in route.nodes:
                task = evaluator.e2_task_map.get(node_id)
                if task is not None:
                    locations.append((task.cust, node_id, sat_id, route_idx))

    for _cust, node_id, src_sat, _src_idx in locations:
        for dst_sat in evaluator.sat_ids:
            if dst_sat == src_sat:
                continue
            dst_routes = sol.e2_routes.get(dst_sat, [])
            choices = [(idx, pos) for idx, route in enumerate(dst_routes)
                       for pos in range(len(route.nodes) + 1)]
            if len(dst_routes) < evaluator.vehicles_per_satellite:
                choices.append((None, 0))
            for dst_idx, dst_pos in choices:
                if time.time() >= deadline:
                    return None
                candidate = sol.copy()
                for routes in candidate.e2_routes.values():
                    for route in routes:
                        if node_id in route.nodes:
                            route.nodes.remove(node_id)
                clean_empty_routes(candidate)
                if dst_idx is None:
                    candidate.e2_routes.setdefault(dst_sat, []).append(
                        Route(dst_sat, [node_id]))
                else:
                    candidate.e2_routes[dst_sat][dst_idx].nodes.insert(
                        dst_pos, node_id)
                _, feasible = evaluate_solution(evaluator, candidate)
                if candidate.cost < base_cost - FEAS_TOL and feasible:
                    return candidate

    for left_idx, left in enumerate(locations):
        for right in locations[left_idx + 1:]:
            if time.time() >= deadline:
                return None
            if left[2] == right[2]:
                continue
            candidate = sol.copy()
            left_node, right_node = left[1], right[1]
            left_route = right_route = None
            left_pos = right_pos = None
            for routes in candidate.e2_routes.values():
                for route in routes:
                    if left_node in route.nodes:
                        left_route, left_pos = route, route.nodes.index(left_node)
                    if right_node in route.nodes:
                        right_route, right_pos = route, route.nodes.index(right_node)
            if left_route is None or right_route is None:
                continue
            left_route.nodes[left_pos], right_route.nodes[right_pos] = (
                right_node, left_node)
            _, feasible = evaluate_solution(evaluator, candidate)
            if (candidate.cost < base_cost - FEAS_TOL and feasible):
                return candidate
    return None


def _assignment_local_improve(sol, evaluator, max_rounds=3):
    sol.cost = evaluator.total_cost(sol)
    for _ in range(max_rounds):
        candidate = _assignment_first_improvement(sol, evaluator)
        if candidate is None:
            break
        _adopt_solution(sol, candidate)


class M2Hooks(ALNSHooks):
    def __init__(self, fixed_route_probability=0.0):
        self.fixed_route_probability = float(fixed_route_probability)
        if not 0 <= self.fixed_route_probability <= 1:
            raise ValueError("fixed_route_probability must be in [0, 1]")
        self._fixed_evaluators = {}
        self.neighborhood_attempts = {"fixed_routes": 0, "assignment": 0}

    def apply_operators(self, candidate, evaluator, destroy_op, repair_op,
                        d_idx, r_idx, num_remove, echelon):
        if not (self.fixed_route_probability > 0
                and random.random() < self.fixed_route_probability
                and len(evaluator.assignment(candidate)) == len(evaluator.catalog)):
            self.neighborhood_attempts["assignment"] += 1
            return super().apply_operators(candidate, evaluator, destroy_op,
                repair_op, d_idx, r_idx, num_remove, echelon)
        from models.routing.baselines.alns import model_1 as fixed_search
        self.neighborhood_attempts["fixed_routes"] += 1
        evaluator.rebuild_from_solution(candidate)
        key = tuple(sorted(evaluator.assignment(candidate).items()))
        fixed = self._fixed_evaluators.get(key)
        if fixed is None:
            fixed = _M1Evaluator(evaluator.problem,
                list(evaluator.e1_task_map.values()), evaluator.e2_tasks_by_sat,
                evaluator.vehicles_per_satellite)
            if len(self._fixed_evaluators) >= 8:
                self._fixed_evaluators.pop(next(iter(self._fixed_evaluators)))
            self._fixed_evaluators[key] = fixed
        fixed._alns_deadline = getattr(evaluator, "_alns_deadline", float("inf"))
        destroys = (fixed_search.random_removal, fixed_search.worst_removal,
                    fixed_search.shaw_removal, fixed_search.route_segment_removal)
        repairs = (fixed_search.greedy_insertion, fixed_search.regret_insertion)
        removed = destroys[d_idx % len(destroys)](candidate, fixed, num_remove, "e2")
        repairs[r_idx % len(repairs)](candidate, removed, fixed, "e2")
        clean_empty_routes(candidate)

    def after_operators(self, candidate, evaluator, current_solution,
                        iteration, d_idx, r_idx):
        # On large instances an exhaustive E1 pass after every candidate can
        # consume more time than the destroy/repair itself. Use a bounded
        # cadence so the adaptive layer receives enough observations.
        customer_count = len(evaluator.problem.customers)
        cadence = 1 if customer_count <= 20 else 5
        if iteration % cadence == 0:
            _e1_local_improve(
                candidate, evaluator,
                max_rounds=3 if customer_count <= 20 else 1,
                deadline=getattr(evaluator, "_alns_deadline", None),
            )
        if len(evaluator.problem.customers) <= 20 and iteration % 50 == 0:
            _assignment_local_improve(candidate, evaluator)


def solve_alns(problem, vehicles_per_satellite=4, max_iterations=5000,
               max_time=120, seed=42, verbose=True, return_info=False,
               initial_solution=None, target_objective=None,
               target_tolerance=1e-6, alns_config_overrides=None,
               fixed_route_probability=0.0, route_seed_fraction=None):
    """Run M2 ALNS: pure-delivery + free customer-satellite assignment."""
    from models.routing.common.operating_policy import policy_for
    policy_for(problem).require_model(2)
    random.seed(seed)
    start_time = time.time()
    if route_seed_fraction is None:
        route_seed_fraction = .2 if len(problem.customers)>20 and max_time>=5 else 0.

    evaluator = Evaluator(problem, vehicles_per_satellite)
    sol = (initial_solution.copy() if initial_solution is not None
           else build_initial_solution(problem, evaluator))

    if not evaluator.is_feasible(sol):
        if sol is None or not any(
                r.nodes for routes in sol.e2_routes.values() for r in routes):
            if verbose:
                print("\n[WARN] Model 2 无法构造含顾客的初始解")
            info = {"objective": None, "runtime": time.time() - start_time,
                    "iterations": 0, "feasible": False, "status": "NO_SOLUTION"}
            return ((None, None, None, info) if return_info
                    else (None, None, None))
        if verbose:
            print("\n[WARN] Model 2 初始解不满足全局约束，"
                  "从该种子继续搜索（分配自由度可恢复可行性）")

    sol.cost = evaluator.total_cost(sol)
    initial_objective = float(sol.cost)
    seed_iterations = 0
    route_seed_seconds = 0.0
    route_seed_best_found_time = None
    if not 0 <= route_seed_fraction < 1:
        raise ValueError("route_seed_fraction must be in [0, 1)")
    if (route_seed_fraction > 0 and evaluator.is_feasible(sol)
            and (alns_config_overrides or {}).get('cooling_schedule', 'time') == 'time'):
        # Improve the chosen complete assignment's routes before spending the
        # remaining budget on expensive free-assignment repair. Both stages
        # share the caller's deadline and iteration cap.
        from models.routing.baselines.alns import model_1 as fixed_search
        evaluator.rebuild_from_solution(sol)
        fixed = _M1Evaluator(problem, list(evaluator.e1_task_map.values()),
                             evaluator.e2_tasks_by_sat, vehicles_per_satellite)
        seed_started = time.time()
        seed_result = run_alns_search(initial_solution=sol, evaluator=fixed,
            destroy_ops=[fixed_search.random_removal, fixed_search.worst_removal,
                         fixed_search.shaw_removal, fixed_search.route_segment_removal],
            repair_ops=[fixed_search.greedy_insertion, fixed_search.regret_insertion],
            config=ALNSConfig(max_iterations=int(max_iterations*route_seed_fraction),
                max_time=max_time*route_seed_fraction, target_cost=target_objective,
                target_tolerance=target_tolerance, **(alns_config_overrides or {})),
            start_time=start_time, verbose=False)
        seed_iterations = seed_result.iterations
        route_seed_seconds = time.time()-seed_started
        if seed_result.best_solution is not None:
            candidate = seed_result.best_solution
            value, valid = evaluator.evaluate(candidate)
            if valid and value < sol.cost-FEAS_TOL:
                candidate.cost = value
                sol = candidate
                route_seed_best_found_time = seed_result.best_found_time
    route_seed_objective = float(sol.cost)
    best_sol = sol.copy()
    best_cost = sol.cost

    if verbose:
        print(f"\n{'=' * 60}")
        print("ALNS 求解器 (Model 2 自由分配 纯配送)")
        print(f"{'=' * 60}")
        print(f"初始解目标值: {sol.cost:.2f}")
        print(f"初始解可行性: {evaluator.is_feasible(sol)}")
        print(f"E1 路径数: {len(sol.e1_routes)}")
        e2_count = sum(len(v) for v in sol.e2_routes.values())
        print(f"E2 路径数: {e2_count}")
        assign = evaluator.assignment(sol)
        print(f"初始分配: " + ", ".join(
            f"{s}:{sum(1 for v in assign.values() if v == s)}"
            for s in evaluator.sat_ids))

    hooks = M2Hooks(fixed_route_probability)
    result = run_alns_search(
        initial_solution=sol,
        evaluator=evaluator,
        destroy_ops=[
            random_customer_removal,
            worst_customer_removal,
            shaw_customer_removal,
            satellite_cluster_removal,
            assignment_boundary_removal,
            two_satellite_removal,
        ],
        repair_ops=[multi_sat_insertion, global_cost_insertion,
                    randomized_satellite_insertion,
                    consolidated_satellite_insertion],
        config=ALNSConfig(
            max_iterations=max(0, max_iterations-seed_iterations),
            max_time=max_time,
            e2_probability=1.0,
            target_cost=target_objective,
            target_tolerance=target_tolerance,
            **(alns_config_overrides or {}),
        ),
        hooks=hooks,
        verbose=verbose,
        start_time=start_time,
    )
    best_sol = result.best_solution
    if best_sol is None:
        info = {"objective": None, "runtime": result.elapsed,
                "iterations": result.iterations, "feasible": False,
                "status": "NO_SOLUTION"}
        return ((None, None, None, info) if return_info
                else (None, None, None))
    best_cost = result.best_cost
    elapsed = result.elapsed

    if verbose:
        print(f"\n{'=' * 60}")
        print("ALNS 求解完成")
        print(f"最优目标值: {best_cost:.2f}")
        print(f"总耗时: {elapsed:.2f} 秒")
        assign = evaluator.assignment(best_sol)
        print("最终分配: " + ", ".join(
            f"{s}:{sum(1 for v in assign.values() if v == s)}"
            for s in evaluator.sat_ids))
        print(f"{'=' * 60}")

    e1_routes_out = [r.full_path() for r in best_sol.e1_routes]
    e2_routes_out = {
        sat_id: [r.full_path() for r in routes]
        for sat_id, routes in best_sol.e2_routes.items()
    }
    assignment = evaluator.assignment(best_sol)
    feasible = bool(evaluator.is_feasible(best_sol))
    info = {
        "objective": float(best_cost), "runtime": float(elapsed),
        "iterations": int(result.iterations+seed_iterations), "feasible": feasible,
        "status": "FEASIBLE" if feasible else "NO_SOLUTION",
        "external_initial_solution": initial_solution is not None,
        "initial_objective": initial_objective,
        "route_seed_objective": route_seed_objective,
        "route_seed_iterations": seed_iterations,
        "route_seed_seconds": route_seed_seconds,
        "route_seed_best_found_time": route_seed_best_found_time,
        "best_found_time": (route_seed_best_found_time
            if route_seed_best_found_time is not None and abs(best_cost-route_seed_objective)<FEAS_TOL
            else result.best_found_time),
        "target_hit_time": result.target_hit_time,
        "termination_reason": result.termination_reason,
        "restarts": result.restarts,
        "local_search_improvements": result.local_search_improvements,
        "alns_diagnostics": result.diagnostics,
        "neighborhood_attempts": dict(hooks.neighborhood_attempts),
    }
    return ((e1_routes_out, e2_routes_out, assignment, info) if return_info
            else (e1_routes_out, e2_routes_out, assignment))


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------

def _apply_warm_start(m, vars_dict, ctx, e1_routes, e2_routes_by_sat,
                      assignment, verbose=True):
    w = vars_dict["w"]
    y_route = vars_dict["route"]
    x = vars_dict["x"]
    served_s = vars_dict["served_s"]
    cols = ctx["cols"]
    K = ctx["K"]
    F = ctx["F"]

    for c, s in assignment.items():
        w[c, s].Start = 1.0
        for s2 in F:
            if s2 != s:
                w[c, s2].Start = 0.0

    for k_idx, route in enumerate(e1_routes):
        if k_idx >= len(K):
            break
        k = K[k_idx]
        for i in range(len(route) - 1):
            ni, nj = route[i], route[i + 1]
            if (ni, nj, k) in x:
                x[ni, nj, k].Start = 1.0

    served_sats = set(assignment.values())
    for s in F:
        served_s[s].Start = 1.0 if s in served_sats else 0.0

    path_to_rid = {col["path"]: rid for rid, col in enumerate(cols)}
    matched = 0
    for sat_id, routes in e2_routes_by_sat.items():
        for route_path in routes:
            path = tuple(route_path)
            if path in path_to_rid:
                y_route[path_to_rid[path]].Start = 1.0
                matched += 1

    if verbose:
        print(f"  Warm start: {len(assignment)} assignments, "
              f"{matched} E2 routes matched")


def solve_m2_exact(problem, vehicles_per_satellite, *,
                    incumbent_cost, e1_routes=None, e2_routes_by_sat=None,
                    assignment=None, time_limit=600, gurobi_params=None,
                    verbose=True, return_info=False):
    """Solve the supplied M2 route pool to MIP optimality.

    The legacy function name remains for compatibility.  Its result metadata
    deliberately marks the proof scope as restricted, because the route pool
    is not a complete route universe.
    """
    started = time.time()

    satellites = list(problem.satellites)
    singleton_cols, _ = _generate_singleton_columns(
        problem, problem.customers, satellites)
    print(f"Generated {len(singleton_cols)} singleton columns")

    alns_cols = []
    if e2_routes_by_sat:
        alns_cols = _extract_route_columns_from_alns(
            problem, e2_routes_by_sat)
        print(f"Extracted {len(alns_cols)} columns from ALNS solution")

    all_cols = singleton_cols + alns_cols
    print(f"Total columns: {len(all_cols)}")

    m, vars_dict, ctx = build_m2_route_master(
        problem, all_cols, vehicles_per_satellite,
        relax=False, incumbent_cost=incumbent_cost * 1.05)

    if e1_routes and e2_routes_by_sat and assignment:
        _apply_warm_start(m, vars_dict, ctx, e1_routes, e2_routes_by_sat,
                          assignment, verbose=verbose)

    if time_limit:
        m.setParam("TimeLimit", time_limit)
    m.setParam("MIPGap", 0.0)
    m.setParam("Presolve", 2)
    m.setParam("Cuts", 2)
    if gurobi_params:
        for name, value in gurobi_params.items():
            m.setParam(name, value)

    if verbose:
        print(f"\nM2 Route-Pool Master: {len(vars_dict['w'])} w-vars, "
              f"{len(vars_dict['route'])} route-vars, "
              f"{len(vars_dict['x'])} x-vars")
        print("Starting optimization...")

    m.optimize()

    elapsed = time.time() - started
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
            print(f"M2 route-pool: no feasible solution (status={info['status']})")
        if return_info:
            return {"e1_routes": None, "e2_routes": None,
                    "assignment": None, "info": info}
        return None, None, None, info

    depot_id = ctx["depot_id"]
    F = ctx["F"]
    K = ctx["K"]
    cols = ctx["cols"]

    sol_x = {key: var.X for key, var in vars_dict["x"].items()}
    e1_routes = [route for _, route in extract_routes_e1(
        sol_x, depot_id, ctx["e1_del_nodes"], K)]

    e2_routes_by_sat = {s: [] for s in F}
    for rid, col in enumerate(cols):
        if vars_dict["route"][rid].X > 0.5:
            e2_routes_by_sat[col["sat_id"]].append(list(col["path"]))

    w = vars_dict["w"]
    assignment = {}
    for c in ctx["C"]:
        for s in F:
            if w[c, s].X > 0.5:
                assignment[c] = s
                break

    info["assignment"] = assignment

    if verbose:
        print(f"\nM2 Route-Pool Result:")
        print(f"  Status: {info['status']}")
        print(f"  Objective: {info['objective']:.4f}" if info["objective"] else "  Objective: N/A")
        print("  Route-pool optimal: "
              f"{info['status'] == 'OPTIMAL'} (not a global proof)")
        print(f"  MIP Gap: {info['mip_gap']:.4%}" if info.get("mip_gap") else "")
        print(f"  Best bound: {info['best_bound']:.4f}" if info.get("best_bound") else "")
        print(f"  Runtime: {info['runtime']:.1f}s" if info.get("runtime") else "")
        from collections import Counter
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
# Column Generation for M2 (true exact solving)
# ===========================================================================

def _solve_m2_pricing_mip(problem, sat_id, customers, cover_duals, vehicle_dual,
                          pricing_time_limit=30.0, pool_solutions=10,
                          output_flag=False):
    """Solve the pricing subproblem for satellite s (M2 pure-delivery).

    Find a feasible E2 route starting/ending at sat_id that visits a subset
    of customers (delivery only), satisfying time windows and capacity, with
    minimum reduced cost:

        rc = route_cost - Σ_{c in route} π[c,s] - σ_s

    where π[c,s] = cover_duals[c] (dual of E2_Cov_{c}d_{s})
          σ_s     = vehicle_dual  (dual of E2_VehicleLimit_{s})

    Returns list of route column dicts with negative reduced cost.
    """
    started = time.time()
    cw = problem.fleet.container_weight
    cv = problem.fleet.container_volume
    inf = float("inf")
    eps = 1e-6

    task_ids = [f"{c.id}d" for c in customers]
    task_map = {}
    for c in customers:
        task_map[f"{c.id}d"] = type("T", (), {
            "id": f"{c.id}d", "cust": c.id, "kind": "d",
            "w": c.weight_del + cw, "v": cv, "tw": c.tw_del,
        })()

    nodes = [sat_id] + task_ids
    max_latest = max(
        [t.tw[1] for t in task_map.values() if t.tw[1] < inf] + [0.0]
    )
    m_time = max(100000.0, max_latest + 10000.0)

    m = gp.Model(f"M2_Pricing_{sat_id}")
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

    # Depot departure time = 0 (satellite is start depot for E2)
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

    # Initial load at satellite = sum of all delivery demands on route
    m.addConstr(
        Q[sat_id] == gp.quicksum(task_map[t].w * y[t] for t in task_ids),
        name="initial_weight",
    )
    m.addConstr(
        Vol[sat_id] == gp.quicksum(task_map[t].v * y[t] for t in task_ids),
        name="initial_volume",
    )

    # Time propagation + capacity tracking along arcs
    for i_node in nodes:
        s_i = 0.0 if i_node == sat_id else getattr(
            problem.nodes.get(task_map[i_node].cust), "service_time", 0.0)
        for j in task_ids:
            if i_node == j:
                continue
            phys_i = sat_id if i_node == sat_id else task_map[i_node].cust
            phys_j = task_map[j].cust
            travel = problem.travel_time(phys_i, phys_j)

            # Prune infeasible arcs
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
            # Capacity: delivery decreases load
            q_j = task_map[j].w
            v_j = task_map[j].v
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

    # Objective: reduced cost
    route_cost = gp.quicksum(
        problem.distance(
            sat_id if i_node == sat_id else task_map[i_node].cust,
            sat_id if j == sat_id else task_map[j].cust,
        ) * z[i_node, j]
        for (i_node, j) in z.keys()
    )
    dual_reward = gp.quicksum(
        cover_duals.get(t, 0.0) * y[t] for t in task_ids
    )
    reduced_cost_expr = route_cost - dual_reward - vehicle_dual
    m.setObjective(reduced_cost_expr, GRB.MINIMIZE)

    m.optimize()

    info = {
        "status": _status_name(m.status),
        "status_code": int(m.status),
        "reduced_cost": float(m.ObjVal) if m.SolCount else None,
        "reduced_cost_bound": float(m.ObjBound) if m.SolCount else float("inf"),
        "runtime": float(m.Runtime),
        "pool_count": int(m.SolCount),
    }

    if not m.SolCount:
        return [], info

    # Extract route(s) from solution(s)
    cols = []
    seen_paths = set()
    sol_count = min(int(m.SolCount), int(pool_solutions or 1))
    for sol_idx in range(sol_count):
        if sol_idx:
            m.setParam("SolutionNumber", sol_idx)
            sol_z = {key: var.Xn for key, var in z.items()}
            sol_y = {key: var.Xn for key, var in y.items()}
        else:
            sol_z = {key: var.X for key, var in z.items()}
            sol_y = {key: var.X for key, var in y.items()}

        # Extract path
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

        # Compute actual route cost and reduced cost
        covered = tuple(n for n in path[1:-1] if n in task_map)
        if not covered:
            continue
        actual_cost = _route_cost(problem, sat_id, path, task_map)
        rc = actual_cost - sum(cover_duals.get(t, 0.0) for t in covered) - vehicle_dual

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


def _extract_m2_duals(m, vars_dict, ctx):
    """Extract LP duals from solved M2 master for column generation.

    Returns:
        cover_duals: dict {(c, s): π[c,s]} — dual of E2_Cov_{c}d_{s}
        vehicle_duals: dict {s: σ_s} — dual of E2_VehicleLimit_{s}
    """
    F = ctx["F"]
    C = ctx["C"]

    cover_duals = {}
    for c in C:
        task_id = f"{c}d"
        for s in F:
            cname = f"E2_Cov_{task_id}_{s}"
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


def solve_m2_column_generation(*args, **kwargs):
    """Deprecated diagnostic routine: DO NOT use as an exact method.

    The full M2 route master couples route variables to E1 through timing and
    linking constraints.  Coverage/vehicle-dual pricing therefore misses part
    of a column's true reduced cost and cannot certify LP completeness.
    """
    raise NotImplementedError(
        "M2 column generation is not a valid exact method for the full master; "
        "use solve_m2_exact with a certified complete route universe or the "
        "small-instance arc-flow model"
    )
