# -*- coding: utf-8 -*-
"""
Model 4 的小规模精确 MIP。

与 Model 3 精确模型的差异（也是本模型的核心贡献）：

  - 顾客-前置仓分配 w[c, s] 是决策变量（单分配：配送与回收同一前置仓）；
  - E1 配送/回收节点改为可选访问，重量/体积是 w 的线性表达式；
  - E1 初始载量用 y[c, s, k] 线性化（w ∧ E1车k访问S{s}d）；
  - E1 卫星截止时间写成蕴含式：w[c,s]=1 ⇒ E1 到达 S{s}d 不晚于顾客 c 的配送截止；
  - E2 每个前置仓以"全体顾客任务"为候选节点，覆盖量与 w 链接：
    E2 车在卫星 s 服务顾客 c 的配送任务 <=> w[c,s]=1。

其余机制（E2 每仓车队、路线锚定本仓、先送后取、E1-E2 时间衔接、TFp）与 M3 相同。
仅适合小规模验证（5/10 客户）；大规模请使用 Model 4 ALNS。
"""
import math

import gurobipy as gp
from gurobipy import GRB

from archive.routing_baselines.alns.common.types import is_tfp_active


def _signed_q(task_w, kind):
    return task_w if kind == "d" else -task_w


def _formulation_options(options):
    """Return audited M4 formulation switches with conservative defaults.

    Every switch is an exact reformulation or exact symmetry breaker.  They
    default to ``False`` so historical benchmark fingerprints remain stable.
    """
    defaults = {
        "prune_e2_arcs": False,
        "arc_specific_time_m": False,
        "drop_implied_precedence": False,
        "route_key_symmetry": False,
        "branch_assignment_first": False,
    }
    unknown = set(options or {}) - set(defaults)
    if unknown:
        raise ValueError(f"unknown M4 formulation options: {sorted(unknown)}")
    defaults.update(options or {})
    return defaults


