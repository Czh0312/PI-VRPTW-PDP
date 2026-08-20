# -*- coding: utf-8 -*-
"""Restricted route-pool exact master for Model 2.

This is a bridge toward branch-price: E2 arc-flow variables are replaced by
pre-generated route columns, while the first-echelon arc-flow and timing
linking remain explicit. The model is exact for the supplied route pool, not
for the full route universe.
"""

import gurobipy as gp
from gurobipy import GRB


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


def _signed_q(task):
    return task.w if task.kind == "d" else -task.w


def _signed_v(task):
    return task.v if task.kind == "d" else -task.v


def _phys(task_map, node_id):
    task = task_map.get(node_id)
    return task.cust if task is not None else node_id


def _route_cost(problem, task_map, path):
    return sum(
        problem.distance(_phys(task_map, path[i]), _phys(task_map, path[i + 1]))
        for i in range(len(path) - 1)
    )


def _service_time(problem, task_map, node_id):
    node = problem.nodes.get(_phys(task_map, node_id))
    return getattr(node, "service_time", 0.0) if node is not None else 0.0


def build_route_pool_master(problem, e1_tasks, e2_tasks_by_sat, route_columns,
                            vehicles_per_satellite, TFp=9999.0,
                            satellite_cost_lbs=None,
                            e1_cost_cap=None, e2_cost_cap=None, relax=False,
                            return_duals=False):
    depot_id = problem.depot.id
    F = [s.id for s in problem.satellites]
    K = list(range(problem.e1_vehicle_count))
    e1_nodes = [t.id for t in e1_tasks]
    n1e = [depot_id] + e1_nodes
    e1_task_map = {t.id: t for t in e1_tasks}
    e2_task_map = {
        t.id: t
        for tasks in e2_tasks_by_sat.values()
        for t in tasks
    }
    e2_task_to_sat = {
        t.id: sat_id
        for sat_id, tasks in e2_tasks_by_sat.items()
        for t in tasks
    }
    all_task_ids = sorted(e2_task_map)

    def loc(node_id):
        if node_id == depot_id or node_id in F:
            return node_id
        task = e1_task_map.get(node_id) or e2_task_map.get(node_id)
        return task.cust if task is not None else node_id

    dist = {}
    travel = {}
    all_ids = set([depot_id] + F + e1_nodes + all_task_ids)
    for i in all_ids:
        for j in all_ids:
            dist[i, j] = 0.0 if i == j else problem.distance(loc(i), loc(j))
            travel[i, j] = 0.0 if i == j else problem.travel_time(loc(i), loc(j))

    e1_service = {depot_id: 0.0}
    for node in e1_nodes:
        e1_service[node] = getattr(problem.nodes.get(loc(node)), "service_time", 0.0)

    m = gp.Model("M2_RoutePool_Master")
    x = m.addVars(n1e, n1e, K, lb=0.0, ub=1.0,
                  vtype=GRB.CONTINUOUS if relax else GRB.BINARY, name="x")
    A = m.addVars(n1e, K, lb=0.0, name="A_e1")
    Q = m.addVars(n1e, K, lb=0.0, ub=problem.e1_weight_cap, name="Q_k")
    V = m.addVars(n1e, K, lb=0.0, ub=problem.e1_volume_cap, name="Vol_k")

    cols = _normalize_columns(problem, e2_task_map, route_columns)
    route_ids = list(range(len(cols)))
    y = m.addVars(route_ids, lb=0.0, ub=1.0,
                  vtype=GRB.CONTINUOUS if relax else GRB.BINARY, name="route")
    S = m.addVars(route_ids, lb=0.0, name="route_start")
    R = m.addVars(route_ids, lb=0.0, name="route_return")
    A2 = m.addVars(all_task_ids, lb=0.0, name="e2_task_arrival")

    obj_e1 = gp.quicksum(
        dist[i, j] * x[i, j, k]
        for k in K for i in n1e for j in n1e if i != j
    )
    obj_e2 = gp.quicksum(cols[rid]["cost"] * y[rid] for rid in route_ids)
    m.setObjective(obj_e1 + obj_e2, GRB.MINIMIZE)
    if e1_cost_cap is not None:
        m.addConstr(obj_e1 <= float(e1_cost_cap), "E1_cost_cap")
    if e2_cost_cap is not None:
        m.addConstr(obj_e2 <= float(e2_cost_cap), "E2_cost_cap")

    _add_e1_constraints(
        m, problem, e1_tasks, x, A, Q, V, n1e, e1_nodes, K, depot_id,
        e1_task_map, dist, travel, e1_service,
    )
    e2_constrs = _add_route_pool_constraints(
        m, problem, e2_tasks_by_sat, cols, y, S, R, A2,
        vehicles_per_satellite, e2_task_map, e2_task_to_sat,
        satellite_cost_lbs=satellite_cost_lbs,
    )
    linking_constrs = _add_linking_constraints(
        m, problem, e1_tasks, e2_tasks_by_sat, cols, x, A, y, S, R,
        n1e, K, depot_id, e1_task_map, e2_task_map, e1_service,
    )

    vars_dict = {
        "x": x,
        "A_e1": A,
        "Q_k": Q,
        "Vol_k": V,
        "route": y,
        "route_start": S,
        "route_return": R,
        "e2_task_arrival": A2,
    }
    ctx = {
        "depot_id": depot_id,
        "F": F,
        "K": K,
        "N1E": n1e,
        "N_E1_Tasks": e1_nodes,
        "e1_task_map": e1_task_map,
        "e2_task_map": e2_task_map,
        "route_columns": cols,
    }
    if return_duals:
        ctx["constrs"] = {
            "cover": e2_constrs["cover"],
            "route_limit": e2_constrs["route_limit"],
            "tw_lo": e2_constrs["tw_lo"],
            "tw_hi": e2_constrs["tw_hi"],
            "sat_cost_lb": e2_constrs["sat_cost_lb"],
            "link_start": linking_constrs["link_start"],
            "link_pickup": linking_constrs["link_pickup"],
        }
    return m, vars_dict, ctx


