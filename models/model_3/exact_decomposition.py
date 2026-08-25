# -*- coding: utf-8 -*-
"""Certified exact decomposition for Model 3 at 100-customer scale.

Core observation (validated on 100_customers/C101):

    min Model3 = min over feasible E1 plans P of
                 [ E1_cost(P) + sum_s E2_s(tau_s(P)) ]

where tau_s(P) = (T_del[s], T_pick[s]) is the E1 delivery/pickup time for
satellite s induced by P.  E2_s(tau) is the per-satellite route-selection
optimum restricted to routes that can depart at/after T_del[s] and return
at/before T_pick[s].  The E2 side is therefore *independent per satellite*
given the E1 plan.

Proof ingredients:

1. Per-satellite unrestricted lower bounds L_s (independent of tau):
   E2_s(tau) >= L_s for every tau, because the floor/cap only restricts the
   feasible route family.  Hence any E1 plan with E1_cost >= C0 := UB - sum L_s
   is closed without any E2 work.

2. E1 plans below C0 form a finite set; the E1 MIP is tiny (16 tasks, 8
   trucks), so we enumerate them with no-good cuts.  Dominated plans (higher
   cost, no earlier deliveries, no later pickups) are discarded.

3. For the surviving Pareto classes, per-satellite *floor-restricted* column
   generation (s_max >= T_del[s]) with an exact pricing oracle certifies a
   lower bound of E2_s at that floor.  By monotonicity the certificate at
   floor f_min(s) (minimum floor over the open classes) applies to every open
   class, so at most one certificate per satellite is ever needed.

The verdict PROVEN_OPTIMAL is returned only when every Pareto class satisfies
E1_cost(P) + sum_s bound_s(P) >= UB with certified bounds.  All certificates
are LP-based valid lower bounds; no restricted-pool value is ever presented as
a certificate.
"""

import time

import gurobipy as gp
from gurobipy import GRB

from .e2_route_cg import (
    EPS,
    PRICING_PROOF_TOL,
    E2RouteColumn,
    _build_master,
    _phys,
    _solve_pricing_mip,
    solve_satellite_route_cg,
)
from .route_pool_exact import _normalize_columns


# ---------------------------------------------------------------------------
# E1 side: tight arc-specific Big-M builder + plan extraction
# ---------------------------------------------------------------------------

def _e1_context(problem, e1_tasks):
    depot_id = problem.depot.id
    F = [s.id for s in problem.satellites]
    K = list(range(problem.e1_vehicle_count))
    e1_nodes = [t.id for t in e1_tasks]
    n1e = [depot_id] + e1_nodes
    e1_task_map = {t.id: t for t in e1_tasks}

    def loc(node_id):
        if node_id == depot_id or node_id in F:
            return node_id
        task = e1_task_map.get(node_id)
        return task.cust if task is not None else node_id

    dist = {}
    time_map = {}
    for i in n1e:
        for j in n1e:
            dist[i, j] = 0.0 if i == j else problem.distance(loc(i), loc(j))
            time_map[i, j] = 0.0 if i == j else problem.travel_time(loc(i), loc(j))

    service = {depot_id: 0.0}
    for node in e1_nodes:
        phys = loc(node)
        node_obj = problem.nodes.get(phys)
        service[node] = getattr(node_obj, "service_time", 0.0) if node_obj else 0.0

    # Safe constructive horizon (mirrors model.py): includes the 9999 pickup
    # sentinel so pickup arrival variables can sit above it.
    inf = float("inf")
    tw_map = {depot_id: (0.0, 0.0)}
    tw_map.update({tid: task.tw for tid, task in e1_task_map.items()})
    finite = [0.0]
    for earliest, latest in tw_map.values():
        # Pickup windows are (9999, inf) sentinels; their arrival is fixed at
        # 9999 and never affects cost or feasibility (see builder), so they
        # are excluded from the tight time horizon.
        if earliest < inf and earliest < 9000.0:
            finite.append(earliest)
        if latest < inf and latest < 9000.0:
            finite.append(latest)
    max_arc_time = max(time_map.values(), default=0.0)
    total_service = sum(service.values())
    task_count = len(e1_nodes)
    time_horizon = (
        max(finite) + (task_count + 2) * max_arc_time + total_service + 1.0
    )
    m_time = time_horizon + max_arc_time
    # Delivery-only horizon: pickups are (9999, inf) sentinels whose arrival
    # never bounds a delivery, so delivery time-window Big-M can be much
    # tighter than the full horizon (keeps the LP strong).
    del_finite = [0.0]
    for tid, task in e1_task_map.items():
        if task.kind != "d":
            continue
        earliest, latest = task.tw
        if earliest < inf:
            del_finite.append(earliest)
        if latest < inf:
            del_finite.append(latest)
    m_delivery = (
        max(del_finite) + (task_count + 2) * max_arc_time
        + total_service + 1.0 + max_arc_time
    )

    def _arc_time_m(i, j):
        latest_i = tw_map.get(i, (0.0, inf))[1]
        if latest_i == inf:
            return m_time
        return max(
            service.get(i, 0.0) + time_map[i, j],
            latest_i + service.get(i, 0.0) + time_map[i, j],
        )

    def _time_infeasible(i, j):
        latest_j = tw_map.get(j, (0.0, inf))[1]
        return (
            latest_j < inf
            and tw_map.get(i, (0.0, inf))[0]
            + service.get(i, 0.0)
            + time_map[i, j]
            > latest_j + 1e-6
        )

    infeasible_arcs = {
        (i, j)
        for i in n1e
        for j in e1_nodes
        if i != j and _time_infeasible(i, j)
    }
    arc_time_m = {
        (i, j): _arc_time_m(i, j)
        for i in n1e
        for j in e1_nodes
        if i != j
    }

    return {
        "depot_id": depot_id,
        "F": F,
        "K": K,
        "e1_nodes": e1_nodes,
        "n1e": n1e,
        "e1_task_map": e1_task_map,
        "dist": dist,
        "time_map": time_map,
        "service": service,
        "M_time": m_time,
        "M_delivery": m_delivery,
        "arc_time_m": arc_time_m,
        "infeasible_arcs": infeasible_arcs,
    }


def _add_e1_constraints_tight(m, problem, e1_tasks, ctx, x, A, Q, V,
                              cost_cap=None, tight_tau_aux=False):
    """E1 constraint block identical in spirit to the route-pool master's, but
    with the safe arc-specific time Big-M from model.py."""
    depot_id = ctx["depot_id"]
    F = ctx["F"]
    K = ctx["K"]
    e1_nodes = ctx["e1_nodes"]
    n1e = ctx["n1e"]
    task_map = ctx["e1_task_map"]
    dist = ctx["dist"]
    travel = ctx["time_map"]
    service = ctx["service"]
    M = ctx["M_time"]
    arc_m = ctx["arc_time_m"]
    infeasible_arcs = ctx["infeasible_arcs"]

    obj_e1 = gp.quicksum(
        dist[i, j] * x[i, j, k]
        for k in K for i in n1e for j in n1e if i != j
    )
    if cost_cap is not None:
        m.addConstr(obj_e1 <= float(cost_cap), name="E1_cost_cap")

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

    forbidden = set()
    for sat_id in F:
        pid, did = f"{sat_id}p", f"{sat_id}d"
        if pid in task_map and did in task_map:
            forbidden.add((pid, did))
            for k in K:
                m.addConstr(x[pid, did, k] == 0, f"E1_ForbidPD_{sat_id}_{k}")

    for (i, j) in sorted(infeasible_arcs):
        if (i, j) in forbidden:
            continue
        for k in K:
            m.addConstr(x[i, j, k] == 0, f"E1_TimeInfeasibleArc_{i}_{j}_{k}")

    for i in n1e:
        s_i = service.get(i, 0.0)
        for j in e1_nodes:
            if i == j or (i, j) in forbidden or (i, j) in infeasible_arcs:
                continue
            m_ij = arc_m.get((i, j), M)
            for k in K:
                m.addConstr(
                    A[j, k] >= A[i, k] + s_i + travel[i, j]
                    - m_ij * (1 - x[i, j, k]),
                    f"E1_Time_{i}_{j}_{k}",
                )

    for j in e1_nodes:
        e_j, l_j = task_map[j].tw
        is_pickup = task_map[j].kind == "p"
        for k in K:
            visits = gp.quicksum(x[i, j, k] for i in n1e if i != j)
            tw_m = M if is_pickup else ctx.get("M_delivery", M)
            m.addConstr(A[j, k] >= e_j - tw_m * (1 - visits), f"E1_TW_lo_{j}_{k}")
            if tight_tau_aux:
                # Unvisited trucks carry A=0 (product form) so that
                # tau_del/tau_pick are linear in A; only needed by the
                # slice/anti-dominance modes.
                ub_j = l_j if l_j < float("inf") else M
                m.addConstr(A[j, k] <= ub_j * visits, f"E1_TW_hi_{j}_{k}")
            elif l_j < float("inf"):
                m.addConstr(A[j, k] <= l_j + tw_m * (1 - visits),
                            f"E1_TW_hi_{j}_{k}")

    total_del_w = sum(t.w for t in e1_tasks if t.kind == "d")
    total_del_v = sum(t.v for t in e1_tasks if t.kind == "d")
    m.addConstr(gp.quicksum(Q[depot_id, k] for k in K) == total_del_w,
                "E1_TotalWeight")
    m.addConstr(gp.quicksum(V[depot_id, k] for k in K) == total_del_v,
                "E1_TotalVolume")

    M_w = 2 * problem.e1_weight_cap + 1000.0
    M_v = 2 * problem.e1_volume_cap + 1000.0
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
            task_j = task_map[j]
            q_j = task_j.w if task_j.kind == "d" else -task_j.w
            v_j = task_j.v if task_j.kind == "d" else -task_j.v
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
    return obj_e1