def build_model(problem, TFp=9999.0, vehicles_per_satellite=4,
                formulation_options=None):
    from models.common.operating_policy import policy_for
    policy_for(problem).require_model(4)
    options = _formulation_options(formulation_options)
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

    # ---------------- 节点与任务 ----------------
    e1_nodes = [depot_id]
    e1_del_nodes = []
    e1_pick_nodes = []
    for s in F:
        e1_del_nodes.append(f"{s}d")
        e1_pick_nodes.append(f"{s}p")
    e1_nodes += e1_del_nodes + e1_pick_nodes

    e2_task_ids = []
    for c in C:
        cnode = problem.nodes.get(c)
        if cnode and cnode.demand > 1e-9:
            e2_task_ids.append(f"{c}d")
        e2_task_ids.append(f"{c}p")

    e1_svc = {depot_id: 0.0}
    for s in F:
        e1_svc[f"{s}d"] = getattr(problem.nodes[s], "service_time", 0.0)
        e1_svc[f"{s}p"] = getattr(problem.nodes[s], "service_time", 0.0)
    e2_svc = {t: cust_svc[t[:-1]] for t in e2_task_ids}

    # E1 任务时间窗：配送 (0, +inf) 由蕴含式收紧；回收 (TFp语义, +inf)
    e1_tw = {}
    for s in F:
        e1_tw[f"{s}d"] = (0.0, inf)
        # E1 回收下界由 Link_Pickup（≥ 该仓 E2 返回时刻）动态施加，
        # 不再使用静态大数占位。
        e1_tw[f"{s}p"] = (0.0, inf)

    e2_tw = {}
    for c in C:
        e2_tw[f"{c}d"] = cust_tw_del[c]
        e2_tw[f"{c}p"] = cust_tw_pick[c]

    # ---------------- 距离/时间与安全时间上界 ----------------
    dist_map = {}
    time_map = {}
    all_loc = set([depot_id] + F + e1_nodes[1:] + e2_task_ids)

    def phys(node_id):
        if node_id in F or node_id == depot_id:
            return node_id
        if node_id.endswith(("d", "p")):
            base = node_id[:-1]
            if base in F or base in set(C):
                return base
        return node_id

    for i in all_loc:
        phys_i = phys(i)
        for j in all_loc:
            phys_j = phys(j)
            dist_map[i, j] = problem.distance(phys_i, phys_j)
            time_map[i, j] = problem.travel_time(phys_i, phys_j)

    finite_times = [0.0]
    for t_id in e2_task_ids:
        e, l = e2_tw[t_id]
        finite_times.append(e)
        if l < inf:
            finite_times.append(l)
    if is_tfp_active(TFp):
        finite_times.append(TFp)
    max_arc = max(time_map.values(), default=0.0)
    total_svc = sum(e1_svc.values()) + sum(e2_svc.values())
    task_count = len(e1_nodes) - 1 + len(F) * len(e2_task_ids)
    time_horizon = (
        max(finite_times)
        + (task_count + 2) * max_arc
        + total_svc
        + 1.0
    )
    M_time = time_horizon + max_arc

    # Exact E2 arc preprocessing.  A_start is variable, but every relative
    # E2 arrival is non-negative.  Hence an absolute task-window lower bound
    # is also a valid (possibly weak) lower bound on a source's absolute
    # service time.  If even that earliest source service cannot reach the
    # destination before its latest time, the arc is physically impossible.
    e2_infeasible = set()
    e2_arc_candidates = 0
    if options["prune_e2_arcs"]:
        for s in F:
            valid = [s] + e2_task_ids
            for i in valid:
                earliest_i = 0.0 if i == s else e2_tw[i][0]
                service_i = e2_svc.get(i, 0.0)
                for j in valid:
                    if i == j:
                        continue
                    e2_arc_candidates += 1
                    if j == s:
                        continue  # satellite return has no finite deadline
                    latest_j = e2_tw[j][1]
                    if (latest_j < inf
                            and earliest_i + service_i + time_map[i, j]
                            > latest_j + 1e-9):
                        e2_infeasible.add((s, i, j))
    else:
        e2_arc_candidates = sum(
            (len([s] + e2_task_ids) - 1) * len([s] + e2_task_ids)
            for s in F
        )

    # The relative E2 time at a task cannot exceed its absolute latest time
    # because every route starts at a non-negative absolute time.  These
    # bounds yield a smallest safe inactive-arc M against A_j >= 0.
    e2_time_ub = {s: 0.0 for s in F}
    for task_id in e2_task_ids:
        latest = e2_tw[task_id][1]
        e2_time_ub[task_id] = (
            min(time_horizon, latest) if latest < inf else time_horizon
        )

    e2_arc_m = {}
    if options["arc_specific_time_m"]:
        for s in F:
            for i in [s] + e2_task_ids:
                for j in e2_task_ids:
                    if i == j or (s, i, j) in e2_infeasible:
                        continue
                    e2_arc_m[s, i, j] = max(
                        0.0,
                        e2_time_ub[i] + e2_svc.get(i, 0.0)
                        + time_map[i, j],
                    )

    total_del_w = sum(cust_w.values())
    total_pick_w = len(C) * cw
    total_del_v = len(C) * cv
    total_pick_v = len(C) * cv

    m = gp.Model("2E_VRPTW_PDD_M4_Exact")

    # ---------------- 分配变量 ----------------
    w = m.addVars(C, F, vtype=GRB.BINARY, name="w")
    for c in C:
        m.addConstr(gp.quicksum(w[c, s] for s in F) == 1, f"Assign_{c}")

    # ---------------- E1 变量 ----------------
    x = m.addVars(e1_nodes, e1_nodes, K, vtype=GRB.BINARY, name="x")
    A_e1 = m.addVars(e1_nodes, K, vtype=GRB.CONTINUOUS, lb=0.0,
                     ub=time_horizon, name="A_e1")
    Q_k = m.addVars(e1_nodes, K, vtype=GRB.CONTINUOUS, lb=0.0,
                    ub=problem.e1_weight_cap, name="Q_k")
    Vol_k = m.addVars(e1_nodes, K, vtype=GRB.CONTINUOUS, lb=0.0,
                      ub=problem.e1_volume_cap, name="Vol_k")

    # E1 访问（可选）：每个卫星配送/回收节点至多访问一次
    for j in e1_del_nodes + e1_pick_nodes:
        m.addConstr(
            gp.quicksum(x[i, j, k] for i in e1_nodes for k in K if i != j)
            <= 1,
            f"E1_Visit_{j}")

    for k in K:
        m.addConstr(
            gp.quicksum(x[depot_id, j, k] for j in e1_del_nodes + e1_pick_nodes)
            <= 1,
            f"E1_Start_{k}")
        m.addConstr(
            gp.quicksum(x[i, depot_id, k]
                        for i in e1_del_nodes + e1_pick_nodes)
            <= 1,
            f"E1_End_{k}")
        for h in e1_del_nodes + e1_pick_nodes:
            m.addConstr(
                gp.quicksum(x[i, h, k] for i in e1_nodes if i != h)
                - gp.quicksum(x[h, j, k] for j in e1_nodes if j != h) == 0,
                f"E1_Flow_{h}_{k}")
        depot_out = gp.quicksum(
            x[depot_id, jj, k] for jj in e1_del_nodes + e1_pick_nodes)
        for j in e1_del_nodes + e1_pick_nodes:
            m.addConstr(
                gp.quicksum(x[i, j, k] for i in e1_nodes if i != j)
                <= depot_out,
                f"E1_Subtour_{j}_{k}")
        m.addConstr(A_e1[depot_id, k] == 0, f"E1_DepotTime_{k}")
        for i in e1_nodes:
            m.addConstr(x[i, i, k] == 0, f"E1_NoSelf_{i}_{k}")
    for idx in range(len(K) - 1):
        m.addConstr(
            gp.quicksum(x[depot_id, j, K[idx]]
                        for j in e1_del_nodes + e1_pick_nodes)
            >= gp.quicksum(x[depot_id, j, K[idx + 1]]
                           for j in e1_del_nodes + e1_pick_nodes),
            f"E1_VehicleSym_{idx}")

    # 禁止同一前置仓 回收->配送
    for s in F:
        pid, did = f"{s}p", f"{s}d"
        for k in K:
            m.addConstr(x[pid, did, k] == 0, f"E1_ForbidArc_{s}_{k}")

    # 有顾客分配的前置仓必须被 E1 配送/回收访问恰好一次
    served_s = m.addVars(F, vtype=GRB.BINARY, name="served_s")
    for s in F:
        for c in C:
            m.addConstr(served_s[s] >= w[c, s], f"Served_ge_{c}_{s}")
        m.addConstr(
            served_s[s] <= gp.quicksum(w[c, s] for c in C),
            f"Served_le_{s}")
        m.addConstr(
            gp.quicksum(x[i, f"{s}d", k]
                        for k in K for i in e1_nodes if i != f"{s}d")
            == served_s[s],
            f"E1_DelVisit_{s}")
        m.addConstr(
            gp.quicksum(x[i, f"{s}p", k]
                        for k in K for i in e1_nodes if i != f"{s}p")
            == served_s[s],
            f"E1_PickVisit_{s}")

    # ---------------- E1 初始载量线性化 ----------------
    y = m.addVars(C, F, K, vtype=GRB.BINARY, name="y")
    visits_d = {}
    for s in F:
        for k in K:
            visits_d[s, k] = gp.quicksum(
                x[i, f"{s}d", k] for i in e1_nodes if i != f"{s}d")
    for c in C:
        for s in F:
            for k in K:
                m.addConstr(y[c, s, k] <= w[c, s], f"Y_ub1_{c}_{s}_{k}")
                m.addConstr(y[c, s, k] <= visits_d[s, k], f"Y_ub2_{c}_{s}_{k}")
                m.addConstr(
                    y[c, s, k] >= w[c, s] + visits_d[s, k] - 1,
                    f"Y_lb_{c}_{s}_{k}")
    for k in K:
        m.addConstr(
            Q_k[depot_id, k]
            == gp.quicksum(cust_w[c] * y[c, s, k] for c in C for s in F),
            f"E1_InitW_{k}")
        m.addConstr(
            Vol_k[depot_id, k]
            == gp.quicksum(cv * y[c, s, k] for c in C for s in F),
            f"E1_InitV_{k}")
    m.addConstr(
        gp.quicksum(Q_k[depot_id, k] for k in K) == total_del_w,
        "E1_TotalWeight")
    m.addConstr(
        gp.quicksum(Vol_k[depot_id, k] for k in K) == total_del_v,
        "E1_TotalVol")
    for idx in range(len(K) - 1):
        m.addConstr(
            Q_k[depot_id, K[idx]] >= Q_k[depot_id, K[idx + 1]],
            f"E1_LoadSym_{idx}")

    def e1_q_expr(node_id):
        if node_id.endswith("d") and node_id[:-1] in F:
            s = node_id[:-1]
            return gp.quicksum(cust_w[c] * w[c, s] for c in C)
        if node_id.endswith("p") and node_id[:-1] in F:
            s = node_id[:-1]
            return -gp.quicksum(cw * w[c, s] for c in C)
        return None

    # E1 载重/体积 MTZ（任务重量为 w 的线性表达式）
    for i in e1_nodes:
        for j in e1_del_nodes + e1_pick_nodes:
            if i == j:
                continue
            q_j = e1_q_expr(j)
            v_j = None
            if j.endswith("d"):
                v_j = gp.quicksum(cv * w[c, j[:-1]] for c in C)
            elif j.endswith("p"):
                v_j = -gp.quicksum(cv * w[c, j[:-1]] for c in C)
            M_w = problem.e1_weight_cap + max(
                total_del_w if j.endswith("d") else total_pick_w, 1.0)
            M_v = problem.e1_volume_cap + max(
                total_del_v if j.endswith("d") else total_pick_v, 1.0)
            for k in K:
                m.addConstr(
                    Q_k[j, k] <= Q_k[i, k] - q_j
                    + M_w * (1 - x[i, j, k]),
                    f"E1_W_up_{i}_{j}_{k}")
                m.addConstr(
                    Q_k[j, k] >= Q_k[i, k] - q_j
                    - M_w * (1 - x[i, j, k]),
                    f"E1_W_lo_{i}_{j}_{k}")
                m.addConstr(
                    Vol_k[j, k] <= Vol_k[i, k] - v_j
                    + M_v * (1 - x[i, j, k]),
                    f"E1_V_up_{i}_{j}_{k}")
                m.addConstr(
                    Vol_k[j, k] >= Vol_k[i, k] - v_j
                    - M_v * (1 - x[i, j, k]),
                    f"E1_V_lo_{i}_{j}_{k}")

    # E1 时间 MTZ
    for i in e1_nodes:
        for j in e1_del_nodes + e1_pick_nodes:
            if (i == j
                    or (j.endswith("d") and i == f"{j[:-1]}p")):
                continue
            for k in K:
                m.addConstr(
                    A_e1[j, k] >= (A_e1[i, k] + e1_svc.get(i, 0.0)
                                   + time_map[i, j])
                    - M_time * (1 - x[i, j, k]),
                    f"E1_Time_{i}_{j}_{k}")

    # E1 配送截止：w[c,s]=1 ⇒ 到达 S{s}d 不晚于顾客 c 的配送截止
    # Uses M1/M3 implication form: A_e1[s_d, k] <= l_d + M*(2 - visits - w)
    for s in F:
        for c in C:
            l_d = cust_tw_del[c][1]
            if l_d >= inf:
                continue
            for k in K:
                m.addConstr(
                    A_e1[f"{s}d", k]
                    <= l_d + M_time * (2 - visits_d[s, k] - w[c, s]),
                    f"E1_Due_{c}_{s}_{k}")

    if is_tfp_active(TFp):
        for s in F:
            for k in K:
                pick_visits = gp.quicksum(
                    x[i, f"{s}p", k] for i in e1_nodes if i != f"{s}p")
                m.addConstr(
                    A_e1[f"{s}p", k] >= TFp - M_time * (1 - pick_visits),
                    f"E1_TFp_{s}_{k}")

    # ---------------- E2 变量 ----------------
    z = {}
    Q_r = {}
    Vol_r = {}
    A_e2 = {}
    V_by_sat = {}
    for s in F:
        vehs = [(s, v) for v in range(vehicles_per_satellite)]
        V_by_sat[s] = vehs
        valid = [s] + e2_task_ids
        for v in vehs:
            for i in valid:
                # This is the vehicle's total load after serving ``i`` (or
                # its departure load when ``i`` is the satellite).  Pickups
                # increase the state, so capacity applies at every node.
                Q_r[i, v] = m.addVar(lb=0.0,
                                     ub=problem.e2_weight_cap,
                                     name=f"Q_r_{i}_{v}")
                Vol_r[i, v] = m.addVar(lb=0.0,
                                       ub=problem.e2_volume_cap,
                                       name=f"Vol_r_{i}_{v}")
                arrival_ub = (e2_time_ub[i]
                              if options["arc_specific_time_m"]
                              else time_horizon)
                A_e2[i, v] = m.addVar(lb=0.0, ub=arrival_ub,
                                      name=f"A_e2_{i}_{v}")
                for j in valid:
                    if i != j and (s, i, j) not in e2_infeasible:
                        z[i, j, v] = m.addVar(vtype=GRB.BINARY,
                                              name=f"z_{i}_{j}_{v}")

    # E2 覆盖量与分配链接
    for s in F:
        for c in C:
            d_id, p_id = f"{c}d", f"{c}p"
            cov_p = gp.quicksum(
                z[i, p_id, v]
                for v in V_by_sat[s] for i in [s] + e2_task_ids
                if i != p_id and (i, p_id, v) in z)
            if d_id in e2_task_ids:
                cov_d = gp.quicksum(
                    z[i, d_id, v]
                    for v in V_by_sat[s] for i in [s] + e2_task_ids
                    if i != d_id and (i, d_id, v) in z)
                m.addConstr(cov_d == w[c, s], f"E2_CovD_{c}_{s}")
            m.addConstr(cov_p == w[c, s], f"E2_CovP_{c}_{s}")

    for s in F:
        vehs = V_by_sat[s]
        valid = [s] + e2_task_ids
        for v in vehs:
            m.addConstr(
                gp.quicksum(z[s, j, v] for j in e2_task_ids
                            if (s, j, v) in z)
                == gp.quicksum(z[i, s, v] for i in e2_task_ids
                               if (i, s, v) in z),
                f"E2_DepotBal_{s}_{v}")
            m.addConstr(
                gp.quicksum(z[s, j, v] for j in e2_task_ids
                            if (s, j, v) in z) <= 1,
                f"E2_Start_{s}_{v}")
            for h in e2_task_ids:
                m.addConstr(
                    gp.quicksum(z[i, h, v] for i in valid
                                if i != h and (i, h, v) in z)
                    - gp.quicksum(z[h, j, v] for j in valid
                                  if j != h and (h, j, v) in z) == 0,
                    f"E2_Flow_{h}_{v}")
            m.addConstr(A_e2[s, v] == 0, f"E2_SatAnchor_{s}_{v}")
            # 同一顾客先送后取（同一辆车内 p 不能在 d 前）
            for c in C:
                d_id, p_id = f"{c}d", f"{c}p"
                if (p_id, d_id, v) in z:
                    m.addConstr(z[p_id, d_id, v] == 0,
                                f"E2_ForbidPickBeforeDel_{c}_{v}")
        # E2 vehicles anchored at one satellite are interchangeable.  Use
        # their labels in order, which removes only symmetric copies.
        for idx in range(len(vehs) - 1):
            m.addConstr(
                gp.quicksum(z[s, j, vehs[idx]] for j in e2_task_ids
                            if (s, j, vehs[idx]) in z)
                >= gp.quicksum(z[s, j, vehs[idx + 1]] for j in e2_task_ids
                               if (s, j, vehs[idx + 1]) in z),
                f"E2_VehicleSym_{s}_{idx}")

    # E2 时间 MTZ
    for s in F:
        valid = [s] + e2_task_ids
        for v in V_by_sat[s]:
            for i in valid:
                for j in e2_task_ids:
                    if i == j or (i, j, v) not in z:
                        continue
                    arc_m = e2_arc_m.get((s, i, j), M_time)
                    m.addConstr(
                        A_e2[j, v] >= (A_e2[i, v] + e2_svc.get(i, 0.0)
                                       + time_map[i, j])
                        - arc_m * (1 - z[i, j, v]),
                        f"E2_Time_{i}_{j}_{v}")

    # E2 initial load: the vehicle must leave its satellite carrying every
    # delivery that it will serve.  A capacity upper bound alone would let an
    # earlier pickup incorrectly fund a later delivery.
    for s in F:
        vehs = V_by_sat[s]
        for v in vehs:
            m.addConstr(
                Q_r[s, v]
                == gp.quicksum(
                    cust_w[c] * gp.quicksum(
                        z[i, f"{c}d", v]
                        for i in [s] + e2_task_ids
                        if i != f"{c}d" and (i, f"{c}d", v) in z)
                    for c in C if f"{c}d" in e2_task_ids
                ),
                f"E2_InitW_{s}_{v}")
            m.addConstr(
                Vol_r[s, v]
                == gp.quicksum(
                    cv * gp.quicksum(
                        z[i, f"{c}d", v]
                        for i in [s] + e2_task_ids
                        if i != f"{c}d" and (i, f"{c}d", v) in z)
                    for c in C if f"{c}d" in e2_task_ids
                ),
                f"E2_InitV_{s}_{v}")
        # Among otherwise interchangeable vehicles, label routes by their
        # (now exact) departure delivery load.  This is compatible with the
        # used-vehicle ordering above and retains one representative of every
        # vehicle-permutation orbit.
        task_rank = {task: rank + 1
                     for rank, task in enumerate(sorted(e2_task_ids))}
        for idx in range(len(vehs) - 1):
            if options["route_key_symmetry"]:
                # A scalar route key can always be sorted because vehicles
                # anchored at one satellite are interchangeable.  Unlike
                # imposing load order and first-task order separately, this
                # single ordering cannot exclude a set of feasible routes.
                left, right = vehs[idx], vehs[idx + 1]
                first_left = gp.quicksum(
                    task_rank[j] * z[s, j, left]
                    for j in e2_task_ids if (s, j, left) in z)
                first_right = gp.quicksum(
                    task_rank[j] * z[s, j, right]
                    for j in e2_task_ids if (s, j, right) in z)
                key_scale = len(e2_task_ids) + 1.0
                m.addConstr(
                    key_scale * Q_r[s, left] + first_left
                    >= key_scale * Q_r[s, right] + first_right,
                    f"E2_RouteKeySym_{s}_{idx}")
            else:
                m.addConstr(
                    Q_r[s, vehs[idx]] >= Q_r[s, vehs[idx + 1]],
                    f"E2_LoadSym_{s}_{idx}")

    # E2 载重/体积 MTZ（任务重量为常量）
    for s in F:
        valid = [s] + e2_task_ids
        for v in V_by_sat[s]:
            for i in valid:
                for j in e2_task_ids:
                    if i == j or (i, j, v) not in z:
                        continue
                    kind = j[-1]
                    w_j = cw if kind == "p" else cust_w[j[:-1]]
                    v_j = cv
                    q_j = _signed_q(w_j, kind)
                    vv_j = _signed_q(v_j, kind)
                    # With explicit [0, capacity] bounds on every load
                    # state, these values deactivate either propagation row
                    # exactly when the arc is not selected.
                    M_w = problem.e2_weight_cap + abs(q_j)
                    M_v = problem.e2_volume_cap + abs(vv_j)
                    m.addConstr(
                        Q_r[j, v] <= Q_r[i, v] - q_j
                        + M_w * (1 - z[i, j, v]),
                        f"E2_W_up_{i}_{j}_{v}")
                    m.addConstr(
                        Q_r[j, v] >= Q_r[i, v] - q_j
                        - M_w * (1 - z[i, j, v]),
                        f"E2_W_lo_{i}_{j}_{v}")
                    m.addConstr(
                        Vol_r[j, v] <= Vol_r[i, v] - vv_j
                        + M_v * (1 - z[i, j, v]),
                        f"E2_V_up_{i}_{j}_{v}")
                    m.addConstr(
                        Vol_r[j, v] >= Vol_r[i, v] - vv_j
                        - M_v * (1 - z[i, j, v]),
                        f"E2_V_lo_{i}_{j}_{v}")

    # ---------------- E1-E2 衔接 ----------------
    A_start = m.addVars(F, range(vehicles_per_satellite),
                        vtype=GRB.CONTINUOUS, lb=0.0, ub=time_horizon,
                        name="A_start")
    A_return = m.addVars(F, range(vehicles_per_satellite),
                         vtype=GRB.CONTINUOUS, lb=0.0, ub=time_horizon,
                         name="A_return")
    for s in F:
        for v_idx in range(vehicles_per_satellite):
            v = (s, v_idx)
            e2_used = gp.quicksum(
                z[s, j, v] for j in e2_task_ids if (s, j, v) in z)
            # No Link_StartUsed/Link_ReturnUsed: M1/M3 does not constrain
            # A_start/A_return for unused vehicles.
            for k in K:
                del_visits = gp.quicksum(
                    x[i, f"{s}d", k]
                    for i in e1_nodes if i != f"{s}d")
                m.addConstr(
                    A_start[s, v_idx]
                    >= A_e1[f"{s}d", k] + e1_svc[f"{s}d"]
                    - M_time * (2 - del_visits - e2_used),
                    f"Link_Start_{s}_{v_idx}_{k}")

            for j_task in e2_task_ids:
                e_j, l_j = (cust_tw_del[j_task[:-1]] if j_task.endswith("d")
                            else cust_tw_pick[j_task[:-1]])
                is_served = gp.quicksum(
                    z[i, j_task, v]
                    for i in [s] + e2_task_ids
                    if i != j_task and (i, j_task, v) in z)
                abs_arrival = A_start[s, v_idx] + A_e2[j_task, v]
                m.addConstr(
                    abs_arrival >= e_j - M_time * (1 - is_served),
                    f"Link_TW_lo_{s}_{v_idx}_{j_task}")
                if l_j < inf:
                    m.addConstr(
                        abs_arrival <= l_j + M_time * (1 - is_served),
                        f"Link_TW_hi_{s}_{v_idx}_{j_task}")

            for c in C:
                d_id, p_id = f"{c}d", f"{c}p"
                if d_id not in e2_task_ids:
                    continue
                precedence_implied = (
                    options["drop_implied_precedence"]
                    and e2_tw[p_id][0]
                    >= e2_tw[d_id][1] + cust_svc[c] - 1e-9
                )
                if precedence_implied:
                    continue
                served_d = gp.quicksum(
                    z[i, d_id, v]
                    for i in [s] + e2_task_ids
                    if i != d_id and (i, d_id, v) in z)
                abs_d = A_start[s, v_idx] + A_e2[d_id, v]
                for p_idx in range(vehicles_per_satellite):
                    vp = (s, p_idx)
                    served_p = gp.quicksum(
                        z[i, p_id, vp]
                        for i in [s] + e2_task_ids
                        if i != p_id and (i, p_id, vp) in z)
                    abs_p = A_start[s, p_idx] + A_e2[p_id, vp]
                    m.addConstr(
                        abs_p >= abs_d + cust_svc[c]
                        - M_time * (2 - served_d - served_p),
                        f"Link_CustPrec_{s}_{c}_{v_idx}_{p_idx}")

            for i in e2_task_ids:
                if (i, s, v) not in z:
                    continue
                m.addConstr(
                    A_return[s, v_idx]
                    >= A_start[s, v_idx] + A_e2[i, v]
                    + e2_svc.get(i, 0.0) + time_map[i, s]
                    - M_time * (1 - z[i, s, v]),
                    f"Link_Ret_lo_{s}_{v_idx}_{i}")
                m.addConstr(
                    A_return[s, v_idx]
                    <= A_start[s, v_idx] + A_e2[i, v]
                    + e2_svc.get(i, 0.0) + time_map[i, s]
                    + M_time * (1 - z[i, s, v]),
                    f"Link_Ret_hi_{s}_{v_idx}_{i}")

            for k in K:
                pick_visits = gp.quicksum(
                    x[i, f"{s}p", k]
                    for i in e1_nodes if i != f"{s}p")
                m.addConstr(
                    A_e1[f"{s}p", k]
                    >= A_return[s, v_idx]
                    - M_time * (1 - pick_visits),
                    f"Link_Pickup_{s}_{v_idx}_{k}")

    # ---------------- 目标 ----------------
    obj = (
        gp.quicksum(
            dist_map[i, j] * x[i, j, k]
            for k in K for i in e1_nodes for j in e1_nodes if i != j)
        + gp.quicksum(
            dist_map[i, j] * z[i, j, v]
            for (i, j, v) in z)
    )
    m.setObjective(obj, GRB.MINIMIZE)

    ctx = {
        "depot_id": depot_id,
        "F": F,
        "C": C,
        "K": K,
        "e1_nodes": e1_nodes,
        "e1_del_nodes": e1_del_nodes,
        "e1_pick_nodes": e1_pick_nodes,
        "e2_task_ids": e2_task_ids,
        "V_by_sat": V_by_sat,
        "time_horizon": time_horizon,
        "M_time": M_time,
        "formulation_options": options,
        "formulation_stats": {
            "e2_arc_candidates_per_vehicle_pool": e2_arc_candidates,
            "e2_arcs_pruned_per_vehicle_pool": len(e2_infeasible),
            "e2_arc_binaries_created": len(z),
            "precedence_rows_skipped": sum(
                len(V_by_sat[s]) ** 2
                for s in F for c in C
                if f"{c}d" in e2_task_ids
                and options["drop_implied_precedence"]
                and e2_tw[f"{c}p"][0]
                >= e2_tw[f"{c}d"][1] + cust_svc[c] - 1e-9
            ),
        },
    }
    return m, {"w": w, "served_s": served_s, "y": y, "x": x, "z": z,
               "A_e1": A_e1, "Q_k": Q_k,
               "Vol_k": Vol_k, "A_e2": A_e2, "Q_r": Q_r, "Vol_r": Vol_r,
               "A_start": A_start, "A_return": A_return}, ctx