def solve_route_pool_exact(problem, e1_tasks, e2_tasks_by_sat, route_columns,
                           vehicles_per_satellite, time_limit=300,
                           mip_gap=0.0, gurobi_params=None, log_file=None,
                           verbose=False, initial_routes=None,
                           satellite_cost_lbs=None):
    m, vars_dict, ctx = build_route_pool_master(
        problem, e1_tasks, e2_tasks_by_sat, route_columns,
        vehicles_per_satellite,
        satellite_cost_lbs=satellite_cost_lbs,
    )
    start_info = None
    if initial_routes:
        start_info = _apply_mip_start(vars_dict, ctx, initial_routes)
    if log_file:
        m.setParam("LogFile", str(log_file))
        m.setParam("OutputFlag", 1)
    elif not verbose:
        m.setParam("OutputFlag", 0)
    m.setParam("TimeLimit", time_limit)
    m.setParam("MIPGap", mip_gap)
    for name, value in (gurobi_params or {}).items():
        m.setParam(name, value)
    m.optimize()

    info = {
        "status_code": int(m.status),
        "status": _status_name(m.status),
        "runtime": float(getattr(m, "Runtime", 0.0)),
        "sol_count": int(getattr(m, "SolCount", 0)),
        "objective": None,
        "best_bound": None,
        "mip_gap": None,
        "route_columns": len(ctx["route_columns"]),
        "mip_start": start_info,
        "satellite_cost_lbs": satellite_cost_lbs or {},
    }
    if m.SolCount:
        info["objective"] = float(m.ObjVal)
        info["mip_gap"] = float(m.MIPGap)
        info["routes"] = _extract_solution_routes(vars_dict, ctx)
    try:
        info["best_bound"] = float(m.ObjBound)
    except Exception:
        pass
    return info


def _apply_mip_start(vars_dict, ctx, initial_routes):
    x = vars_dict["x"]
    y = vars_dict["route"]
    depot_id = ctx["depot_id"]
    n1e = set(ctx["N1E"])
    K = ctx["K"]
    cols = ctx["route_columns"]

    for var in x.values():
        var.Start = 0.0
    for var in y.values():
        var.Start = 0.0

    e1_started = 0
    for k, route in zip(K, initial_routes.get("e1", [])):
        if len(route) < 2:
            continue
        for i, j in zip(route[:-1], route[1:]):
            if i in n1e and j in n1e and i != j:
                x[i, j, k].Start = 1.0
        if route[0] == depot_id and route[-1] == depot_id:
            e1_started += 1

    path_to_rids = {}
    for rid, col in enumerate(cols):
        path_to_rids.setdefault(tuple(col["path"]), []).append(rid)

    e2_started = 0
    missing_e2_paths = 0
    for routes in initial_routes.get("e2", {}).values():
        for path in routes:
            rids = path_to_rids.get(tuple(path))
            if not rids:
                missing_e2_paths += 1
                continue
            y[rids[0]].Start = 1.0
            e2_started += 1

    return {
        "e1_routes_started": e1_started,
        "e2_routes_started": e2_started,
        "missing_e2_paths": missing_e2_paths,
    }