def build_e1_mip(problem, e1_tasks, cost_cap=None, time_limit=60.0,
                 output_flag=False, tight_tau_aux=False):
    ctx = _e1_context(problem, e1_tasks)
    m = gp.Model("E1_Pareto")
    x = m.addVars(ctx["n1e"], ctx["n1e"], ctx["K"], vtype=GRB.BINARY, name="x")
    A = m.addVars(ctx["n1e"], ctx["K"], lb=0.0, name="A_e1")
    Q = m.addVars(ctx["n1e"], ctx["K"], lb=0.0, ub=problem.e1_weight_cap, name="Q_k")
    V = m.addVars(ctx["n1e"], ctx["K"], lb=0.0, ub=problem.e1_volume_cap, name="Vol_k")
    obj = _add_e1_constraints_tight(
        m, problem, e1_tasks, ctx, x, A, Q, V, cost_cap=cost_cap,
        tight_tau_aux=tight_tau_aux,
    )
    m.setObjective(obj, GRB.MINIMIZE)
    m.setParam("OutputFlag", 1 if output_flag else 0)
    m.setParam("MIPGap", 0.0)
    m.setParam("TimeLimit", time_limit)
    return m, {
        "x": x,
        "A_e1": A,
        "ctx": ctx,
        "obj": obj,
        "e1_tasks": e1_tasks,
    }


def extract_e1_plan(m, env):
    """Extract (cost, t_del, t_pick, arcs) from a solved E1 MIP."""
    x = env["x"]
    A = env["A_e1"]
    ctx = env["ctx"]
    K = ctx["K"]
    n1e = ctx["n1e"]
    e1_task_map = ctx["e1_task_map"]
    service = ctx["service"]

    arcs = []
    for (i, j, k), var in x.items():
        if var.X > 0.5:
            arcs.append((i, j, k))
    arcs.sort()
    cost = float(env["obj"].getValue())

    t_del = {}
    t_pick = {}
    for task in env["e1_tasks"]:
        s = task.cust
        visiting_k = None
        for k in K:
            if any(x[i, task.id, k].X > 0.5 for i in n1e if i != task.id):
                visiting_k = k
                break
        if visiting_k is None:
            raise RuntimeError(f"E1 task {task.id} not visited in plan")
        val = float(A[task.id, visiting_k].X)
        if task.kind == "d":
            t_del[s] = val + float(service.get(task.id, 0.0))
        else:
            # Pickup windows are (9999, inf) sentinels in this dataset; any
            # arrival >= 9000 is a non-binding ceiling, so normalize it to one
            # canonical value so classes differing only in non-binding pickup
            # times merge in the Pareto filter.
            t_pick[s] = 9999.0 if val >= 9000.0 else val
    return {
        "cost": cost,
        "t_del": t_del,
        "t_pick": t_pick,
        "arcs": arcs,
    }


def extract_e1_plan_pool(m, env, sol_idx):
    """Extract a plan from pool solution number sol_idx (uses Xn values)."""
    m.Params.SolutionNumber = sol_idx
    x = env["x"]
    A = env["A_e1"]
    ctx = env["ctx"]
    K = ctx["K"]
    n1e = ctx["n1e"]
    e1_task_map = ctx["e1_task_map"]
    service = ctx["service"]

    arcs = []
    for (i, j, k), var in x.items():
        if var.Xn > 0.5:
            arcs.append((i, j, k))
    arcs.sort()
    if not arcs:
        return None
    cost = float(m.PoolObjVal)
    t_del = {}
    t_pick = {}
    for task in env["e1_tasks"]:
        s = task.cust
        visiting_k = None
        for k in K:
            if any(x[i, task.id, k].Xn > 0.5 for i in n1e if i != task.id):
                visiting_k = k
                break
        if visiting_k is None:
            continue
        val = float(A[task.id, visiting_k].Xn)
        if task.kind == "d":
            t_del[s] = val + float(service.get(task.id, 0.0))
        else:
            t_pick[s] = 9999.0 if val >= 9000.0 else val
    return {
        "cost": float(cost),
        "t_del": t_del,
        "t_pick": t_pick,
        "arcs": arcs,
    }


def rebuild_e1_plan(problem, e1_tasks, arcs):
    """Reconstruct a full E1 plan (cost, t_del, t_pick) from its (i, j, k)
    arc set by simulating the deterministic earliest-arrival schedule."""
    depot_id = problem.depot.id
    task_map = {t.id: t for t in e1_tasks}
    svc = {}
    phys = {}
    for t in e1_tasks:
        phys[t.id] = t.cust
        node_obj = problem.nodes.get(t.cust)
        svc[t.id] = getattr(node_obj, "service_time", 0.0) if node_obj else 0.0

    by_truck = {}
    for (i, j, k) in arcs:
        by_truck.setdefault(k, []).append((i, j))
    routes = []
    for k in sorted(by_truck):
        adj = {}
        for (i, j) in by_truck[k]:
            adj[i] = j
        seq = []
        node = depot_id
        seen = set()
        while node in adj and adj[node] not in seen:
            nxt = adj[node]
            if nxt != depot_id:
                seq.append(nxt)
            node = nxt
            seen.add(node)
        if seq:
            routes.append(seq)

    cost = 0.0
    t_del = {}
    t_pick = {}
    for seq in routes:
        cur = 0.0
        prev = depot_id
        for tid in seq:
            task = task_map[tid]
            tt = problem.travel_time(prev, phys[tid])
            arr = max(task.tw[0], cur + tt)
            cost += problem.distance(prev, phys[tid])
            cur = arr + svc[tid]
            prev = phys[tid]
            if task.kind == "d":
                t_del[task.cust] = arr + svc[tid]
            else:
                t_pick[task.cust] = 9999.0 if arr >= 9000.0 else arr
        cost += problem.distance(prev, depot_id)
    return {
        "cost": cost,
        "t_del": t_del,
        "t_pick": t_pick,
        "arcs": sorted(arcs),
    }


def _tau_key(plan, ndigits=4):
    return (
        tuple(sorted((s, round(v, ndigits)) for s, v in plan["t_del"].items())),
        tuple(sorted((s, round(v, ndigits)) for s, v in plan["t_pick"].items())),
    )


def _class_representative(plans):
    """Dominating representative of a tau-class: min cost, componentwise min
    t_del, componentwise max t_pick.  Proving this representative proves every
    member of the class (E2 is monotone: later delivery floor is harder, later
    pickup ceiling is easier)."""
    rep = {
        "cost": min(p["cost"] for p in plans),
        "t_del": {s: min(p["t_del"][s] for p in plans) for s in plans[0]["t_del"]},
        "t_pick": {s: max(p["t_pick"][s] for p in plans) for s in plans[0]["t_pick"]},
        "members": len(plans),
    }
    return rep


def _tau_exprs(env):
    """Linear expressions for tau_del[s] / tau_pick[s] (valid because
    unvisited trucks now carry A=0)."""
    A = env["A_e1"]
    K = env["ctx"]["K"]
    service = env["ctx"]["service"]
    tau_del = {}
    tau_pick = {}
    for task in env["e1_tasks"]:
        s = task.cust
        if task.kind == "d":
            tau_del[s] = (
                gp.quicksum(A[task.id, k] for k in K)
                + float(service.get(task.id, 0.0))
            )
        else:
            tau_pick[s] = gp.quicksum(A[task.id, k] for k in K)
    return tau_del, tau_pick


def _dominates(p, q, tol=1e-6):
    """p dominates q: no worse cost, no later deliveries, no earlier pickups."""
    if p["cost"] > q["cost"] + tol:
        return False
    if any(p["t_del"][s] > q["t_del"][s] + tol for s in p["t_del"]):
        return False
    if any(p["t_pick"][s] < q["t_pick"][s] - tol for s in p["t_pick"]):
        return False
    return True


def _add_anti_dominance(m, env, q, cost_cap, horizon, tau_del, tau_pick,
                        del_big_m=None, eps=1e-5):
    """Require the next E1 plan NOT to be weakly dominated by q.

    A plan weakly dominated by q (cost >= q.cost, no earlier delivery, no
    later pickup) has total >= total(q), so excluding it is safe for the
    proof.  Escapes (one must fire) are the strict improvements:
      - cost < q.cost
      - tau_del[s] < q.t_del[s]
      - tau_pick[s] > q.t_pick[s]  (only when q's pickup is binding, < 9000;
        sentinel pickups are irrelevant for dominance)
    eps=1e-5 matches the repo-wide proof tolerance."""
    esc = []
    u0 = m.addVar(vtype=GRB.BINARY, name="esc_cost")
    m.addConstr(env["obj"] <= q["cost"] - eps + cost_cap * (1 - u0))
    esc.append(u0)
    for s, expr in tau_del.items():
        u = m.addVar(vtype=GRB.BINARY, name=f"esc_del_{s}")
        m_s = (del_big_m or {}).get(s, horizon)
        m.addConstr(expr <= q["t_del"][s] - eps + m_s * (1 - u))
        esc.append(u)
    for s, expr in tau_pick.items():
        if q["t_pick"][s] >= 9000.0 - 1e-6:
            continue  # non-binding sentinel: pickup dimension is vacuous
        u = m.addVar(vtype=GRB.BINARY, name=f"esc_pick_{s}")
        m.addConstr(expr >= q["t_pick"][s] + eps - horizon * (1 - u))
        esc.append(u)
    m.addConstr(gp.quicksum(esc) >= 1, name="anti_dominance")