def _apply_m4_warm_start(m, vars_dict, ctx, problem, e1_routes, e2_routes_by_sat, verbose=False):
    """Warm-start M4 from M3's solution (or any feasible route set).
    
    M3's fixed assignment is a feasible solution for M4 (free assignment is a 
    superset). We set w[c,s] based on which satellite's E2 routes serve customer c,
    and set x/z variables for the arcs in the routes.
    """
    import math
    
    w = vars_dict["w"]
    x = vars_dict["x"]
    z = vars_dict["z"]
    F = ctx["F"]
    C = ctx["C"]
    K = ctx["K"]
    depot_id = ctx["depot_id"]
    e1_nodes = ctx["e1_nodes"]
    e1_del_nodes = ctx["e1_del_nodes"]
    e1_pick_nodes = ctx["e1_pick_nodes"]
    e2_task_ids = ctx["e2_task_ids"]
    V_by_sat = ctx["V_by_sat"]
    M_time = ctx["M_time"]
    time_horizon = ctx["time_horizon"]
    
    # Give Gurobi a *complete discrete* start.  The previous implementation
    # only marked selected x/z arcs and left all other binaries (including the
    # E1 load linearisation y) undefined.  Gurobi then had to solve a repair
    # sub-MIP before it could use an ALNS incumbent.  Continuous starts remain
    # optional: once every binary is fixed, Gurobi can recover them with an LP.
    for vd in [w, vars_dict["served_s"], vars_dict["y"], x, z]:
        for var in vd.values():
            var.Start = 0.0
    for vd in [vars_dict["A_e1"], vars_dict["Q_k"], vars_dict["Vol_k"],
               vars_dict["A_e2"], vars_dict["Q_r"], vars_dict["Vol_r"],
               vars_dict["A_start"], vars_dict["A_return"]]:
        for var in vd.values():
            var.Start = GRB.UNDEFINED
    
    # Determine customer assignment from E2 routes
    cust_sat = {}  # customer_id -> satellite_id
    for sat_id, routes in (e2_routes_by_sat or {}).items():
        for route in (routes or []):
            for node_id in route:
                if node_id in e2_task_ids:
                    cust_id = node_id[:-1]  # strip d/p suffix
                    if cust_id in C:
                        cust_sat[cust_id] = sat_id
    
    # Set w[c,s] and served_s starts.
    served_s = vars_dict["served_s"]
    for c in C:
        assigned_sat = cust_sat.get(c)
        if assigned_sat is None:
            # Try to get from problem's pre-assignment
            cnode = problem.nodes.get(c)
            if cnode and cnode.assigned_sat:
                assigned_sat = cnode.assigned_sat
        if assigned_sat and assigned_sat in F:
            cust_sat[c] = assigned_sat
            w[c, assigned_sat].Start = 1.0
            served_s[assigned_sat].Start = 1.0

    # E1 vehicle labels are symmetric and the exact model orders their depot
    # delivery loads.  Re-label warm-start routes before assigning x/y starts
    # so an otherwise feasible ALNS solution is not rejected by E1_LoadSym.
    def _e1_delivery_weight(route):
        total = 0.0
        for node_id in route or []:
            if not node_id.endswith("d"):
                continue
            sat_id = node_id[:-1]
            if sat_id not in F:
                continue
            total += sum(
                problem.nodes[c].weight_del + problem.fleet.container_weight
                for c, assigned in cust_sat.items() if assigned == sat_id
            )
        return total

    ordered_e1_routes = sorted(
        (route for route in (e1_routes or []) if route),
        key=_e1_delivery_weight,
        reverse=True,
    )
    
    # Set E1 x variable starts
    for k_idx, route in enumerate(ordered_e1_routes):
        if k_idx >= len(K):
            break
        k = K[k_idx]
        if not route or len(route) < 2:
            continue
        for i, j in zip(route, route[1:]):
            if (i, j, k) in x:
                x[i, j, k].Start = 1.0

    # y[c,s,k] = w[c,s] AND truck k visits S{s}d.
    y = vars_dict["y"]
    for k_idx, route in enumerate(ordered_e1_routes):
        if k_idx >= len(K):
            break
        k = K[k_idx]
        delivered_sats = {
            node_id[:-1] for node_id in route
            if node_id.endswith("d") and node_id[:-1] in F
        }
        for c, sat_id in cust_sat.items():
            if sat_id in delivered_sats and (c, sat_id, k) in y:
                y[c, sat_id, k].Start = 1.0
    
    # Set E2 z variable starts
    for sat_id, routes in (e2_routes_by_sat or {}).items():
        if sat_id not in V_by_sat:
            continue
        vehs = V_by_sat[sat_id]
        def _e2_delivery_weight(route):
            return sum(
                problem.nodes[node_id[:-1]].weight_del
                + problem.fleet.container_weight
                for node_id in route or []
                if node_id in e2_task_ids and node_id.endswith("d")
            )

        if ctx["formulation_options"]["route_key_symmetry"]:
            task_rank = {task: rank + 1
                         for rank, task in enumerate(sorted(e2_task_ids))}

            def _e2_route_key(route):
                first_task = next(
                    (node for node in route or [] if node in task_rank), None)
                first_rank = task_rank.get(first_task, 0)
                return ((len(e2_task_ids) + 1.0)
                        * _e2_delivery_weight(route) + first_rank)

            route_sort_key = _e2_route_key
        else:
            route_sort_key = _e2_delivery_weight
        ordered_routes = sorted(
            (route for route in (routes or []) if route),
            key=route_sort_key,
            reverse=True,
        )
        for v_idx, route in enumerate(ordered_routes):
            if v_idx >= len(vehs):
                break
            v = vehs[v_idx]
            if not route or len(route) < 2:
                continue
            for i, j in zip(route, route[1:]):
                if (i, j, v) in z:
                    z[i, j, v].Start = 1.0