def _extract_solution_routes(vars_dict, ctx):
    x = vars_dict["x"]
    y = vars_dict["route"]
    depot_id = ctx["depot_id"]
    n1e = ctx["N1E"]
    e1_nodes = ctx["N_E1_Tasks"]
    K = ctx["K"]
    cols = ctx["route_columns"]

    e1_routes = []
    for k in K:
        first = None
        for j in e1_nodes:
            if x[depot_id, j, k].X > 0.5:
                first = j
                break
        if first is None:
            continue
        route = [depot_id, first]
        current = first
        seen = {depot_id, first}
        while current != depot_id:
            nxt = None
            for j in n1e:
                if j != current and x[current, j, k].X > 0.5:
                    nxt = j
                    break
            if nxt is None:
                break
            route.append(nxt)
            if nxt == depot_id:
                break
            if nxt in seen:
                break
            seen.add(nxt)
            current = nxt
        if len(route) > 2 and route[-1] == depot_id:
            e1_routes.append(route)

    e2_routes = {sat_id: [] for sat_id in ctx["F"]}
    for rid, col in enumerate(cols):
        if y[rid].X > 0.5:
            e2_routes.setdefault(col["sat_id"], []).append(list(col["path"]))
    return {"e1": e1_routes, "e2": e2_routes}


def extract_joint_duals(m, vars_dict, ctx, e2_tasks_by_sat):
    """Read LP duals after m.optimize(); aggregate linking duals into per-sat sigma/rho.

    Returns dict with: pi_cover, nu_vehicle, sigma_start, rho_pickup,
    alpha_lo, alpha_hi, pi_satlb, link_dual_max_abs, n_selected.
    Returns None if m.SolCount == 0 or status is not OPTIMAL.
    """
    if not m.SolCount or m.status != GRB.OPTIMAL:
        return None

    cc = ctx.get("constrs", {})
    if not cc:
        return None

    # E2_Cover duals — equality rows, free sign
    pi_cover = {
        tid: cc["cover"][tid].Pi
        for tid in ctx["e2_task_map"]
        if tid in cc.get("cover", {})
    }

    # E2_RouteLimit duals — <= rows, dual <= 0
    nu_vehicle = {}
    for sat_id in ctx["F"]:
        c = cc.get("route_limit", {}).get(sat_id)
        nu_vehicle[sat_id] = c.Pi if c is not None else 0.0

    # E2_TaskTW duals
    alpha_lo = {}
    alpha_hi = {}
    for tid in ctx["e2_task_map"]:
        c_lo = cc.get("tw_lo", {}).get(tid)
        alpha_lo[tid] = c_lo.Pi if c_lo is not None else 0.0
        c_hi = cc.get("tw_hi", {}).get(tid)
        alpha_hi[tid] = c_hi.Pi if c_hi is not None else 0.0

    # E2_SatCostLB duals — >= rows, dual >= 0
    pi_satlb = {}
    for sat_id, c in cc.get("sat_cost_lb", {}).items():
        pi_satlb[sat_id] = c.Pi

    # --- Convex-combination weighted aggregation of linking duals ---
    x = vars_dict["x"]
    y = vars_dict["route"]
    cols = ctx["route_columns"]
    K = ctx["K"]
    n1e = ctx["N1E"]
    n1e_set = set(n1e)

    # n_selected[s] = sum of y[rid].X over routes of satellite s
    n_selected = {sat_id: 0.0 for sat_id in ctx["F"]}
    for rid, col in enumerate(cols):
        n_selected[col["sat_id"]] += y[rid].X

    # Pre-compute visits_del_X[s][k] and visits_pick_X[s][k]
    visits_del_x = {}
    visits_pick_x = {}
    for sat_id in ctx["F"]:
        del_id = f"{sat_id}d"
        pick_id = f"{sat_id}p"
        visits_del_x[sat_id] = {}
        visits_pick_x[sat_id] = {}
        for k in K:
            v_del = 0.0
            if del_id in n1e_set:
                v_del = float(sum(
                    x[i, del_id, k].X for i in n1e if i != del_id
                ))
            visits_del_x[sat_id][k] = v_del
            v_pick = 0.0
            if pick_id in n1e_set:
                v_pick = float(sum(
                    x[i, pick_id, k].X for i in n1e if i != pick_id
                ))
            visits_pick_x[sat_id][k] = v_pick

    sigma_start = {}
    rho_pickup = {}
    link_dual_max_abs = 0.0
    for sat_id in ctx["F"]:
        denom = max(n_selected.get(sat_id, 0.0), 1.0)
        s_sum = 0.0
        r_sum = 0.0
        for (s, rid, k), c in cc.get("link_start", {}).items():
            if s != sat_id:
                continue
            pi_val = c.Pi
            if abs(pi_val) > link_dual_max_abs:
                link_dual_max_abs = abs(pi_val)
            s_sum += pi_val * visits_del_x.get(sat_id, {}).get(k, 0.0)
        for (s, rid, k), c in cc.get("link_pickup", {}).items():
            if s != sat_id:
                continue
            pi_val = c.Pi
            if abs(pi_val) > link_dual_max_abs:
                link_dual_max_abs = abs(pi_val)
            r_sum += pi_val * visits_pick_x.get(sat_id, {}).get(k, 0.0)
        sigma_start[sat_id] = s_sum / denom
        rho_pickup[sat_id] = r_sum / denom

    return {
        "pi_cover": pi_cover,
        "nu_vehicle": nu_vehicle,
        "sigma_start": sigma_start,
        "rho_pickup": rho_pickup,
        "alpha_lo": alpha_lo,
        "alpha_hi": alpha_hi,
        "pi_satlb": pi_satlb,
        "link_dual_max_abs": link_dual_max_abs,
        "n_selected": n_selected,
    }


