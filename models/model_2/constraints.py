# models/model_2/constraints.py
# -*- coding: utf-8 -*-
"""
对齐文档2 (Unified Two-Echelon Pickup and Delivery Problem)
所有约束编号对应文档2的公式编号

修复要点:
  1) A_e1 按 (node, k) 索引, A_e2 按 (node, v_key) 索引
  2) 新增 A_start[f, v_idx] 作为 E2 发车时刻
  3) 客户时间窗用 is_served + e1_visits 双层 Big-M 松弛
  4) 删除错误的跨车 Prec 约束 (由 TFp 取代)
"""
import gurobipy as gp


def _signed_q(task):
    """文档2: q_i > 0 配送, q_i < 0 回收"""
    return task.w if task.kind == 'd' else -task.w


def _signed_v(task):
    """文档2: v_i > 0 配送, v_i < 0 回收"""
    return task.v if task.kind == 'd' else -task.v


def add_all_constraints(m: gp.Model, problem, e1_tasks, e2_tasks_by_sat,
                        vars_dict: dict, ctx: dict):
    # ---------- 解包变量 ----------
    x = vars_dict['x']
    z = vars_dict['z']
    A_e1 = vars_dict['A_e1']
    A_e2 = vars_dict['A_e2']
    A_start = vars_dict['A_start']
    Q_k = vars_dict['Q_k']
    Vol_k = vars_dict['Vol_k']
    Q_r = vars_dict['Q_r']
    Vol_r = vars_dict['Vol_r']

    # ---------- 解包上下文 ----------
    depot_id = ctx['depot_id']
    F = ctx['F']
    FP_nodes = ctx['FP_nodes']
    N1E = ctx['N1E']
    N2E = ctx['N2E']
    N_E1_Tasks = ctx['N_E1_Tasks']
    N_E2_Tasks = ctx['N_E2_Tasks']
    K = ctx['K']
    V_by_sat = ctx['V_by_sat']
    vehicles_per_satellite = ctx['vehicles_per_satellite']
    e1_task_map = ctx['e1_task_map']
    e2_task_map = ctx['e2_task_map']
    e2_task_to_sat_map = ctx['e2_task_to_sat_map']
    time_map = ctx['time_map']
    e1_service_time_map = ctx['e1_service_time_map']
    e2_service_time_map = ctx['e2_service_time_map']

    TFp = ctx['TFp']
    M_time = ctx['M_time']
    M_weight_e1 = ctx['M_weight_e1']
    M_weight_e2 = ctx['M_weight_e2']
    M_vol_e1 = ctx['M_vol_e1']
    M_vol_e2 = ctx['M_vol_e2']

    # ===================================================================
    #  第一阶段约束  (文档2 §5)
    # ===================================================================

    # ------ 5.1 路径与流量守恒 ------

    # (2) 每个 C1 任务恰好被服务一次
    for j in N_E1_Tasks:
        m.addConstr(
            gp.quicksum(x[i, j, k] for i in N1E for k in K if i != j) == 1,
            f"E1_Visit_{j}")

    # (3) 每辆卡车最多从总仓库出发一次
    for k in K:
        m.addConstr(
            gp.quicksum(x[depot_id, j, k] for j in N_E1_Tasks) <= 1,
            f"E1_Start_{k}")

    # (4) 流量守恒
    for k in K:
        for h in N_E1_Tasks:
            m.addConstr(
                gp.quicksum(x[i, h, k] for i in N1E if i != h)
                - gp.quicksum(x[h, j, k] for j in N1E if j != h) == 0,
                f"E1_Flow_{h}_{k}")
    # 子环消除: 卡车 k 若访问任一任务节点, 必须从 depot 出发
    for k in K:
        depot_out = gp.quicksum(x[depot_id, jj, k] for jj in N_E1_Tasks)
        for j in N_E1_Tasks:
            m.addConstr(
                gp.quicksum(x[i, j, k] for i in N1E if i != j) <= depot_out,
                f"E1_SubtourElim_{j}_{k}")
                
    # (5) 每辆卡车最多返回总仓库一次
    for k in K:
        m.addConstr(
            gp.quicksum(x[i, depot_id, k] for i in N_E1_Tasks) <= 1,
            f"E1_End_{k}")

    # (6)(7) 出发/返回车辆 ≤ |K| —— 已被 (3)(5) 蕴含，此处省略以减少冗余

    # (8) 出入库数量一致 —— 流量守恒已蕴含，但保留对 LP 松弛有帮助
    m.addConstr(
        gp.quicksum(x[depot_id, j, k] for k in K for j in N_E1_Tasks)
        == gp.quicksum(x[i, depot_id, k] for k in K for i in N_E1_Tasks),
        "E1_DepotBalance")

    # ------ 5.2 时间窗与时间连续性 ------

    # 仓库时间锚定: 每辆卡车在 depot 的时间 = 0
    for k in K:
        m.addConstr(A_e1[depot_id, k] == 0, f"E1_DepotTime_{k}")

    # 禁止同一前置仓的 p→d 边（回收后不能再配送）
    forbidden_edges = set()
    for s_id in F:
        pid, did = f"{s_id}p", f"{s_id}d"
        if pid in e1_task_map and did in e1_task_map:
            for k in K:
               m.addConstr(x[pid, did, k] == 0, f"E1_ForbidArc_{s_id}_{k}")

    # (9) 时间连续性 MTZ
    for i in N1E:
        s_i = e1_service_time_map.get(i, 0.0)
        for j in N_E1_Tasks:
            if i == j or (i, j) in forbidden_edges:
                continue
            for k in K:
                m.addConstr(
                    A_e1[j, k] >= (A_e1[i, k] + s_i + time_map[i, j])
                    - M_time * (1 - x[i, j, k]),
                    f"E1_Time_{i}_{j}_{k}")

    # 任务硬时间窗: 对每个 (j, k) 都加
    for j in N_E1_Tasks:
        e_j, l_j = e1_task_map[j].tw
        for k in K:
            # 只有 k 访问了 j 时，时间窗才生效
            visits_jk = gp.quicksum(
                x[i, j, k] for i in N1E if i != j
            )
            m.addConstr(
                A_e1[j, k] >= e_j - M_time * (1 - visits_jk),
                f"E1_TW_lo_{j}_{k}")
            if l_j < float('inf'):
                m.addConstr(
                    A_e1[j, k] <= l_j + M_time * (1 - visits_jk),
                    f"E1_TW_hi_{j}_{k}")

    # (10) 前置仓回收节点的统一最早时间 TFp
    for j in FP_nodes:
        for k in K:
            visits_jk = gp.quicksum(
                x[i, j, k] for i in N1E if i != j
            )
            m.addConstr(
                A_e1[j, k] >= TFp - M_time * (1 - visits_jk),
                f"E1_TFp_{j}_{k}")

    # 注意: 旧代码里的 E1_Prec (同前置仓 p >= d) 已删除。
    # 理由: (10) 已把所有 pickup 节点推到 TFp 之后，而 delivery 节点时间窗都在 TFp 之前。
    # 两点不由同一辆车访问时，旧写法反而会错误地跨车强制顺序。

    # ------ 5.3 载重与容积 ------

    # (15) 总配送重量守恒
    total_del_w = sum(t.w for t in e1_tasks if t.kind == 'd')
    m.addConstr(
        gp.quicksum(Q_k[depot_id, k] for k in K) == total_del_w,
        "E1_TotalWeight")

    # (16) 总配送体积守恒
    total_del_v = sum(t.v for t in e1_tasks if t.kind == 'd')
    m.addConstr(
        gp.quicksum(Vol_k[depot_id, k] for k in K) == total_del_v,
        "E1_TotalVol")

    # 未使用的卡车初始载量 = 0
    # 这里用 cap 做系数而不是 M_weight_e1，避免 LP 松弛过松
    for k in K:
        vehicle_used = gp.quicksum(x[depot_id, j, k] for j in N_E1_Tasks)
        m.addConstr(
            Q_k[depot_id, k] <= problem.e1_weight_cap * vehicle_used,
            f"E1_InitW_{k}")
        m.addConstr(
            Vol_k[depot_id, k] <= problem.e1_volume_cap * vehicle_used,
            f"E1_InitV_{k}")

    # (11)(12) 重量双侧 Big-M:  Q_j = Q_i - q_j  （当弧激活）
    # (13)(14) 体积双侧 Big-M
    for i in N1E:
        for j in N_E1_Tasks:
            if i == j or (i, j) in forbidden_edges:
                continue
            t_j = e1_task_map[j]
            q_j = _signed_q(t_j)
            v_j = _signed_v(t_j)
            for k in K:
                m.addConstr(
                    Q_k[j, k] <= Q_k[i, k] - q_j
                    + M_weight_e1 * (1 - x[i, j, k]),
                    f"E1_W_up_{i}_{j}_{k}")
                m.addConstr(
                    Q_k[j, k] >= Q_k[i, k] - q_j
                    - M_weight_e1 * (1 - x[i, j, k]),
                    f"E1_W_lo_{i}_{j}_{k}")
                m.addConstr(
                    Vol_k[j, k] <= Vol_k[i, k] - v_j
                    + M_vol_e1 * (1 - x[i, j, k]),
                    f"E1_V_up_{i}_{j}_{k}")
                m.addConstr(
                    Vol_k[j, k] >= Vol_k[i, k] - v_j
                    - M_vol_e1 * (1 - x[i, j, k]),
                    f"E1_V_lo_{i}_{j}_{k}")

    # (17)(18) 容量上界已通过变量 ub 实现，此处无需额外约束

    # ===================================================================
    #  第二阶段约束  (文档2 §7)
    # ===================================================================

    # ------ 7.1 路径与流量守恒 ------

    # (19) 每个 C2 任务恰好被服务一次
    for j in N_E2_Tasks:
        sat_j = e2_task_to_sat_map.get(j)
        if not sat_j:
            continue
        vehs = V_by_sat[sat_j]
        valid_src = [sat_j] + [t.id for t in e2_tasks_by_sat.get(sat_j, [])]
        m.addConstr(
            gp.quicksum(z[i, j, v] for v in vehs
                        for i in valid_src
                        if i != j and (i, j, v) in z) == 1,
            f"E2_Visit_{j}")

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

    # (25) 车辆只能从自己的前置仓出发 —— 通过 z 变量的定义域已隐式满足

    # ------ 7.2 时间约束 ------

    # 每辆 E2 车在其前置仓的相对时间锚定为 0
    for f in F:
        for v in V_by_sat[f]:
            m.addConstr(A_e2[f, v] == 0, f"E2_SatAnchor_{f}_{v}")

    # (26) 时间连续性 MTZ (按 v 展开)
    for sat_id in F:
        tasks_in_sat = e2_tasks_by_sat.get(sat_id, [])
        t_ids = [t.id for t in tasks_in_sat]
        valid_nodes = [sat_id] + t_ids
        for v in V_by_sat[sat_id]:
            for i in valid_nodes:
                s_i = e2_service_time_map.get(i, 0.0)
                for j in t_ids:
                    if i == j:
                        continue
                    if (i, j, v) not in z:
                        continue
                    m.addConstr(
                        A_e2[j, v] >= (A_e2[i, v] + s_i + time_map[i, j])
                        - M_time * (1 - z[i, j, v]),
                        f"E2_Time_{i}_{j}_{v}")

    # 注意: 旧代码里的 E2_Prec (同客户 p >= d + service) 已删除。
    # 若同一客户的 d 和 p 由不同车辆服务，这条约束无意义；
    # 若由同一车辆服务，MTZ + 时间窗会自然保证正确顺序。

    # ------ 7.3 载重与容积 ------

    # E2 初始载量: 离开前置仓时 = 该车辆所服务的配送任务总重
    for sat_id in F:
        vehs = V_by_sat[sat_id]
        tasks_in_sat = e2_tasks_by_sat.get(sat_id, [])
        t_ids = [t.id for t in tasks_in_sat]
        valid_nodes = [sat_id] + t_ids
        del_tasks = [t for t in tasks_in_sat if t.kind == 'd']

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
                    f"E2_InitW_{sat_id}_{v}")
                m.addConstr(
                    Vol_r[sat_id, v] == gp.quicksum(
                        t.v * gp.quicksum(
                            z[i, t.id, v]
                            for i in valid_nodes
                            if i != t.id and (i, t.id, v) in z
                        ) for t in del_tasks),
                    f"E2_InitV_{sat_id}_{v}")
            elif (sat_id, v) in Q_r:
                m.addConstr(Q_r[sat_id, v] == 0)
                m.addConstr(Vol_r[sat_id, v] == 0)

    # (31) 每个前置仓配送总重守恒
    for sat_id in F:
        vehs = V_by_sat[sat_id]
        tasks_in_sat = e2_tasks_by_sat.get(sat_id, [])
        del_w = sum(t.w for t in tasks_in_sat if t.kind == 'd')
        if del_w > 0:
            m.addConstr(
                gp.quicksum(Q_r[sat_id, v] for v in vehs
                            if (sat_id, v) in Q_r) == del_w,
                f"E2_TotalW_{sat_id}")

    # (32) 每个前置仓配送总体积守恒
    for sat_id in F:
        vehs = V_by_sat[sat_id]
        tasks_in_sat = e2_tasks_by_sat.get(sat_id, [])
        del_v = sum(t.v for t in tasks_in_sat if t.kind == 'd')
        if del_v > 0:
            m.addConstr(
                gp.quicksum(Vol_r[sat_id, v] for v in vehs
                            if (sat_id, v) in Vol_r) == del_v,
                f"E2_TotalV_{sat_id}")

    # (27)(28) 重量双侧 Big-M
    # (29)(30) 体积双侧 Big-M
    for sat_id in F:
        tasks_in_sat = e2_tasks_by_sat.get(sat_id, [])
        t_ids = [t.id for t in tasks_in_sat]
        valid_nodes = [sat_id] + t_ids
        for v in V_by_sat[sat_id]:
            for i in valid_nodes:
                for j in t_ids:
                    if i == j:
                        continue
                    if (i, j, v) not in z:
                        continue
                    t_j = e2_task_map[j]
                    q_j = _signed_q(t_j)
                    v_j = _signed_v(t_j)
                    m.addConstr(
                        Q_r[j, v] <= Q_r[i, v] - q_j
                        + M_weight_e2 * (1 - z[i, j, v]),
                        f"E2_W_up_{i}_{j}_{v}")
                    m.addConstr(
                        Q_r[j, v] >= Q_r[i, v] - q_j
                        - M_weight_e2 * (1 - z[i, j, v]),
                        f"E2_W_lo_{i}_{j}_{v}")
                    m.addConstr(
                        Vol_r[j, v] <= Vol_r[i, v] - v_j
                        + M_vol_e2 * (1 - z[i, j, v]),
                        f"E2_V_up_{i}_{j}_{v}")
                    m.addConstr(
                        Vol_r[j, v] >= Vol_r[i, v] - v_j
                        - M_vol_e2 * (1 - z[i, j, v]),
                        f"E2_V_lo_{i}_{j}_{v}")

    # (33)(34) 容量上界已通过变量 ub 实现，无需额外约束

    # ===================================================================
    #  一二阶段衔接约束  (文档2 §9)
    # ===================================================================

    for f in F:
        tasks_in_f = e2_tasks_by_sat.get(f, [])
        if not tasks_in_f:
            continue

        # E1 送货虚拟节点 (Sxd)
        e1_del_node = f"{f}d"
        if e1_del_node not in e1_task_map:
            continue

        # satellite 的卸货服务时间
        s_f = e1_service_time_map.get(e1_del_node, 0.0)

        valid_nodes_f = [f] + [t.id for t in tasks_in_f]

        # --- (37) E1-E2 时间衔接: E2 发车时间 >= E1 到达 Sxd + 卸货时间 ---
        for v_idx in range(vehicles_per_satellite):
            for k in K:
                # k 是否访问了 e1_del_node
                e1_visits_k = gp.quicksum(
                    x[i, e1_del_node, k] for i in N1E if i != e1_del_node
                )
                m.addConstr(
                    A_start[f, v_idx]
                    >= A_e1[e1_del_node, k] + s_f
                    - M_time * (1 - e1_visits_k),
                    f"Link_Start_{f}_{v_idx}_{k}")

        # --- (35)(36) 客户绝对时间窗: 用 is_served 松弛 ---
        for v_idx in range(vehicles_per_satellite):
            v = (f, v_idx)
            for j_task in tasks_in_f:
                e_j, l_j = j_task.tw
                # is_served = 1 当且仅当车 v 访问了 j_task
                is_served = gp.quicksum(
                    z[i, j_task.id, v]
                    for i in valid_nodes_f
                    if i != j_task.id and (i, j_task.id, v) in z
                )
                abs_arrival = A_start[f, v_idx] + A_e2[j_task.id, v]
                m.addConstr(
                    abs_arrival >= e_j - M_time * (1 - is_served),
                    f"Link_TW_lo_{f}_{v_idx}_{j_task.id}")
                if l_j < float('inf'):
                    m.addConstr(
                        abs_arrival <= l_j + M_time * (1 - is_served),
                        f"Link_TW_hi_{f}_{v_idx}_{j_task.id}")