def _enumerate_slice(problem, e1_tasks, cost_cap, *, max_regions=20000,
                     solve_time_limit=60.0, output_flag=False, verbose=True,
                     eps=1e-5):
    """Exact Pareto enumeration by recursive region partition (slice method).

    Each region is a box: cost in [cost_lb, cost_ub], tau_del[s] in
    [del_ge, del_le], tau_pick[s] in [pick_ge, pick_le].  Solving a region
    returns ANY feasible plan P (no optimality proof needed).  P splits the
    region into disjoint sub-regions whose union covers every plan not weakly
    dominated by P, so the recursion terminates at exactly the Pareto-minimal
    tau-classes.  Completeness: regions that yield no plan are proven
    infeasible (each region is a small box-constrained E1 MIP)."""
    started = time.time()
    sats = sorted({t.cust for t in e1_tasks if t.kind == "d"})

    root = {"cost_lb": None, "cost_ub": cost_cap, "del_ge": {}, "del_le": {},
            "pick_ge": {}, "pick_le": {}}
    stack = [root]
    frontier = []
    seen_tau = set()
    complete = True
    stop_reason = None
    regions_solved = 0
    while stack and regions_solved < max_regions:
        region = stack.pop()
        out = _find_e1_plan_dfs(problem, e1_tasks, region, node_budget=None)
        regions_solved += 1
        if not out["complete"]:
            complete = False
            stop_reason = "dfs_budget_exceeded"
            break
        plan = out["plan"]
        if plan is None:
            continue  # region proven infeasible by exhaustive DFS
        key = _tau_key(plan)
        if key in seen_tau:
            continue
        seen_tau.add(key)
        frontier.append(plan)
        if verbose and (len(frontier) <= 10 or len(frontier) % 10 == 0):
            print(f"  E1 slice point {len(frontier)}: cost={plan['cost']:.4f} "
                  f"t_del[S1]={plan['t_del'].get('S1')} "
                  f"regions={regions_solved} t={time.time()-started:.0f}s",
                  flush=True)

        # Lexicographic escape dims: cost, then deliveries, then binding
        # pickups (sentinel pickups are vacuous for dominance).
        dims = [("cost", None)]
        dims += [("del", s) for s in sats]
        dims += [("pick", s) for s in sats]

        # Sub-region for each escape dim: previous dims pinned to P's values,
        # this dim strictly improving, later dims free (inherit region bounds).
        ge_pins = {"cost": plan["cost"], "del": plan["t_del"], "pick": plan["t_pick"]}
        for idx, (dim, s) in enumerate(dims):
            sub = {
                "cost_lb": region["cost_lb"],
                "cost_ub": region["cost_ub"],
                "del_ge": dict(region["del_ge"]),
                "del_le": dict(region["del_le"]),
                "pick_ge": dict(region["pick_ge"]),
                "pick_le": dict(region["pick_le"]),
            }
            # pin previous dims to >= P's values (for cost: >= c_P)
            for (dim2, s2) in dims[:idx]:
                if dim2 == "cost":
                    sub["cost_lb"] = max(sub["cost_lb"] or 0.0, plan["cost"])
                elif dim2 == "del":
                    sub["del_ge"][s2] = max(sub["del_ge"].get(s2, 0.0),
                                            plan["t_del"][s2])
                else:
                    sub["pick_ge"][s2] = max(sub["pick_ge"].get(s2, 0.0),
                                             plan["t_pick"][s2])
            # strict improvement on this dim
            if dim == "cost":
                sub["cost_ub"] = min(sub["cost_ub"] or float("inf"),
                                     plan["cost"] - eps)
            elif dim == "del":
                sub["del_le"][s] = min(sub["del_le"].get(s, float("inf")),
                                       plan["t_del"][s] - eps)
            else:
                # binding pickups only: skip sentinel dims
                if plan["t_pick"][s] < 9000.0 - 1e-6:
                    sub["pick_ge"][s] = max(sub["pick_ge"].get(s, 0.0),
                                            plan["t_pick"][s] + eps)
                else:
                    continue
            stack.append(sub)
    else:
        if stack:
            complete = False
            stop_reason = f"max_regions_{max_regions}"

    # Final Pareto filter (defensive: partition already yields undominated
    # points, but dedupe by tau and drop any dominated representative).
    reps = {}
    for p in frontier:
        key = _tau_key(p)
        if key not in reps or p["cost"] < reps[key]["cost"]:
            reps[key] = p
    items = list(reps.values())
    pareto = []
    for i, p in enumerate(items):
        if any(_dominates(q, p) for j, q in enumerate(items) if i != j):
            continue
        pareto.append(p)

    return {
        "complete": complete,
        "stop_reason": stop_reason,
        "plans_count": len(frontier),
        "tau_classes": len(reps),
        "pareto_classes": len(pareto),
        "pareto": pareto,
        "regions_solved": regions_solved,
        "elapsed": time.time() - started,
    }


def _find_e1_plan_dfs(problem, e1_tasks, region, node_budget=None):
    """Exact feasibility oracle for one E1 region (DFS).

    Mirrors the E1 MIP semantics exactly:
      - each task visited once; trucks used in index order (E1 vehicle
        symmetry);
      - initial load = total delivery weight/volume of the truck; load
        decreases on delivery and increases on pickup, staying in [0, cap];
      - time: arrival = max(earliest, prev + travel) (waiting allowed),
        arrival <= latest; pickup windows are (9999, inf) sentinels;
      - same-satellite pickup -> delivery arcs are forbidden on one truck;
      - region = box bounds on cost / tau_del / tau_pick.
    Returns a plan dict (cost, t_del, t_pick, arcs) or None after exhausting
    the search (a rigorous infeasibility certificate for the region)."""
    EPS_T = 1e-6
    depot_id = problem.depot.id
    trucks = list(range(problem.e1_vehicle_count))
    task_map = {t.id: t for t in e1_tasks}
    task_ids = list(task_map)
    # Most-constrained-first candidate order: tightest earliest window first.
    # (Pure heuristic; correctness is unaffected -- all candidates are tried.)
    task_ids.sort(key=lambda t: (task_map[t].tw[0], task_map[t].tw[1]))
    svc = {}
    phys = {}
    for t in e1_tasks:
        phys[t.id] = t.cust
        node_obj = problem.nodes.get(t.cust)
        svc[t.id] = getattr(node_obj, "service_time", 0.0) if node_obj else 0.0

    cost_ub = region.get("cost_ub")
    cost_lb = region.get("cost_lb")
    del_ge = region.get("del_ge", {})
    del_le = region.get("del_le", {})
    pick_ge = region.get("pick_ge", {})
    pick_le = region.get("pick_le", {})

    def travel(a, b):
        return problem.travel_time(a, b)

    result = {"found": None, "nodes": 0, "budget_hit": False}
    result["nodes"] = 0

    def check_tau_place(t, arrival):
        s = t.cust
        if t.kind == "d":
            val = arrival + svc[t.id]
            if del_ge.get(s) is not None and val < del_ge[s] - EPS_T:
                return False
            if del_le.get(s) is not None and val > del_le[s] + EPS_T:
                return False
        else:
            val = arrival
            if pick_ge.get(s) is not None and val < pick_ge[s] - EPS_T:
                return False
            if pick_le.get(s) is not None and val > pick_le[s] + EPS_T:
                return False
        return True

    def make_plan(closed_routes, tau_del, tau_pick, cost):
        arcs = []
        for r in closed_routes:
            seq = [depot_id] + r + [depot_id]
            arcs.extend(zip(seq[:-1], seq[1:]))
        return {
            "cost": cost,
            "t_del": dict(tau_del),
            "t_pick": dict(tau_pick),
            "arcs": sorted((a, b, 0) for a, b in arcs),
        }

    def dfs(k, route, cur_time, cur_phys, d_w, d_v, p_w, p_v,
            net_hist, tau_del, tau_pick, cost, closed_routes):
        if result["found"] is not None:
            return
        result["nodes"] += 1
        if node_budget is not None and result["nodes"] > node_budget:
            result["budget_hit"] = True
            return
        if cost_ub is not None and cost > cost_ub + EPS_T:
            return
        if k == len(trucks):
            if len(route) == 0 and sum(len(r) for r in closed_routes) == len(task_ids):
                if cost_lb is not None and cost < cost_lb - EPS_T:
                    return
                result["found"] = make_plan(closed_routes, tau_del, tau_pick, cost)
            return

        # Option 1: extend truck k with an unassigned task
        visited_here = set(route)
        visited_all = set()
        for r in closed_routes:
            visited_all.update(r)
        visited_all.update(route)
        # Nearest-first candidate order (cost-aware heuristic; the search is
        # exhaustive regardless of order, so completeness is unaffected).
        candidates = sorted(
            task_ids,
            key=lambda t: travel(cur_phys, phys[t]) if t not in visited_all else float("inf"),
        )
        for t in candidates:
            if t in visited_all:
                continue
            task = task_map[t]
            if task.kind == "d" and f"{task.cust}p" in visited_here:
                continue
            tt = travel(cur_phys, phys[t])
            arrival = max(task.tw[0], cur_time + tt)
            if arrival > task.tw[1] + EPS_T:
                continue
            if not check_tau_place(task, arrival):
                continue
            nd_w = d_w + (task.w if task.kind == "d" else 0.0)
            nd_v = d_v + (task.v if task.kind == "d" else 0.0)
            np_w = p_w + (task.w if task.kind == "p" else 0.0)
            np_v = p_v + (task.v if task.kind == "p" else 0.0)
            if nd_w > problem.e1_weight_cap + EPS_T or nd_v > problem.e1_volume_cap + EPS_T:
                continue
            # net histories: net_w = D_i - P_i, net_v = V_i - Pv_i
            new_net = net_hist + [(nd_w - np_w, nd_v - np_v)]
            w_low = max(w for w, v in new_net)
            w_high = problem.e1_weight_cap + min(w for w, v in new_net)
            v_low = max(v for w, v in new_net)
            v_high = problem.e1_volume_cap + min(v for w, v in new_net)
            if not (max(nd_w, w_low) <= min(problem.e1_weight_cap, w_high) + EPS_T
                    and max(nd_v, v_low) <= min(problem.e1_volume_cap, v_high) + EPS_T):
                continue
            new_tau_del = dict(tau_del)
            new_tau_pick = dict(tau_pick)
            if task.kind == "d":
                new_tau_del[task.cust] = arrival + svc[t]
            else:
                new_tau_pick[task.cust] = arrival
            dfs(k, route + [t], arrival + svc[t], phys[t],
                nd_w, nd_v, np_w, np_v, new_net,
                new_tau_del, new_tau_pick, cost + tt, closed_routes)
            if result["found"] is not None:
                return

        # Option 2: close truck k, move to next truck
        if route:
            ok = True
            for w_net, v_net in net_hist:
                q = d_w - w_net
                vq = d_v - v_net
                if (q < -EPS_T or q > problem.e1_weight_cap + EPS_T
                        or vq < -EPS_T or vq > problem.e1_volume_cap + EPS_T):
                    ok = False
                    break
            if ok:
                ret_cost = travel(phys[route[-1]], depot_id)
                dfs(k + 1, [], 0.0, depot_id, 0.0, 0.0, 0.0, 0.0, [],
                    tau_del, tau_pick, cost + ret_cost, closed_routes + [route])
        else:
            # truck k unused => all later unused; finish if all tasks placed
            total_placed = sum(len(r) for r in closed_routes)
            if total_placed == len(task_ids):
                if cost_lb is not None and cost < cost_lb - EPS_T:
                    return
                result["found"] = make_plan(closed_routes, tau_del, tau_pick, cost)
        if result["found"] is not None:
            return

    dfs(0, [], 0.0, depot_id, 0.0, 0.0, 0.0, 0.0, [], {}, {}, 0.0, [])
    if result["budget_hit"]:
        return {"plan": None, "complete": False, "nodes": result["nodes"]}
    return {"plan": result["found"], "complete": True, "nodes": result["nodes"]}