def _normalize_columns(problem, e2_task_map, route_columns):
    normalized = []
    seen = set()
    for col in route_columns:
        path = tuple(col["path"])
        if len(path) < 3 or path in seen:
            continue
        seen.add(path)
        sat_id = col.get("sat_id") or path[0]
        tasks = tuple(n for n in path if n in e2_task_map)
        if not tasks:
            continue
        normalized.append({
            "sat_id": sat_id,
            "path": path,
            "tasks": tasks,
            "cost": float(col.get("cost", _route_cost(problem, e2_task_map, path))),
            "source": col.get("source", "pool"),
        })
    return normalized


def _add_e1_constraints(m, problem, e1_tasks, x, A, Q, V, n1e, e1_nodes, K,
                        depot_id, task_map, dist, travel, service):
    M = 100000.0
    FD = [t.id for t in e1_tasks if t.kind == "d"]

    for i in n1e:
        for k in K:
            m.addConstr(x[i, i, k] == 0, f"E1_NoSelf_{i}_{k}")
    for k in K:
        m.addConstr(A[depot_id, k] == 0.0, f"E1_DepotTime_{k}")
        m.addConstr(
            gp.quicksum(x[depot_id, j, k] for j in e1_nodes) <= 1,
            f"E1_Start_{k}",
        )
        m.addConstr(
            gp.quicksum(x[i, depot_id, k] for i in e1_nodes) <= 1,
            f"E1_End_{k}",
        )

    for j in e1_nodes:
        m.addConstr(
            gp.quicksum(x[i, j, k] for k in K for i in n1e if i != j) == 1,
            f"E1_Visit_{j}",
        )

    for k in K:
        depot_out = gp.quicksum(x[depot_id, j, k] for j in e1_nodes)
        for h in e1_nodes:
            m.addConstr(
                gp.quicksum(x[i, h, k] for i in n1e if i != h)
                == gp.quicksum(x[h, j, k] for j in n1e if j != h),
                f"E1_Flow_{h}_{k}",
            )
            m.addConstr(
                gp.quicksum(x[i, h, k] for i in n1e if i != h) <= depot_out,
                f"E1_Subtour_{h}_{k}",
            )

    for idx in range(len(K) - 1):
        m.addConstr(
            gp.quicksum(x[depot_id, j, K[idx]] for j in e1_nodes)
            >= gp.quicksum(x[depot_id, j, K[idx + 1]] for j in e1_nodes),
            f"E1_Sym_{idx}",
        )

    m.addConstr(
        gp.quicksum(x[depot_id, j, k] for k in K for j in e1_nodes)
        == gp.quicksum(x[i, depot_id, k] for k in K for i in e1_nodes),
        "E1_DepotBalance",
    )

    for sat_id in {t.cust for t in e1_tasks}:
        pid, did = f"{sat_id}p", f"{sat_id}d"
        if pid in task_map and did in task_map:
            for k in K:
                m.addConstr(x[pid, did, k] == 0, f"E1_ForbidPD_{sat_id}_{k}")

    for i in n1e:
        s_i = service.get(i, 0.0)
        for j in e1_nodes:
            if i == j:
                continue
            for k in K:
                m.addConstr(
                    A[j, k] >= A[i, k] + s_i + travel[i, j]
                    - M * (1 - x[i, j, k]),
                    f"E1_Time_{i}_{j}_{k}",
                )

    for j in e1_nodes:
        e_j, l_j = task_map[j].tw
        for k in K:
            visits = gp.quicksum(x[i, j, k] for i in n1e if i != j)
            m.addConstr(A[j, k] >= e_j - M * (1 - visits),
                        f"E1_TW_lo_{j}_{k}")
            if l_j < float("inf"):
                m.addConstr(A[j, k] <= l_j + M * (1 - visits),
                            f"E1_TW_hi_{j}_{k}")

    total_del_w = sum(t.w for t in e1_tasks if t.kind == "d")
    total_del_v = sum(t.v for t in e1_tasks if t.kind == "d")
    m.addConstr(gp.quicksum(Q[depot_id, k] for k in K) == total_del_w,
                "E1_TotalWeight")
    m.addConstr(gp.quicksum(V[depot_id, k] for k in K) == total_del_v,
                "E1_TotalVolume")

    for k in K:
        used = gp.quicksum(x[depot_id, j, k] for j in e1_nodes)
        m.addConstr(Q[depot_id, k] <= problem.e1_weight_cap * used,
                    f"E1_InitWUsed_{k}")
        m.addConstr(V[depot_id, k] <= problem.e1_volume_cap * used,
                    f"E1_InitVUsed_{k}")
        m.addConstr(
            Q[depot_id, k] == gp.quicksum(
                t.w * gp.quicksum(x[i, t.id, k] for i in n1e if i != t.id)
                for t in e1_tasks if t.kind == "d"
            ),
            f"E1_InitWExact_{k}",
        )
        m.addConstr(
            V[depot_id, k] == gp.quicksum(
                t.v * gp.quicksum(x[i, t.id, k] for i in n1e if i != t.id)
                for t in e1_tasks if t.kind == "d"
            ),
            f"E1_InitVExact_{k}",
        )

    for i in n1e:
        for j in e1_nodes:
            if i == j:
                continue
            q_j = _signed_q(task_map[j])
            v_j = _signed_v(task_map[j])
            m_w = problem.e1_weight_cap + abs(q_j)
            m_v = problem.e1_volume_cap + abs(v_j)
            for k in K:
                m.addConstr(Q[j, k] <= Q[i, k] - q_j + m_w * (1 - x[i, j, k]),
                            f"E1_W_up_{i}_{j}_{k}")
                m.addConstr(Q[j, k] >= Q[i, k] - q_j - m_w * (1 - x[i, j, k]),
                            f"E1_W_lo_{i}_{j}_{k}")
                m.addConstr(V[j, k] <= V[i, k] - v_j + m_v * (1 - x[i, j, k]),
                            f"E1_V_up_{i}_{j}_{k}")
                m.addConstr(V[j, k] >= V[i, k] - v_j - m_v * (1 - x[i, j, k]),
                            f"E1_V_lo_{i}_{j}_{k}")


