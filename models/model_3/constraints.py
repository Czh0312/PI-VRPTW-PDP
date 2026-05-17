# models/model_3/constraints.py
# -*- coding: utf-8 -*-
"""
对齐文档3 (2E-VRP-PDP-TR) — 统一载重/体积变量
Q/V 送减取增, 共享容量由 ub 自然保证
中转 Δq/Δw 作用于统一 Q/V
"""
import gurobipy as gp


def _signed_q(task):
    """配送为正, 回收为负"""
    return task.w if task.kind == 'd' else -task.w


def _signed_v(task):
    """配送为正, 回收为负"""
    return task.v if task.kind == 'd' else -task.v


def add_all_constraints(m: gp.Model, problem, e1_tasks, e2_tasks_by_sat,
                        vars_dict: dict, ctx: dict):
    # ---------- 解包变量 ----------
    x = vars_dict['x']
    A_e1 = vars_dict['A_e1']
    Q_k = vars_dict['Q_k']
    Vol_k = vars_dict['Vol_k']

    z = vars_dict['z']
    A_e2 = vars_dict['A_e2']
    Q_r = vars_dict['Q_r']
    Vol_r = vars_dict['Vol_r']

    A_start = vars_dict['A_start']
    A_return = vars_dict['A_return']

    # ---------- 解包上下文 ----------
    depot_id = ctx['depot_id']
    F = ctx['F']
    FD_nodes = ctx['FD_nodes']
    FP_nodes = ctx['FP_nodes']
    N1E = ctx['N1E']
    C1 = ctx['C1']
    C2 = ctx['C2']
    CD_nodes = ctx['CD_nodes']
    CP_nodes = ctx['CP_nodes']
    T_nodes = ctx['T']
    K = ctx['K']
    V_by_sat = ctx['V_by_sat']
    vehicles_per_satellite = ctx['vehicles_per_satellite']
    e1_task_map = ctx['e1_task_map']
    e2_task_map = ctx['e2_task_map']
    e2_task_to_sat_map = ctx['e2_task_to_sat_map']
    P1_map = ctx['P1_map']
    P1_inv_map = ctx['P1_inv_map']
    time_map = ctx['time_map']
    e1_service_time_map = ctx['e1_service_time_map']
    e2_service_time_map = ctx['e2_service_time_map']

    M_time = ctx['M_time']
    M_weight_e1 = ctx['M_weight_e1']
    M_weight_e2 = ctx['M_weight_e2']
    M_vol_e1 = ctx['M_vol_e1']
    M_vol_e2 = ctx['M_vol_e2']

    # ===================================================================
    #  第一阶段约束 (文档3 §4)
    # ===================================================================

    # (2) 每个 C1 任务恰好被服务一次
    for j in C1:
        m.addConstr(
            gp.quicksum(x[i, j, k] for i in N1E for k in K if i != j) == 1,
            f"E1_Visit_{j}")

    # (3) 每辆卡车最多从中心仓库出发一次
    for k in K:
        m.addConstr(
            gp.quicksum(x[depot_id, j, k] for j in C1) <= 1,
            f"E1_Start_{k}")

    # (4) 流量守恒
    for k in K:
        for h in C1:
            m.addConstr(
                gp.quicksum(x[i, h, k] for i in N1E if i != h)
                - gp.quicksum(x[h, j, k] for j in N1E if j != h) == 0,
                f"E1_Flow_{h}_{k}")

    # 子环消除
    for k in K:
        depot_out = gp.quicksum(x[depot_id, jj, k] for jj in C1)
        for j in C1:
            m.addConstr(
                gp.quicksum(x[i, j, k] for i in N1E if i != j) <= depot_out,
                f"E1_SubtourElim_{j}_{k}")

    for k in K:
        m.addConstr(
            gp.quicksum(x[i, depot_id, k] for i in C1) <= 1,
            f"E1_End_{k}")

    m.addConstr(
        gp.quicksum(x[depot_id, j, k] for k in K for j in C1)
        == gp.quicksum(x[i, depot_id, k] for k in K for i in C1),
        "E1_DepotBalance")

    # Depot 时间锚定
    for k in K:
        m.addConstr(A_e1[depot_id, k] == 0, f"E1_DepotTime_{k}")

    # 禁止同卫星 p→d
    forbidden_e1 = set()
    for s_id in F:
        pid, did = f"{s_id}p", f"{s_id}d"
        if pid in e1_task_map and did in e1_task_map:
            for k in K:
                m.addConstr(x[pid, did, k] == 0, f"E1_ForbidArc_{s_id}_{k}")
            forbidden_e1.add((pid, did))

    # (5) 时间连续性 MTZ
    for i in N1E:
        s_i = e1_service_time_map.get(i, 0.0)
        for j in C1:
            if i == j or (i, j) in forbidden_e1:
                continue
            for k in K:
                m.addConstr(
                    A_e1[j, k] >= (A_e1[i, k] + s_i + time_map[i, j])
                    - M_time * (1 - x[i, j, k]),
                    f"E1_Time_{i}_{j}_{k}")

    # (6) 任务硬时间窗
    for j in C1:
        e_j, l_j = e1_task_map[j].tw
        for k in K:
            visits_jk = gp.quicksum(x[i, j, k] for i in N1E if i != j)
            m.addConstr(
                A_e1[j, k] >= e_j - M_time * (1 - visits_jk),
                f"E1_TW_lo_{j}_{k}")
            if l_j < float('inf'):
                m.addConstr(
                    A_e1[j, k] <= l_j + M_time * (1 - visits_jk),
                    f"E1_TW_hi_{j}_{k}")

    # ------ E1 载重/体积 (统一 Q/V, 送减取增) ------

    # (12)(13) 总配送守恒
    total_del_w = sum(t.w for t in e1_tasks if t.kind == 'd')
    total_del_v = sum(t.v for t in e1_tasks if t.kind == 'd')
    m.addConstr(
        gp.quicksum(Q_k[depot_id, k] for k in K) == total_del_w,
        "E1_TotalWeight")
    m.addConstr(
        gp.quicksum(Vol_k[depot_id, k] for k in K) == total_del_v,
        "E1_TotalVol")

    # 未使用卡车初始载量 = 0
    for k in K:
        vehicle_used = gp.quicksum(x[depot_id, j, k] for j in C1)
        m.addConstr(Q_k[depot_id, k] <= problem.e1_weight_cap * vehicle_used,
                    f"E1_InitW_{k}")
        m.addConstr(Vol_k[depot_id, k] <= problem.e1_volume_cap * vehicle_used,
                    f"E1_InitV_{k}")

    # 载重/体积双侧 Big-M: Q_j = Q_i - q_j (送减取增)
    for i in N1E:
        for j in C1:
            if i == j or (i, j) in forbidden_e1:
                continue
            t_j = e1_task_map[j]
            q_j = _signed_q(t_j)
            v_j = _signed_v(t_j)
            for k in K:
                m.addConstr(
                    Q_k[j, k] <= Q_k[i, k] - q_j + M_weight_e1 * (1 - x[i, j, k]),
                    f"E1_W_up_{i}_{j}_{k}")
                m.addConstr(
                    Q_k[j, k] >= Q_k[i, k] - q_j - M_weight_e1 * (1 - x[i, j, k]),
                    f"E1_W_lo_{i}_{j}_{k}")
                m.addConstr(
                    Vol_k[j, k] <= Vol_k[i, k] - v_j + M_vol_e1 * (1 - x[i, j, k]),
                    f"E1_V_up_{i}_{j}_{k}")
                m.addConstr(
                    Vol_k[j, k] >= Vol_k[i, k] - v_j - M_vol_e1 * (1 - x[i, j, k]),
                    f"E1_V_lo_{i}_{j}_{k}")

    # 容量上界已由变量 ub 实现

    # ===================================================================
    #  第二阶段约束 (文档3 §5)
    # ===================================================================

    # (19) 每个 C2 任务恰好被服务一次
    for j in C2:
        sat_j = e2_task_to_sat_map.get(j)
        if not sat_j:
            continue
        vehs = V_by_sat[sat_j]
        valid_src = [sat_j] + [t.id for t in e2_tasks_by_sat.get(sat_j, [])]
        visit_sum = gp.quicksum(
            z[i, j, v] for v in vehs
            for i in valid_src
            if i != j and (i, j, v) in z)
        m.addConstr(visit_sum == 1, f"E2_Visit_{j}")

    for sat_id in F:
        vehs = V_by_sat[sat_id]
        tasks_in_sat = e2_tasks_by_sat.get(sat_id, [])
        t_ids = [t.id for t in tasks_in_sat]
        valid_nodes = [sat_id] + t_ids

        for v in vehs:
            # (20) 从 f 出发 = 返回 f, 且 ≤ 1
            m.addConstr(
                gp.quicksum(z[sat_id, j, v] for j in t_ids
                            if (sat_id, j, v) in z)
                == gp.quicksum(z[i, sat_id, v] for i in t_ids
                               if (i, sat_id, v) in z),
                f"E2_DepotBal_{sat_id}_{v}")
            m.addConstr(
                gp.quicksum(z[sat_id, j, v] for j in t_ids
                            if (sat_id, j, v) in z) <= 1,
                f"E2_Start_{sat_id}_{v}")

            # (21) 流量守恒
            for h in t_ids:
                m.addConstr(
                    gp.quicksum(z[i, h, v] for i in valid_nodes
                                if i != h and (i, h, v) in z)
                    - gp.quicksum(z[h, j, v] for j in valid_nodes
                                  if j != h and (h, j, v) in z) == 0,
                    f"E2_Flow_{h}_{v}")

    # ------ E2 时间约束 ------
    for f in F:
        for v in V_by_sat[f]:
            m.addConstr(A_e2[f, v] == 0, f"E2_SatAnchor_{f}_{v}")

    # (22) 时间连续性
    for sat_id in F:
        tasks_in_sat = e2_tasks_by_sat.get(sat_id, [])
        t_ids = [t.id for t in tasks_in_sat]
        valid_nodes = [sat_id] + t_ids
        for v in V_by_sat[sat_id]:
            for i in valid_nodes:
                s_i = e2_service_time_map.get(i, 0.0)
                for j in t_ids:
                    if i == j or (i, j, v) not in z:
                        continue
                    m.addConstr(
                        A_e2[j, v] >= (A_e2[i, v] + s_i + time_map[i, j])
                        - M_time * (1 - z[i, j, v]),
                        f"E2_Time_{i}_{j}_{v}")

    # ------ E2 载重/体积 (统一 Q/V, 送减取增 + 中转) ------

    # 初始载量 = 该车服务的配送任务总重
    for sat_id in F:
        vehs = V_by_sat[sat_id]
        tasks_in_sat = e2_tasks_by_sat.get(sat_id, [])
        del_tasks = [t for t in tasks_in_sat if t.kind == 'd']
        valid_nodes = [sat_id] + [t.id for t in tasks_in_sat]

        for v in vehs:
            if not tasks_in_sat:
                if (sat_id, v) in Q_r:
                    m.addConstr(Q_r[sat_id, v] == 0)
                    m.addConstr(Vol_r[sat_id, v] == 0)
                continue

            if del_tasks and (sat_id, v) in Q_r:
                m.addConstr(
                    Q_r[sat_id, v] == gp.quicksum(
                        t.w * gp.quicksum(
                            z[i, t.id, v]
                            for i in valid_nodes
                            if i != t.id and (i, t.id, v) in z
                        ) for t in del_tasks),
                    f"E2_InitQ_{sat_id}_{v}")
                m.addConstr(
                    Vol_r[sat_id, v] == gp.quicksum(
                        t.v * gp.quicksum(
                            z[i, t.id, v]
                            for i in valid_nodes
                            if i != t.id and (i, t.id, v) in z
                        ) for t in del_tasks),
                    f"E2_InitVol_{sat_id}_{v}")
            elif (sat_id, v) in Q_r:
                m.addConstr(Q_r[sat_id, v] == 0)
                m.addConstr(Vol_r[sat_id, v] == 0)

    # (27)(28) 总配送守恒
    for sat_id in F:
        vehs = V_by_sat[sat_id]
        tasks_in_sat = e2_tasks_by_sat.get(sat_id, [])
        del_w = sum(t.w for t in tasks_in_sat if t.kind == 'd')
        del_v = sum(t.v for t in tasks_in_sat if t.kind == 'd')
        if del_w > 0:
            m.addConstr(
                gp.quicksum(Q_r[sat_id, v] for v in vehs
                            if (sat_id, v) in Q_r) == del_w,
                f"E2_TotalW_{sat_id}")
        if del_v > 0:
            m.addConstr(
                gp.quicksum(Vol_r[sat_id, v] for v in vehs
                            if (sat_id, v) in Vol_r) == del_v,
                f"E2_TotalV_{sat_id}")

    # 未使用车辆初始载量 = 0
    for sat_id in F:
        vehs = V_by_sat[sat_id]
        t_ids = [t.id for t in e2_tasks_by_sat.get(sat_id, [])]
        for v in vehs:
            vehicle_used = gp.quicksum(
                z[sat_id, j, v] for j in t_ids if (sat_id, j, v) in z)
            if (sat_id, v) in Q_r:
                m.addConstr(
                    Q_r[sat_id, v] <= problem.e2_weight_cap * vehicle_used,
                    f"E2_InitQU_{sat_id}_{v}")
                m.addConstr(
                    Vol_r[sat_id, v] <= problem.e2_volume_cap * vehicle_used,
                    f"E2_InitVU_{sat_id}_{v}")

    # E2 载重/体积双侧 Big-M (送减取增)
    for sat_id in F:
        tasks_in_sat = e2_tasks_by_sat.get(sat_id, [])
        t_ids = [t.id for t in tasks_in_sat]
        valid_nodes = [sat_id] + t_ids
        for v in V_by_sat[sat_id]:
            for i in valid_nodes:
                for j in t_ids:
                    if i == j or (i, j, v) not in z:
                        continue
                    t_j = e2_task_map[j]
                    q_j = _signed_q(t_j)
                    v_j = _signed_v(t_j)

                    m.addConstr(
                        Q_r[j, v] <= Q_r[i, v] - q_j
                        + M_weight_e2 * (1 - z[i, j, v]),
                        f"E2_Q_up_{i}_{j}_{v}")
                    m.addConstr(
                        Q_r[j, v] >= Q_r[i, v] - q_j
                        - M_weight_e2 * (1 - z[i, j, v]),
                        f"E2_Q_lo_{i}_{j}_{v}")
                    m.addConstr(
                        Vol_r[j, v] <= Vol_r[i, v] - v_j
                        + M_vol_e2 * (1 - z[i, j, v]),
                        f"E2_V_up_{i}_{j}_{v}")
                    m.addConstr(
                        Vol_r[j, v] >= Vol_r[i, v] - v_j
                        - M_vol_e2 * (1 - z[i, j, v]),
                        f"E2_V_lo_{i}_{j}_{v}")

    # ===================================================================
    #  一二阶段衔接约束 (文档3 §7)
    # ===================================================================

    for f in F:
        tasks_in_f = e2_tasks_by_sat.get(f, [])
        if not tasks_in_f:
            continue

        e1_del_node = P1_inv_map.get(f)
        if e1_del_node is None:
            continue

        s_f = e1_service_time_map.get(e1_del_node, 0.0)

        # (37) E2 出发 ≥ E1 配送到达 + 卸货时间
        for v_idx in range(vehicles_per_satellite):
            for k in K:
                e1_visits = gp.quicksum(
                    x[i, e1_del_node, k] for i in N1E if i != e1_del_node)
                m.addConstr(
                    A_start[f, v_idx] >= A_e1[e1_del_node, k] + s_f
                    - M_time * (1 - e1_visits),
                    f"Link_Start_{f}_{v_idx}_{k}")

        # (35) 客户绝对时间窗
        valid_nodes_f = [f] + [t.id for t in tasks_in_f]
        for v_idx in range(vehicles_per_satellite):
            v = (f, v_idx)
            for j_task in tasks_in_f:
                e_j, l_j = j_task.tw
                is_served = gp.quicksum(
                    z[i, j_task.id, v]
                    for i in valid_nodes_f
                    if i != j_task.id and (i, j_task.id, v) in z)
                abs_arrival = A_start[f, v_idx] + A_e2[j_task.id, v]
                m.addConstr(
                    abs_arrival >= e_j - M_time * (1 - is_served),
                    f"Link_TW_lo_{f}_{v_idx}_{j_task.id}")
                if l_j < float('inf'):
                    m.addConstr(
                        abs_arrival <= l_j + M_time * (1 - is_served),
                        f"Link_TW_hi_{f}_{v_idx}_{j_task.id}")

        # (36) 返回时间
        for v_idx in range(vehicles_per_satellite):
            v = (f, v_idx)
            for i in [t.id for t in tasks_in_f]:
                if (i, f, v) not in z:
                    continue
                s_i = e2_service_time_map.get(i, 0.0)
                m.addConstr(
                    A_return[f, v_idx]
                    >= A_start[f, v_idx] + A_e2[i, v] + s_i + time_map[i, f]
                    - M_time * (1 - z[i, f, v]),
                    f"Link_Ret_lo_{f}_{v_idx}_{i}")
                m.addConstr(
                    A_return[f, v_idx]
                    <= A_start[f, v_idx] + A_e2[i, v] + s_i + time_map[i, f]
                    + M_time * (1 - z[i, f, v]),
                    f"Link_Ret_hi_{f}_{v_idx}_{i}")

        # (38) E1 回收 ≥ E2 返回
        e1_pick_node = P1_map.get(e1_del_node)
        if e1_pick_node is None:
            continue
        for v_idx in range(vehicles_per_satellite):
            for k in K:
                e1_pick_visits = gp.quicksum(
                    x[i, e1_pick_node, k] for i in N1E if i != e1_pick_node)
                m.addConstr(
                    A_e1[e1_pick_node, k]
                    >= A_return[f, v_idx]
                    - M_time * (1 - e1_pick_visits),
                    f"Link_Pickup_{f}_{v_idx}_{k}")