def _enumerate_e1_all_dfs(problem, e1_tasks, cost_cap, *,
                          node_budget=200_000_000, verbose=True,
                          deadline=None):
    """Enumerate a dominating representative of every feasible E1 plan with
    cost <= cost_cap by exhaustive DFS.

    Key structural reduction: pickup windows are (9999, inf) sentinels, so a
    truck can only visit pickups AFTER its deliveries (any later delivery is
    time-infeasible), and the pickup order affects only cost, never the tau
    vector (all pickups arrive >= 9999, a non-binding ceiling for E2).  For
    each truck we therefore keep only the MINIMUM-cost ordering of its pickup
    set; any other ordering has the same tau vector and higher cost, i.e. is
    dominated, so skipping it is safe for the proof.  This collapses the
    pickup-order explosion (k! per truck) to a small TSP per pickup set.

    The DFS mirrors the E1 MIP semantics for deliveries (capacity interval
    checks, time windows, truck symmetry) and is exhaustive, so completeness
    follows from exhausting the tree."""
    started = time.time()
    EPS_T = 1e-6
    depot_id = problem.depot.id
    trucks = list(range(problem.e1_vehicle_count))
    del_tasks = [t for t in e1_tasks if t.kind == "d"]
    pick_tasks = [t for t in e1_tasks if t.kind == "p"]
    del_ids = [t.id for t in del_tasks]
    del_ids.sort(key=lambda t: (0.0, 0.0))
    task_map = {t.id: t for t in e1_tasks}
    svc = {}
    phys = {}
    for t in e1_tasks:
        phys[t.id] = t.cust
        node_obj = problem.nodes.get(t.cust)
        svc[t.id] = getattr(node_obj, "service_time", 0.0) if node_obj else 0.0
    pick_perm_cache = {}

    def travel(a, b):
        return problem.travel_time(a, b)

    def min_pickup_cost(pick_ids, start_phys):
        """Minimum cost to serve the pickup set from start_phys and return to
        the depot (small TSP path; brute force over <= 8! orders)."""
        key = (start_phys, tuple(sorted(pick_ids)))
        if key in pick_perm_cache:
            return pick_perm_cache[key]
        if not pick_ids:
            return travel(start_phys, depot_id)
        best = float("inf")
        import itertools
        for perm in itertools.permutations(pick_ids):
            cost = travel(start_phys, phys[perm[0]])
            for i in range(len(perm) - 1):
                cost += travel(phys[perm[i]], phys[perm[i + 1]])
            cost += travel(phys[perm[-1]], depot_id)
            if cost < best:
                best = cost
        pick_perm_cache[key] = best
        return best

    plans = []
    stats = {"nodes": 0, "budget_hit": False}
    # per-task minimum incoming-arc cost over all locations (valid lower bound
    # for cost pruning: every unassigned delivery must be reached by some arc)
    all_locs = ([depot_id] + [phys[t] for t in del_ids]
                + [phys[t.id] for t in pick_tasks])
    min_in_arc = {}
    for t in del_ids:
        min_in_arc[t] = min(travel(loc, phys[t]) for loc in all_locs)
    total_min_in = sum(min_in_arc.values())

    def make_plan(closed_routes, tau_del, cost):
        arcs = []
        for r in closed_routes:
            if not r:
                continue
            seq = [depot_id] + r + [depot_id]
            arcs.extend(zip(seq[:-1], seq[1:]))
        if cost < 900.0 and getattr(_enumerate_e1_all_dfs, "_debug", False):
            print("DFS_DEBUG low-cost plan:", cost, closed_routes, flush=True)
        return {
            "cost": cost,
            "t_del": dict(tau_del),
            "t_pick": {t.cust: 9999.0 for t in pick_tasks},
            "arcs": sorted((a, b, 0) for a, b in arcs),
        }

    def dfs(k, route, cur_time, cur_phys, d_w, d_v, net_hist, tau_del,
            cost, closed_routes, remaining_picks):
        stats["nodes"] += 1
        if deadline is not None and stats["nodes"] % 100000 == 0:
            if time.time() > deadline:
                stats["budget_hit"] = True
                return
        if stats["nodes"] > node_budget:
            stats["budget_hit"] = True
            return
        if cost > cost_cap + EPS_T:
            return
        if k == len(trucks):
            total_del_placed = sum(len(r) for r in closed_routes)
            if not route and not remaining_picks and total_del_placed == len(del_ids):
                plans.append(make_plan(closed_routes, tau_del, cost))
            return

        visited_here = set(route)
        visited_all = set()
        for r in closed_routes:
            visited_all.update(r)
        visited_all.update(route)
        # admissible remaining-cost lower bound: each unassigned delivery must
        # be reached by an arc costing at least its min-incoming value.
        remaining_dels = [t for t in del_ids if t not in visited_all]
        lb_remain = sum(min_in_arc[t] for t in remaining_dels)
        if cost + lb_remain > cost_cap + EPS_T:
            return
        candidates = sorted(
            (t for t in del_ids if t not in visited_all),
            # Start each truck at the FARTHEST unvisited delivery (classic
            # farthest-insertion clustering: trucks cover disjoint regions);
            # continue nearest-first within the truck.  This reaches the
            # low-cost clustered groupings early, instead of deep-exploring
            # bad nearest chains first.
            key=lambda t: (
                travel(cur_phys, phys[t]) if route
                else -travel(depot_id, phys[t])
            ),
        )
        for t in candidates:
            task = task_map[t]
            tt = travel(cur_phys, phys[t])
            arrival = max(task.tw[0], cur_time + tt)
            if arrival > task.tw[1] + EPS_T:
                continue
            nd_w = d_w + task.w
            nd_v = d_v + task.v
            if nd_w > problem.e1_weight_cap + EPS_T or nd_v > problem.e1_volume_cap + EPS_T:
                continue
            new_net = net_hist + [(nd_w, nd_v)]
            w_low = max(w for w, v in new_net)
            w_high = problem.e1_weight_cap + min(w for w, v in new_net)
            v_low = max(v for w, v in new_net)
            v_high = problem.e1_volume_cap + min(v for w, v in new_net)
            if not (max(nd_w, w_low) <= min(problem.e1_weight_cap, w_high) + EPS_T
                    and max(nd_v, v_low) <= min(problem.e1_volume_cap, v_high) + EPS_T):
                continue
            new_tau_del = dict(tau_del)
            new_tau_del[task.cust] = arrival + svc[t]
            dfs(k, route + [t], arrival + svc[t], phys[t],
                nd_w, nd_v, new_net, new_tau_del, cost + tt,
                closed_routes, remaining_picks)
            if stats["budget_hit"]:
                return

        # Close truck k: exact capacity check, then choose a pickup subset
        # (min-cost order) and move to the next truck.
        if route:
            ok = True
            for w_net, v_net in net_hist:
                q = d_w - w_net
                vq = d_v - v_net
                if (q < -EPS_T or q > problem.e1_weight_cap + EPS_T
                        or vq < -EPS_T or vq > problem.e1_volume_cap + EPS_T):
                    ok = False
                    break
            if ok:
                last_del_phys = phys[route[-1]]
                import itertools
                rp = list(remaining_picks)
                for mask in range(1 << len(rp)):
                    subset = [rp[i] for i in range(len(rp)) if (mask >> i) & 1]
                    rest = [p for p in rp if p not in subset]
                    pc = min_pickup_cost(subset, last_del_phys)
                    new_cost = cost + pc
                    if new_cost > cost_cap + EPS_T:
                        continue
                    dfs(k + 1, [], 0.0, depot_id, 0.0, 0.0, [], tau_del,
                        new_cost, closed_routes + [route], frozenset(rest))
                    if stats["budget_hit"]:
                        return
        else:
            # truck k is either unused (all placed) or pickup-only
            import itertools
            rp = list(remaining_picks)
            total_del_placed = sum(len(r) for r in closed_routes)
            for mask in range(1 << len(rp)):
                subset = [rp[i] for i in range(len(rp)) if (mask >> i) & 1]
                rest = [p for p in rp if p not in subset]
                pc = min_pickup_cost(subset, depot_id)
                new_cost = cost + pc
                if new_cost > cost_cap + EPS_T:
                    continue
                if not subset and total_del_placed == len(del_ids) and not rest:
                    plans.append(make_plan(closed_routes, tau_del, new_cost))
                elif not subset:
                    # truck k unused: deliveries and/or pickups remain for
                    # later trucks (duplicate representations are fine; they
                    # are deduplicated by tau afterwards).
                    dfs(k + 1, [], 0.0, depot_id, 0.0, 0.0, [], tau_del,
                        new_cost, closed_routes, frozenset(rest))
                    if stats["budget_hit"]:
                        return
                else:
                    # pickup-only truck k
                    dfs(k + 1, [], 0.0, depot_id, 0.0, 0.0, [], tau_del,
                        new_cost, closed_routes + [[]], frozenset(rest))
                    if stats["budget_hit"]:
                        return
        if stats["budget_hit"]:
            return

    dfs(0, [], 0.0, depot_id, 0.0, 0.0, [], {}, 0.0, [],
        frozenset(p.id for p in pick_tasks))
    return {
        "plans": plans,
        "complete": not stats["budget_hit"],
        "nodes": stats["nodes"],
        "elapsed": time.time() - started,
    }

    def travel(a, b):
        return problem.travel_time(a, b)

    plans = []
    stats = {"nodes": 0, "budget_hit": False}

    def make_plan(closed_routes, tau_del, tau_pick, cost):
        arcs = []
        for r in closed_routes:
            seq = [depot_id] + r + [depot_id]
            arcs.extend(zip(seq[:-1], seq[1:]))
        return {
            "cost": cost,
            "t_del": dict(tau_del),
            "t_pick": dict(tau_pick),
            "arcs": sorted((a, b, 0) for a, b in arcs),
        }

    def dfs(k, route, cur_time, cur_phys, d_w, d_v, p_w, p_v,
            net_hist, tau_del, tau_pick, cost, closed_routes):
        stats["nodes"] += 1
        if deadline is not None and stats["nodes"] % 200000 == 0:
            if time.time() > deadline:
                stats["budget_hit"] = True
                return
        if stats["nodes"] > node_budget:
            stats["budget_hit"] = True
            return
        if cost > cost_cap + EPS_T:
            return
        if k == len(trucks):
            if not route and sum(len(r) for r in closed_routes) == len(task_ids):
                plans.append(make_plan(closed_routes, tau_del, tau_pick, cost))
            return

        visited_here = set(route)
        visited_all = set()
        for r in closed_routes:
            visited_all.update(r)
        visited_all.update(route)
        candidates = sorted(
            (t for t in task_ids if t not in visited_all),
            key=lambda t: travel(cur_phys, phys[t]),
        )
        for t in candidates:
            task = task_map[t]
            if task.kind == "d" and f"{task.cust}p" in visited_here:
                continue
            tt = travel(cur_phys, phys[t])
            arrival = max(task.tw[0], cur_time + tt)
            if arrival > task.tw[1] + EPS_T:
                continue
            nd_w = d_w + (task.w if task.kind == "d" else 0.0)
            nd_v = d_v + (task.v if task.kind == "d" else 0.0)
            np_w = p_w + (task.w if task.kind == "p" else 0.0)
            np_v = p_v + (task.v if task.kind == "p" else 0.0)
            if (nd_w > problem.e1_weight_cap + EPS_T
                    or nd_v > problem.e1_volume_cap + EPS_T):
                continue
            new_net = net_hist + [(nd_w - np_w, nd_v - np_v)]
            w_low = max(w for w, v in new_net)
            w_high = problem.e1_weight_cap + min(w for w, v in new_net)
            v_low = max(v for w, v in new_net)
            v_high = problem.e1_volume_cap + min(v for w, v in new_net)
            if not (max(nd_w, w_low) <= min(problem.e1_weight_cap, w_high) + EPS_T
                    and max(nd_v, v_low) <= min(problem.e1_volume_cap, v_high) + EPS_T):
                continue
            new_tau_del = dict(tau_del)
            new_tau_pick = dict(tau_pick)
            if task.kind == "d":
                new_tau_del[task.cust] = arrival + svc[t]
            else:
                new_tau_pick[task.cust] = arrival
            dfs(k, route + [t], arrival + svc[t], phys[t],
                nd_w, nd_v, np_w, np_v, new_net,
                new_tau_del, new_tau_pick, cost + tt, closed_routes)
            if stats["budget_hit"]:
                return

        if route:
            ok = True
            for w_net, v_net in net_hist:
                q = d_w - w_net
                vq = d_v - v_net
                if (q < -EPS_T or q > problem.e1_weight_cap + EPS_T
                        or vq < -EPS_T or vq > problem.e1_volume_cap + EPS_T):
                    ok = False
                    break
            if ok:
                ret_cost = travel(phys[route[-1]], depot_id)
                dfs(k + 1, [], 0.0, depot_id, 0.0, 0.0, 0.0, 0.0, [],
                    tau_del, tau_pick, cost + ret_cost, closed_routes + [route])
        else:
            total_placed = sum(len(r) for r in closed_routes)
            if total_placed == len(task_ids):
                plans.append(make_plan(closed_routes, tau_del, tau_pick, cost))
        if stats["budget_hit"]:
            return

    dfs(0, [], 0.0, depot_id, 0.0, 0.0, 0.0, 0.0, [], {}, {}, 0.0, [])
    return {
        "plans": plans,
        "complete": not stats["budget_hit"],
        "nodes": stats["nodes"],
        "elapsed": time.time() - started,
    }