def _add_route_pool_constraints(m, problem, e2_tasks_by_sat, cols, y, S, R, A2,
                                vehicles_per_satellite, task_map,
                                task_to_sat, satellite_cost_lbs=None):
    M = 100000.0
    route_by_sat = {}
    route_by_task = {task_id: [] for task_id in task_map}
    for rid, col in enumerate(cols):
        route_by_sat.setdefault(col["sat_id"], []).append(rid)
        for task_id in col["tasks"]:
            route_by_task.setdefault(task_id, []).append(rid)

    cover_constrs = {}
    for task_id, routes in route_by_task.items():
        cover_constrs[task_id] = m.addConstr(
            gp.quicksum(y[rid] for rid in routes) == 1,
            f"E2_Cover_{task_id}")

    route_limit_constrs = {}
    satlb_constrs = {}
    for sat_id, tasks in e2_tasks_by_sat.items():
        route_limit_constrs[sat_id] = m.addConstr(
            gp.quicksum(y[rid] for rid in route_by_sat.get(sat_id, []))
            <= vehicles_per_satellite,
            f"E2_RouteLimit_{sat_id}",
        )
        if satellite_cost_lbs and sat_id in satellite_cost_lbs:
            satlb_constrs[sat_id] = m.addConstr(
                gp.quicksum(cols[rid]["cost"] * y[rid]
                            for rid in route_by_sat.get(sat_id, []))
                >= float(satellite_cost_lbs[sat_id]),
                f"E2_SatCostLB_{sat_id}",
            )

    tw_lo_constrs = {}
    tw_hi_constrs = {}
    for task_id, task in task_map.items():
        e_j, l_j = task.tw
        tw_lo_constrs[task_id] = m.addConstr(A2[task_id] >= e_j, f"E2_TaskTWlo_{task_id}")
        if l_j < float("inf"):
            tw_hi_constrs[task_id] = m.addConstr(A2[task_id] <= l_j, f"E2_TaskTWhi_{task_id}")

    for rid, col in enumerate(cols):
        path = col["path"]
        sat_id = col["sat_id"]
        m.addConstr(S[rid] <= M * y[rid], f"E2_StartUsed_{rid}")
        m.addConstr(R[rid] <= M * y[rid], f"E2_ReturnUsed_{rid}")
        prev = sat_id
        for node in path[1:-1]:
            task = task_map[node]
            travel = problem.travel_time(_phys(task_map, prev), task.cust)
            if prev == sat_id:
                m.addConstr(
                    A2[node] >= S[rid] + travel - M * (1 - y[rid]),
                    f"E2_RouteTime_{rid}_{node}",
                )
            else:
                service_prev = _service_time(problem, task_map, prev)
                m.addConstr(
                    A2[node] >= A2[prev] + service_prev + travel
                    - M * (1 - y[rid]),
                    f"E2_RouteTime_{rid}_{prev}_{node}",
                )
            prev = node
        if len(path) > 2:
            last = path[-2]
            return_time = problem.travel_time(_phys(task_map, last), sat_id)
            service_last = _service_time(problem, task_map, last)
            expr = A2[last] + service_last + return_time
            m.addConstr(R[rid] >= expr - M * (1 - y[rid]),
                        f"E2_ReturnLo_{rid}")

    for cust_id in sorted({t.cust for t in task_map.values()}):
        d_id, p_id = f"{cust_id}d", f"{cust_id}p"
        if d_id not in task_map or p_id not in task_map:
            continue
        service_d = _service_time(problem, task_map, d_id)
        m.addConstr(A2[p_id] >= A2[d_id] + service_d,
                    f"E2_CustPrec_{cust_id}")

    return {
        "cover": cover_constrs,
        "route_limit": route_limit_constrs,
        "tw_lo": tw_lo_constrs,
        "tw_hi": tw_hi_constrs,
        "sat_cost_lb": satlb_constrs,
    }


def _add_linking_constraints(m, problem, e1_tasks, e2_tasks_by_sat, cols, x, A,
                             y, S, R, n1e, K, depot_id, e1_task_map,
                             e2_task_map, e1_service):
    M = 100000.0
    routes_by_sat = {}
    for rid, col in enumerate(cols):
        routes_by_sat.setdefault(col["sat_id"], []).append(rid)

    link_start_constrs = {}
    link_pickup_constrs = {}
    for sat_id, route_ids in routes_by_sat.items():
        del_node = f"{sat_id}d"
        pick_node = f"{sat_id}p"
        if del_node in e1_task_map:
            service_del = e1_service.get(del_node, 0.0)
            for rid in route_ids:
                for k in K:
                    visits_del = gp.quicksum(x[i, del_node, k]
                                             for i in n1e if i != del_node)
                    key = (sat_id, rid, k)
                    link_start_constrs[key] = m.addConstr(
                        S[rid] >= A[del_node, k] + service_del
                        - M * (2 - y[rid] - visits_del),
                        f"Link_Start_{sat_id}_{rid}_{k}",
                    )
        if pick_node in e1_task_map:
            for rid in route_ids:
                for k in K:
                    visits_pick = gp.quicksum(x[i, pick_node, k]
                                              for i in n1e if i != pick_node)
                    key = (sat_id, rid, k)
                    link_pickup_constrs[key] = m.addConstr(
                        A[pick_node, k] >= R[rid]
                        - M * (2 - y[rid] - visits_pick),
                        f"Link_Pickup_{sat_id}_{rid}_{k}",
                    )
    return {"link_start": link_start_constrs, "link_pickup": link_pickup_constrs}