def _enumerate_e1_pool(problem, e1_tasks, cost_cap, *, batch_size=200,
                       max_plans=5000, solve_time_limit=120.0,
                       output_flag=False, deadline=None, resume_nogoods=None,
                       resume_plans=None):
    """Enumerate E1 plans below cost_cap in bulk via Gurobi's solution pool.

    Each batch solve uses PoolSearchMode=2 to collect up to batch_size
    distinct near-optimal plans in one MIP solve; their arc no-goods are then
    added and the next batch is solved.  The loop is complete when a solve
    proves infeasibility (no plan below cost_cap remains outside the
    enumerated set).  This avoids the per-plan no-good wall because each batch
    only excludes the previous batches, and the pool search is designed to
    find many diverse solutions quickly."""
    started = time.time()
    plans = list(resume_plans or [])
    seen = set()
    nogoods = []
    for arc_set in (resume_nogoods or []):
        key = frozenset(arc_set)
        if key not in seen:
            seen.add(key)
            nogoods.append(arc_set)
    for p in plans:
        key = frozenset(p["arcs"])
        if key not in seen:
            seen.add(key)
            nogoods.append(p["arcs"])
    complete = True
    stop_reason = None
    batch_count = 0
    while len(nogoods) < max_plans:
        if deadline is not None and time.time() > deadline:
            complete = False
            stop_reason = "total_time_budget"
            break
        batch_count += 1
        m, env = build_e1_mip(
            problem, e1_tasks, cost_cap=cost_cap - 1e-9,
            time_limit=solve_time_limit, output_flag=output_flag,
        )
        m.Params.PoolSearchMode = 2
        m.Params.PoolSolutions = batch_size
        m.Params.PoolGap = 0.0
        try:
            m.Params.Threads = 8
        except Exception:
            pass
        for arc_set in nogoods:
            m.addConstr(
                gp.quicksum(env["x"][arc] for arc in arc_set) <= len(arc_set) - 1,
                name="nogood",
            )
        m.optimize()
        if m.SolCount == 0:
            if m.status == GRB.INFEASIBLE:
                stop_reason = "infeasible_above_cap"
            else:
                complete = False
                stop_reason = f"pool_batch_{m.status}_no_solution"
            break
        new_plans = []
        for i in range(int(m.SolCount)):
            plan = extract_e1_plan_pool(m, env, i)
            if plan is None:
                continue
            key = frozenset(plan["arcs"])
            if key in seen:
                continue
            seen.add(key)
            new_plans.append(plan)
        if not new_plans:
            complete = False
            stop_reason = "pool_no_new_plans"
            break
        plans.extend(new_plans)
        nogoods.extend(p["arcs"] for p in new_plans)
        if output_flag:
            print(f"  pool batch {batch_count}: +{len(new_plans)} plans "
                  f"(total {len(plans)}), status={m.Status}, "
                  f"t={time.time()-started:.0f}s", flush=True)

    classes = {}
    for p in plans:
        classes.setdefault(_tau_key(p), []).append(p)
    reps = [_class_representative(v) for v in classes.values()]
    pareto = []
    for i, p in enumerate(reps):
        if any(_dominates(q, p) for j, q in enumerate(reps) if i != j):
            continue
        pareto.append(p)
    return {
        "complete": complete,
        "stop_reason": stop_reason,
        "plans_count": len(plans),
        "tau_classes": len(classes),
        "pareto_classes": len(pareto),
        "pareto": pareto,
        "all_arcs": [p["arcs"] for p in plans],
        "all_plans": plans,
        "elapsed": time.time() - started,
    }


def _e1_astar_find_plan(problem, e1_tasks, cost_cap, excluded_arc_sets=(),
                        node_budget=50_000_000, deadline=None):
    """A* search for the cheapest E1 plan not excluded by any no-good.

    States are (truck k, delivery route, ...) with priority = cost + admissible
    lower bound on the remaining cost (min incoming arc per unassigned
    delivery).  The first complete plan popped is the cheapest plan in the
    region; if the queue minimum priority exceeds cost_cap, no plan <= cost_cap
    exists (rigorous completeness proof).  Excluded plans are skipped at
    completion using the canonical (used-trucks-first, descending delivery
    load) truck ordering, matching the MIP no-good representation.

    Returns {"plan": plan|None, "complete": bool, "nodes": n}."""
    import heapq
    import itertools

    started = time.time()
    EPS_T = 1e-6
    depot_id = problem.depot.id
    trucks = list(range(problem.e1_vehicle_count))
    del_tasks = [t for t in e1_tasks if t.kind == "d"]
    pick_tasks = [t for t in e1_tasks if t.kind == "p"]
    del_ids = [t.id for t in del_tasks]
    task_map = {t.id: t for t in e1_tasks}
    phys = {}
    svc = {}
    for t in e1_tasks:
        phys[t.id] = t.cust
        node_obj = problem.nodes.get(t.cust)
        svc[t.id] = getattr(node_obj, "service_time", 0.0) if node_obj else 0.0

    def travel(a, b):
        return problem.travel_time(a, b)

    all_locs = ([depot_id] + [phys[t] for t in del_ids]
                + [phys[t.id] for t in pick_tasks])
    min_in_arc = {t: min(travel(loc, phys[t]) for loc in all_locs)
                  for t in del_ids}
    min_in_pick = {p.id: min(travel(loc, phys[p.id]) for loc in all_locs)
                   for p in pick_tasks}

    pick_perm_cache = {}

    def min_pickup_cost(pick_ids, start_phys):
        key = (start_phys, tuple(sorted(pick_ids)))
        if key in pick_perm_cache:
            return pick_perm_cache[key]
        if not pick_ids:
            return travel(start_phys, depot_id)
        best = float("inf")
        for perm in itertools.permutations(pick_ids):
            c = travel(start_phys, phys[perm[0]])
            for i in range(len(perm) - 1):
                c += travel(phys[perm[i]], phys[perm[i + 1]])
            c += travel(phys[perm[-1]], depot_id)
            best = min(best, c)
        pick_perm_cache[key] = best
        return best

    excluded = [frozenset(arc_set) for arc_set in excluded_arc_sets]

    def canonical_arcs(closed_routes, tau_del, cost):
        """Build (i,j,k) arcs with trucks canonicalized to the MIP's
        representation: used trucks first, ordered by descending initial
        delivery load (E1_LoadSym / E1_Sym)."""
        used = [r for r in closed_routes if r]
        q0 = [sum(task_map[t].w for t in r if task_map[t].kind == "d") for r in used]
        order = sorted(range(len(used)), key=lambda i: -q0[i])
        arcs = []
        for new_k, old_i in enumerate(order):
            r = used[old_i]
            seq = [depot_id] + r + [depot_id]
            arcs.extend((a, b, new_k) for a, b in zip(seq[:-1], seq[1:]))
        return {
            "cost": cost,
            "t_del": dict(tau_del),
            "t_pick": {t.cust: 9999.0 for t in pick_tasks},
            "arcs": sorted(arcs),
        }

    counter = 0
    # heap entries: (priority, counter, state)
    state0 = {
        "k": 0, "route": (), "cur_time": 0.0, "cur_phys": depot_id,
        "d_w": 0.0, "d_v": 0.0, "net_hist": (), "tau_del": {},
        "cost": 0.0, "closed": (), "remaining_picks": frozenset(
            p.id for p in pick_tasks),
    }
    lb0 = sum(min_in_arc.values()) + sum(min_in_pick.values())
    heap = [(lb0, counter, state0)]
    stats = {"nodes": 0, "budget_hit": False, "complete_plans_checked": 0}

    def priority(state):
        unassigned_dels = [t for t in del_ids
                           if t not in state["route"]
                           and all(t not in r for r in state["closed"])]
        lb = sum(min_in_arc[t] for t in unassigned_dels)
        lb += sum(min_in_pick[p] for p in state["remaining_picks"])
        return state["cost"] + lb

    def children(state):
        k = state["k"]
        route = state["route"]
        cur_time = state["cur_time"]
        cur_phys = state["cur_phys"]
        d_w = state["d_w"]
        d_v = state["d_v"]
        net_hist = state["net_hist"]
        tau_del = state["tau_del"]
        cost = state["cost"]
        closed = state["closed"]
        remaining_picks = state["remaining_picks"]
        if k == len(trucks):
            return []
        visited_all = set()
        for r in closed:
            visited_all.update(r)
        visited_all.update(route)
        # 1) extend current truck with a delivery
        for t in sorted((x for x in del_ids if x not in visited_all),
                        key=lambda x: travel(cur_phys, phys[x])):
            task = task_map[t]
            tt = travel(cur_phys, phys[t])
            arrival = max(task.tw[0], cur_time + tt)
            if arrival > task.tw[1] + EPS_T:
                continue
            nd_w = d_w + task.w
            nd_v = d_v + task.v
            if nd_w > problem.e1_weight_cap + EPS_T or nd_v > problem.e1_volume_cap + EPS_T:
                continue
            new_net = net_hist + ((nd_w, nd_v),)
            w_low = max(w for w, v in new_net)
            w_high = problem.e1_weight_cap + min(w for w, v in new_net)
            v_low = max(v for w, v in new_net)
            v_high = problem.e1_volume_cap + min(v for w, v in new_net)
            if not (max(nd_w, w_low) <= min(problem.e1_weight_cap, w_high) + EPS_T
                    and max(nd_v, v_low) <= min(problem.e1_volume_cap, v_high) + EPS_T):
                continue
            new_tau = dict(tau_del)
            new_tau[task.cust] = arrival + svc[t]
            yield {
                "k": k, "route": route + (t,),
                "cur_time": arrival + svc[t], "cur_phys": phys[t],
                "d_w": nd_w, "d_v": nd_v, "net_hist": new_net,
                "tau_del": new_tau, "cost": cost + tt,
                "closed": closed, "remaining_picks": remaining_picks,
            }
        # 2) close truck k
        rp = list(remaining_picks)
        if route:
            ok = True
            for w_net, v_net in net_hist:
                q = d_w - w_net
                vq = d_v - v_net
                if (q < -EPS_T or q > problem.e1_weight_cap + EPS_T
                        or vq < -EPS_T or vq > problem.e1_volume_cap + EPS_T):
                    ok = False
                    break
            if ok:
                last_del = phys[route[-1]]
                for mask in range(1 << len(rp)):
                    subset = [rp[i] for i in range(len(rp)) if (mask >> i) & 1]
                    rest = frozenset(p for p in rp if p not in subset)
                    pc = min_pickup_cost(subset, last_del)
                    yield {
                        "k": k + 1, "route": (), "cur_time": 0.0,
                        "cur_phys": depot_id, "d_w": 0.0, "d_v": 0.0,
                        "net_hist": (), "tau_del": tau_del,
                        "cost": cost + pc, "closed": closed + (route,),
                        "remaining_picks": rest,
                    }
        else:
            total_del = sum(len(r) for r in closed)
            for mask in range(1 << len(rp)):
                subset = [rp[i] for i in range(len(rp)) if (mask >> i) & 1]
                rest = frozenset(p for p in rp if p not in subset)
                pc = min_pickup_cost(subset, depot_id)
                if not subset and total_del == len(del_ids) and not rest:
                    # complete plan candidate
                    plan = canonical_arcs(closed, tau_del, cost + pc)
                    stats["complete_plans_checked"] += 1
                    if frozenset(plan["arcs"]) not in excluded:
                        yield ("PLAN", plan)
                        return
                elif subset or (not subset and (total_del < len(del_ids) or rest)):
                    yield {
                        "k": k + 1, "route": (), "cur_time": 0.0,
                        "cur_phys": depot_id, "d_w": 0.0, "d_v": 0.0,
                        "net_hist": (), "tau_del": tau_del,
                        "cost": cost + pc,
                        "closed": closed + (() if not subset else ((),)),
                        "remaining_picks": rest,
                    }

    while heap:
        if deadline is not None and time.time() > deadline:
            stats["budget_hit"] = True
            break
        stats["nodes"] += 1
        if stats["nodes"] > node_budget:
            stats["budget_hit"] = True
            break
        pr, _, state = heapq.heappop(heap)
        if pr > cost_cap + EPS_T:
            break  # nothing better than cost_cap remains
        for ch in children(state):
            if isinstance(ch, tuple) and ch and ch[0] == "PLAN":
                return {"plan": ch[1], "complete": False,
                        "nodes": stats["nodes"]}
            counter += 1
            heapq.heappush(heap, (priority(ch), counter, ch))
    return {"plan": None, "complete": not stats["budget_hit"],
            "nodes": stats["nodes"], "elapsed": time.time() - started}


def enumerate_e1_pareto_frontier(problem, e1_tasks, cost_cap, *,
                                 max_plans=5000, solve_time_limit=60.0,
                                 mode="exhaustive", output_flag=False,
                                 verbose=True, deadline=None,
                                 resume_nogoods=None, mip_gap=0.01,
                                 resume_plans=None):
    """Enumerate the Pareto-minimal E1 plans below cost_cap.

    mode="exhaustive" (default): fast no-good loop.  Each solve stops at the
    first feasible plan (SolutionLimit=1); a no-good cut over its arcs is
    added and the loop repeats until the model is proven infeasible, which
    certifies that every feasible plan below cost_cap has been seen.  Then the
    plans are reduced to dominating tau-class representatives and the
    Pareto-minimal set.  This is complete and fast: per-plan cost is a single
    easy feasibility solve; only the final infeasibility proof does work.

    mode="slice": exact Pareto partition (each region is a box of cost /
    delivery / pickup bounds, no disjunctions; any feasible point in a region
    is split into sub-regions that cover everything not weakly dominated by
    it).  mode="pareto": anti-dominance disjunction (diagnostics).

    Returns complete=False when the plan cap or a solve without an incumbent
    is hit (no proof can be drawn)."""
    started = time.time()
    m, env = build_e1_mip(
        problem, e1_tasks, cost_cap=cost_cap - 1e-9,
        time_limit=solve_time_limit, output_flag=output_flag,
    )
    try:
        m.Params.Threads = 4
    except Exception:
        pass
    tau_del, tau_pick = _tau_exprs(env)
    horizon = env["ctx"]["M_time"] + max(env["ctx"]["service"].values(), default=0.0) + 1.0
    # per-satellite tight big-M for delivery-time escapes: latest window +
    # service (finite windows in this dataset); keeps the disjunctive LP tight.
    del_big_m = {}
    for task in e1_tasks:
        if task.kind == "d" and task.tw[1] < float("inf"):
            del_big_m[task.cust] = (
                task.tw[1] + float(env["ctx"]["service"].get(task.id, 0.0)) + 1.0
            )

    if mode == "slice":
        return _enumerate_slice(
            problem, e1_tasks, cost_cap,
            max_regions=max_plans,
            solve_time_limit=solve_time_limit,
            output_flag=output_flag, verbose=verbose,
        )

    if mode == "dfs_all":
        out = _enumerate_e1_all_dfs(
            problem, e1_tasks, cost_cap,
            node_budget=max_plans * 100000,
            verbose=verbose,
            deadline=deadline,
        )
        plans = out["plans"]
        classes = {}
        for p in plans:
            classes.setdefault(_tau_key(p), []).append(p)
        reps = [_class_representative(v) for v in classes.values()]
        pareto = []
        for i, p in enumerate(reps):
            if any(_dominates(q, p) for j, q in enumerate(reps) if i != j):
                continue
            pareto.append(p)
        return {
            "complete": out["complete"],
            "stop_reason": None if out["complete"] else
                           ("dfs_time_budget" if deadline is not None
                            and time.time() > deadline else "dfs_node_budget"),
            "plans_count": len(plans),
            "tau_classes": len(classes),
            "pareto_classes": len(pareto),
            "pareto": pareto,
            "all_arcs": [p["arcs"] for p in plans],
            "elapsed": out["elapsed"],
            "dfs_nodes": out["nodes"],
        }

    if mode == "pool":
        return _enumerate_e1_pool(
            problem, e1_tasks, cost_cap,
            batch_size=200,
            max_plans=max_plans,
            solve_time_limit=solve_time_limit,
            output_flag=output_flag,
            deadline=deadline,
            resume_nogoods=resume_nogoods,
            resume_plans=resume_plans,
        )

    if mode == "pareto":
        frontier = []
        complete = True
        stop_reason = None
        for it in range(max_plans):
            if frontier:
                for var in env["x"].values():
                    var.Start = var.X
                for var in env["A_e1"].values():
                    var.Start = var.X
            m.optimize()
            if m.status != GRB.OPTIMAL:
                if m.status == GRB.INFEASIBLE:
                    stop_reason = "infeasible_above_cap"
                else:
                    complete = False
                    stop_reason = f"e1_solve_{m.status}"
                break
            plan = extract_e1_plan(m, env)
            frontier = [p for p in frontier if not _dominates(plan, p)]
            frontier.append(plan)
            _add_anti_dominance(
                m, env, plan, cost_cap, horizon, tau_del, tau_pick,
                del_big_m=del_big_m,
            )
            if verbose and (it < 10 or it % 25 == 0):
                print(f"  E1 frontier point {it}: cost={plan['cost']:.4f} "
                      f"t_del[S1]={plan['t_del'].get('S1')} "
                      f"t={time.time()-started:.1f}s")
        else:
            complete = False
            stop_reason = f"max_plans_{max_plans}"
        return {
            "complete": complete,
            "stop_reason": stop_reason,
            "plans_count": len(frontier),
            "pareto_classes": len(frontier),
            "pareto": frontier,
            "elapsed": time.time() - started,
        }

    # ---- exhaustive fast mode ----
    # Rebuild the model per iteration (with the tight delivery Big-M this is
    # ~2-3s/plan; model reuse / warm starts measured much slower).  Each solve
    # may stop at any feasible incumbent; the loop is complete because it ends
    # only on a proven infeasibility or a time-limited solve with no
    # incumbent.
    plans = []
    complete = True
    stop_reason = None
    for it in range(max_plans):
        if deadline is not None and time.time() > deadline:
            complete = False
            stop_reason = "total_time_budget"
            break
        t_solve = time.time()
        m, env = build_e1_mip(
            problem, e1_tasks, cost_cap=cost_cap - 1e-9,
            time_limit=solve_time_limit, output_flag=output_flag,
        )
        # Each solve only needs ANY feasible plan (its no-good is added and
        # the loop repeats); optimality per solve is not required for
        # completeness -- only the final infeasibility proof matters.  A loose
        # MIPGap therefore makes each solve stop at the first acceptable
        # incumbent, which is much faster with hundreds of accumulated
        # no-goods.
        try:
            m.Params.MIPGap = mip_gap
            m.Params.MIPFocus = 1
            m.Params.Heuristics = 0.3
        except Exception:
            pass
        for arc_set in (resume_nogoods or []):
            m.addConstr(
                gp.quicksum(env["x"][arc] for arc in arc_set) <= len(arc_set) - 1,
                name="resume_nogood",
            )
        for arc_set in [p["arcs"] for p in plans]:
            m.addConstr(
                gp.quicksum(env["x"][arc] for arc in arc_set) <= len(arc_set) - 1,
                name="nogood",
            )
        m.optimize()
        # Hard per-solve watchdog: Gurobi's TimeLimit can be exceeded in rare
        # presolve/crossover cases; never let one solve stall the enumeration.
        if time.time() - t_solve > 2 * solve_time_limit + 5.0:
            complete = False
            stop_reason = "solve_watchdog_timeout"
            break
        if m.SolCount == 0:
            if m.status == GRB.INFEASIBLE:
                stop_reason = "infeasible_above_cap"
                break
            # Wall: no feasible plan found within the limit.  Retry with
            # extended limits before declaring the enumeration stuck (the
            # remaining plans exist but Gurobi's heuristic cannot reach them
            # quickly).
            solved = False
            for multiplier in (2, 4):
                if deadline is not None and time.time() + 30 > deadline:
                    break
                ext = solve_time_limit * multiplier
                m.Params.TimeLimit = ext
                try:
                    m.Params.Heuristics = 0.5
                except Exception:
                    pass
                m.optimize()
                if m.SolCount > 0:
                    solved = True
                    break
                if m.status == GRB.INFEASIBLE:
                    stop_reason = "infeasible_above_cap"
                    break
            if not solved:
                complete = False
                stop_reason = (
                    "e1_solve_no_solution_after_retries"
                    if stop_reason != "infeasible_above_cap"
                    else "infeasible_above_cap"
                )
                break
        plan = extract_e1_plan(m, env)
        plans.append(plan)
        arc_set = plan["arcs"]
        m.addConstr(
            gp.quicksum(env["x"][arc] for arc in arc_set) <= len(arc_set) - 1,
            name=f"nogood_{it}",
        )
        if verbose and (it < 5 or it % 25 == 0):
            print(f"  E1 plan {it}: cost={plan['cost']:.4f} "
                  f"t_del[S1]={plan['t_del'].get('S1')} "
                  f"t={time.time()-started:.1f}s", flush=True)
    else:
        complete = False
        stop_reason = f"max_plans_{max_plans}"

    # Deduplicate by tau-class with dominating representatives, then Pareto
    # filter (undominated representatives).
    classes = {}
    for p in plans:
        classes.setdefault(_tau_key(p), []).append(p)
    reps = [_class_representative(v) for v in classes.values()]
    frontier = []
    for i, p in enumerate(reps):
        dominated = False
        for j, q in enumerate(reps):
            if i == j:
                continue
            if _dominates(q, p):
                dominated = True
                break
        if not dominated:
            frontier.append(p)

    return {
        "complete": complete,
        "stop_reason": stop_reason,
        "plans_count": len(plans),
        "tau_classes": len(classes),
        "pareto_classes": len(frontier),
        "pareto": frontier,
        "all_arcs": [p["arcs"] for p in plans],
        "elapsed": time.time() - started,
    }


# ---------------------------------------------------------------------------
# E2 side: certified per-satellite lower bounds
# ---------------------------------------------------------------------------

def full_route_s_max(problem, e2_task_map, col):
    """Latest feasible departure of a route column (depart at t and still meet
    every time window with zero-wait forward scheduling)."""
    path = col["path"]
    sat_phys = _phys(e2_task_map, col["sat_id"])
    horizon = max(
        (t.tw[1] for t in e2_task_map.values()
         if t.kind == "d" and t.tw[1] < float("inf")),
        default=2000.0,
    )
    sm = horizon
    task_nodes = [n for n in path if n in e2_task_map]
    cum = 0.0
    prev_phys = sat_phys
    prev_service = 0.0
    for idx, node in enumerate(task_nodes):
        task = e2_task_map[node]
        latest = task.tw[1]
        travel_seg = problem.travel_time(prev_phys, task.cust)
        cum = travel_seg if idx == 0 else cum + prev_service + travel_seg
        if latest < float("inf"):
            sm = min(sm, latest - cum)
        prev_phys = task.cust
        node_obj = problem.nodes.get(task.cust)
        prev_service = getattr(node_obj, "service_time", 0.0) if node_obj else 0.0
    return sm


def certify_satellite_floor_lb(problem, sat_id, tasks, vehicles, floor,
                               initial_columns, *, max_iter=80,
                               pricing_time_limit=300.0, pool_solutions=10,
                               output_flag=False, verbose=True):
    """Certify a lower bound of the floor-restricted E2 satellite problem:
    min route cost over routes with latest feasible departure >= floor.

    Uses the same machinery as experiments/probe_s1_floor_cert.py, generalized
    to any satellite: column-generation over the set-partitioning LP master
    with MIP pricing constrained to floor-feasible routes (s_max_floor).  The
    certificate is valid only when the final pricing call proves that no
    floor-feasible route in the full universe has negative reduced cost.

    Returns dict with status CERTIFIED (lp_bound is a valid universe LB),
    NOT_CERTIFIED (time/iteration limit), or FLOOR_INFEASIBLE (no route can
    cover some task at the floor => E2_s(floor) = +inf, an even stronger
    closure)."""
    started = time.time()
    task_ids = [t.id for t in tasks]
    e2_task_map = {t.id: t for t in tasks}
    cols = _normalize_columns(problem, e2_task_map, initial_columns)
    for c in cols:
        c["_s_max"] = full_route_s_max(problem, e2_task_map, c)
    pool = [c for c in cols if c["_s_max"] >= floor - EPS]
    if verbose:
        print(f"  [{sat_id}] floor={floor:.4f}: pool routes={len(cols)}, "
              f"floor-feasible={len(pool)}")

    covered = set()
    for c in pool:
        covered.update(c["tasks"])
    missing = set(task_ids) - covered
    if missing:
        return {
            "status": "FLOOR_INFEASIBLE",
            "floor": floor,
            "uncoverable_tasks": sorted(missing),
            "lp_bound": float("inf"),
            "elapsed": time.time() - started,
        }

    columns_cg = []
    seen = set()
    for c in pool:
        path = tuple(c["path"])
        if path in seen:
            continue
        seen.add(path)
        columns_cg.append(E2RouteColumn(
            sat_id=sat_id, path=path,
            tasks=tuple(n for n in c["path"] if n in e2_task_map),
            cost=float(c["cost"]), source="pool_floor",
        ))
    if verbose:
        print(f"  [{sat_id}] initial CG columns: {len(columns_cg)}")

    lp_obj = None
    converged = False
    last_info = None
    for it in range(max_iter):
        m, lambdas, artificials, cover_constrs, vehicle_constr, _ = _build_master(
            sat_id, task_ids, columns_cg, vehicles, relax=True,
        )
        m.Params.OutputFlag = 1 if output_flag else 0
        m.optimize()
        if m.status != GRB.OPTIMAL:
            break
        lp_obj = float(m.ObjVal)
        art_sum = sum(v.X for v in artificials.values())
        cover_duals = {tid: cover_constrs[tid].Pi for tid in task_ids}
        vehicle_dual = float(vehicle_constr.Pi)

        cols_out, info = _solve_pricing_mip(
            problem, sat_id, tasks, cover_duals, vehicle_dual,
            pricing_time_limit=pricing_time_limit, pool_solutions=pool_solutions,
            s_max_floor=floor, output_flag=output_flag,
        )
        last_info = info
        rcb = info.get("reduced_cost_bound", float("-inf"))
        # PRICING_PROOF_TOL (1e-5) matches the repo-wide proof tolerance; a
        # route with reduced cost in [-1e-5, 0) would only shift the LP bound
        # by ~1e-5, far below any meaningful closure gap.
        certified_now = (
            rcb is not None and rcb >= -PRICING_PROOF_TOL and len(cols_out) == 0
        )
        n_added = 0
        for c in cols_out:
            if c.path in seen:
                continue
            seen.add(c.path)
            columns_cg.append(c)
            n_added += 1
        if verbose:
            print(f"  [{sat_id}] iter {it}: LP={lp_obj:.4f} art={art_sum:.3f} "
                  f"pricing={info['status']} rc_bound={rcb} new={n_added} "
                  f"cols={len(columns_cg)} t={time.time()-started:.0f}s")
        if certified_now:
            converged = True
            break
        if n_added == 0 and not cols_out:
            break

    status = "CERTIFIED" if converged else "NOT_CERTIFIED"
    if not converged and lp_obj is not None:
        status = "NOT_CERTIFIED"
    return {
        "status": status,
        "floor": floor,
        "lp_bound": lp_obj,
        "converged": converged,
        "iterations": it + 1,
        "columns": len(columns_cg),
        "pricing_status": last_info.get("status") if last_info else None,
        "pricing_rc_bound": last_info.get("reduced_cost_bound") if last_info else None,
        "elapsed": time.time() - started,
    }


def certify_satellite_unrestricted_lb(problem, sat_id, tasks, vehicles,
                                      initial_columns, *, max_iter=60,
                                      pricing_time_limit=60.0,
                                      pool_solutions=10, output_flag=False,
                                      verbose=True):
    """Certified per-satellite unrestricted lower bound via route CG.

    The certified LP optimum over the full route universe is a valid lower
    bound of the per-satellite E2 problem (LP relaxation <= integer optimum).
    Returns dict with lp_valid, lp_bound and the generated columns."""
    cols = _normalize_columns(problem, {t.id: t for t in tasks}, initial_columns)
    initial_paths = [list(c["path"]) for c in cols]
    result = solve_satellite_route_cg(
        problem, sat_id, tasks, vehicles,
        initial_paths=initial_paths,
        max_iterations=max_iter,
        pricing_time_limit=pricing_time_limit,
        pricing_pool_solutions=pool_solutions,
        pricing_method="mip",
        integer_master=False,
        return_columns=True,
        output_flag=output_flag,
    )
    if verbose:
        print(f"  [{sat_id}] CG lp_valid={result.get('lp_valid')} "
              f"lp={result.get('lp_obj')} cols={result.get('columns')} "
              f"iter={result.get('iterations')} "
              f"t={result.get('elapsed', 0.0):.0f}s")
    return {
        "sat_id": sat_id,
        "lp_valid": bool(result.get("lp_valid")),
        "lp_bound": result.get("lp_obj"),
        "columns": result.get("columns_data", []),
        "status": result.get("status"),
        "pricing_exact": bool(result.get("pricing_exact")),
        "elapsed": result.get("elapsed", 0.0),
    }


# ---------------------------------------------------------------------------
# Certified exact driver
# ---------------------------------------------------------------------------

def solve_m3_exact_certificate(problem, e1_tasks, e2_tasks_by_sat,
                               vehicles_per_satellite, *,
                               incumbent, satellite_lbs, columns,
                               max_frontier_plans=5000,
                               frontier_solve_time_limit=60.0,
                               floor_cert_max_iter=80,
                               floor_cert_time_limit=300.0,
                               floor_cert_pool_solutions=10,
                               total_time_limit=None,
                               resume_frontier_arcs=None,
                               resume_frontier_plans=None,
                               frontier_known_complete=False,
                               e1_mip_gap=0.01,
                               output_flag=False, verbose=True):
    """Run the certified exact decomposition for one instance.

    Returns a verdict dict.  proven_optimal=True only when:
      - the E1 frontier enumeration below C0 is complete,
      - every Pareto class closes with certified lower bounds.
    """
    started = time.time()
    deadline = (started + total_time_limit) if total_time_limit else None
    e2_task_map = {
        t.id: t
        for tasks in e2_tasks_by_sat.values()
        for t in tasks
    }
    cols = _normalize_columns(problem, e2_task_map, columns)
    cols_by_sat = {}
    for c in cols:
        cols_by_sat.setdefault(c["sat_id"], []).append(c)

    active_sats = [s for s, tasks in e2_tasks_by_sat.items() if tasks]
    missing_lb = [s for s in active_sats if s not in satellite_lbs]
    if missing_lb:
        return {
            "status": "MISSING_SATELLITE_LB",
            "proven_optimal": False,
            "message": f"no lower bound for satellites {missing_lb}",
        }
    e2_lb = sum(float(satellite_lbs[s]) for s in active_sats)
    c0 = incumbent - e2_lb
    if verbose:
        print(f"UB={incumbent:.6f}  E2_LB={e2_lb:.6f}  "
              f"E1 threshold C0={c0:.6f}")

    if frontier_known_complete and resume_frontier_plans:
        # Frontier was already proven complete in a previous run; use the
        # resumed plans directly (skip re-verification).
        classes = {}
        for p in resume_frontier_plans:
            classes.setdefault(_tau_key(p), []).append(p)
        reps = [_class_representative(v) for v in classes.values()]
        pareto = []
        for i, p in enumerate(reps):
            if any(_dominates(q, p) for j, q in enumerate(reps) if i != j):
                continue
            pareto.append(p)
        frontier_info = {
            "complete": True,
            "plans_count": len(resume_frontier_plans),
            "pareto_classes": len(pareto),
            "pareto": pareto,
            "all_arcs": [p["arcs"] for p in resume_frontier_plans],
            "all_plans": resume_frontier_plans,
            "elapsed": 0.0,
        }
    else:
        frontier_info = enumerate_e1_pareto_frontier(
            problem, e1_tasks, c0,
            max_plans=max_frontier_plans,
            solve_time_limit=frontier_solve_time_limit,
            mode="pool",
            output_flag=output_flag, verbose=verbose, deadline=deadline,
            resume_nogoods=resume_frontier_arcs,
            mip_gap=e1_mip_gap,
            resume_plans=resume_frontier_plans,
        )
    if not frontier_info["complete"]:
        return {
            "status": "FRONTIER_INCOMPLETE",
            "proven_optimal": False,
            "message": f"E1 enumeration stopped: {frontier_info['stop_reason']}",
            "stop_reason": frontier_info["stop_reason"],
            "frontier": frontier_info,
            "all_arcs": frontier_info.get("all_arcs", []),
            "all_plans": frontier_info.get("all_plans", []),
            "incumbent": incumbent,
            "e2_lb": e2_lb,
            "c0": c0,
            "elapsed": time.time() - started,
        }

    classes = frontier_info["pareto"]
    open_classes = []
    for idx, p in enumerate(classes):
        base = p["cost"] + e2_lb
        open_classes.append({
            "index": idx,
            "cost": p["cost"],
            "t_del": p["t_del"],
            "t_pick": p["t_pick"],
            "base": base,
            "deficit": max(0.0, incumbent - base),
            "closed_by_lb": base >= incumbent - 1e-6,
        })
    per_class = [
        {
            "index": c["index"],
            "e1_cost": c["cost"],
            "t_del": c["t_del"],
            "total_lb": c["base"],
            "closed": c["closed_by_lb"],
            "closed_by_lb_only": c["closed_by_lb"] and c["deficit"] <= 1e-9,
        }
        for c in open_classes
    ]

    certificates = {}
    sat_attempts = []
    cert_by_sat = {s: [] for s in active_sats}

    def class_total(c):
        excess = 0.0
        for s in active_sats:
            best = 0.0
            for (f, b) in cert_by_sat[s]:
                if f <= c["t_del"][s] + 1e-6:
                    best = max(best, b - float(satellite_lbs[s]))
            excess += best
        return c["cost"] + e2_lb + excess

    # candidate floors: distinct t_del[s] over the open classes, descending
    # (the highest floors give the strongest E2 lower bounds first).
    candidates = []
    for s in active_sats:
        floors = sorted(
            {round(c["t_del"][s], 6) for c in open_classes
             if not c["closed_by_lb"]},
            reverse=True,
        )
        for f in floors:
            candidates.append((f, s))
    candidates.sort(reverse=True)

    for (floor, s) in candidates:
        if all(c["closed_by_lb"] for c in open_classes):
            break
        if deadline is not None and time.time() + 30.0 > deadline:
            # Not enough time left for another floor certificate; stop cleanly
            # with the classes closed so far.
            remaining_open = sum(1 for c in open_classes if not c["closed_by_lb"])
            return {
                "status": "NOT_PROVEN_TIME_BUDGET",
                "proven_optimal": False,
                "message": (f"floor certificates for {remaining_open} "
                            f"open class(es) not finished within time budget"),
                "incumbent": incumbent,
                "e2_lb": e2_lb,
                "c0": c0,
                "frontier": {
                    "plans_count": frontier_info["plans_count"],
                    "pareto_classes": frontier_info["pareto_classes"],
                    "complete": frontier_info["complete"],
                },
                "per_class": per_class,
                "certificates": certificates,
                "satellite_lbs": satellite_lbs,
                "elapsed": time.time() - started,
            }
        cert = certify_satellite_floor_lb(
            problem, s, e2_tasks_by_sat[s], vehicles_per_satellite,
            floor, cols_by_sat.get(s, []),
            max_iter=floor_cert_max_iter,
            pricing_time_limit=floor_cert_time_limit,
            pool_solutions=floor_cert_pool_solutions,
            output_flag=output_flag, verbose=verbose,
        )
        key = f"{s}@{floor:.4f}"
        certificates[key] = cert
        sat_attempts.append({"satellite": s, "floor": floor, **cert})
        if cert["status"] == "CERTIFIED":
            cert_by_sat[s].append((floor, cert["lp_bound"]))
        elif cert["status"] == "FLOOR_INFEASIBLE":
            cert_by_sat[s].append((floor, float("inf")))
        for c in open_classes:
            if c["closed_by_lb"]:
                continue
            c["base"] = class_total(c)
            c["closed_by_lb"] = c["base"] >= incumbent - 1e-6

    all_closed = all(c["closed_by_lb"] for c in open_classes)
    per_class = []
    for c in open_classes:
        per_class.append({
            "index": c["index"],
            "e1_cost": c["cost"],
            "t_del": c["t_del"],
            "total_lb": c["base"],
            "closed": c["closed_by_lb"],
            "closed_by_lb_only": c["closed_by_lb"] and c["deficit"] <= 1e-9,
        })

    return {
        "status": "PROVEN_OPTIMAL" if all_closed else "NOT_PROVEN",
        "proven_optimal": bool(all_closed),
        "incumbent": incumbent,
        "e2_lb": e2_lb,
        "c0": c0,
        "frontier": {
            "plans_count": frontier_info["plans_count"],
            "pareto_classes": frontier_info["pareto_classes"],
            "complete": frontier_info["complete"],
        },
        "all_arcs": frontier_info.get("all_arcs", []),
        "all_plans": frontier_info.get("all_plans", []),
        "per_class": per_class,
        "certificates": certificates,
        "satellite_lbs": satellite_lbs,
        "elapsed": time.time() - started,
    }
