# models/model_5/model.py
# -*- coding: utf-8 -*-
"""
Model 5 的基础 MIP：取送货路线优化，载重/体积使用统一 Q/V。
当前 exact 入口在该 MIP 解的基础上做启发式中转后处理；联合中转
变量未在 MIP 中建模。
"""
import gurobipy as gp
from gurobipy import GRB

from .objective import set_objective
from .constraints import add_all_constraints


def build_transshipment_model(problem, e1_tasks, e2_tasks_by_sat,
                               vehicles_per_satellite):
    """构建 2E-VRP-PDP 数学模型 (无中转变量，中转由后处理完成)"""
    depot_id = problem.depot.id
    F = [s.id for s in problem.satellites]

    # ==========================================
    # 模块 1：节点与任务映射
    # ==========================================
    e1_task_nodes = [t.id for t in e1_tasks]
    e1_task_map = {t.id: t for t in e1_tasks}
    FD_nodes = [t.id for t in e1_tasks if t.kind == 'd']
    FP_nodes = [t.id for t in e1_tasks if t.kind == 'p']

    # P1 映射: 配送 -> 回收; P1_inv: satellite -> 配送虚拟节点
    P1_map = {}
    P1_inv_map = {}
    for f in F:
        d_id = f"{f}d"
        p_id = f"{f}p"
        if d_id in e1_task_map:
            P1_inv_map[f] = d_id
            if p_id in e1_task_map:
                P1_map[d_id] = p_id

    e2_task_nodes = []
    e2_task_map = {}
    e2_task_to_sat_map = {}
    CD_nodes, CP_nodes = [], []
    for sat, tasks in e2_tasks_by_sat.items():
        for t in tasks:
            e2_task_nodes.append(t.id)
            e2_task_map[t.id] = t
            e2_task_to_sat_map[t.id] = sat
            if t.kind == 'd':
                CD_nodes.append(t.id)
            else:
                CP_nodes.append(t.id)

    # T_nodes 不再使用（中转由后处理完成）
    T_nodes = list(e2_task_nodes)

    # ==========================================
    # 模块 2：距离与时间矩阵
    # ==========================================
    dist_map, time_map = {}, {}
    all_ids = list(set([depot_id] + F + e1_task_nodes + e2_task_nodes))

    def get_physical_loc(node_id: str) -> str:
        if node_id == depot_id or node_id in F:
            return node_id
        task = e1_task_map.get(node_id) or e2_task_map.get(node_id)
        if task:
            return task.cust
        return node_id

    for i in all_ids:
        loc_i = get_physical_loc(i)
        for j in all_ids:
            if i == j:
                dist_map[i, j], time_map[i, j] = 0.0, 0.0
                continue
            loc_j = get_physical_loc(j)
            dist_map[i, j] = problem.distance(loc_i, loc_j)
            time_map[i, j] = problem.travel_time(loc_i, loc_j)

    # ==========================================
    # 模块 3：服务时间与上下文构建
    # ==========================================
    e1_service_time_map = {depot_id: 0.0}
    for sat_id in F:
        e1_service_time_map[sat_id] = getattr(
            problem.nodes[sat_id], "service_time", 0.0)
    for t_id, task in e1_task_map.items():
        phys = task.cust
        e1_service_time_map[t_id] = getattr(
            problem.nodes.get(phys), "service_time", 0.0
        ) if phys in problem.nodes else 0.0

    e2_service_time_map = {n: 0.0 for n in [depot_id] + F}
    for t_id, task in e2_task_map.items():
        e2_service_time_map[t_id] = (
            problem.nodes[task.cust].service_time
            if task.cust in problem.nodes else 0.0
        )

    K_list = list(range(problem.e1_vehicle_count))
    V_by_sat_dict = {
        sid: [(sid, v) for v in range(vehicles_per_satellite)]
        for sid in F
    }
    V_all = []
    for sid in F:
        V_all.extend(V_by_sat_dict[sid])

    ctx = {
        'depot_id': depot_id,
        'F': F,
        'FD_nodes': FD_nodes,
        'FP_nodes': FP_nodes,
        'N1E': [depot_id] + e1_task_nodes,
        'C1': e1_task_nodes,
        'N2E': F + e2_task_nodes,
        'C2': e2_task_nodes,
        'CD_nodes': CD_nodes,
        'CP_nodes': CP_nodes,
        'T': T_nodes,
        'K': K_list,
        'V': V_all,
        'V_by_sat': V_by_sat_dict,
        'vehicles_per_satellite': vehicles_per_satellite,
        'e1_task_map': e1_task_map,
        'e2_task_map': e2_task_map,
        'e2_task_to_sat_map': e2_task_to_sat_map,
        'P1_map': P1_map,
        'P1_inv_map': P1_inv_map,
        'dist_map': dist_map,
        'time_map': time_map,
        'e1_service_time_map': e1_service_time_map,
        'e2_service_time_map': e2_service_time_map,
        # Big-M
        'M_time': 100000.0,
        'M_weight_e1': 2 * problem.e1_weight_cap + 1000.0,
        'M_weight_e2': 2 * problem.e2_weight_cap + 1000.0,
        'M_vol_e1': 2 * problem.e1_volume_cap + 1000.0,
        'M_vol_e2': 2 * problem.e2_volume_cap + 1000.0,
    }

    # ==========================================
    # 模块 4：决策变量定义
    # ==========================================
    m = gp.Model("2E_VRP_PDP_Trans")
    N1E = ctx['N1E']
    K = ctx['K']

    # --- E1 路由与状态变量 (统一 Q/V) ---
    x = m.addVars(N1E, N1E, K, vtype=GRB.BINARY, name="x")
    A_e1 = m.addVars(N1E, K, vtype=GRB.CONTINUOUS, lb=0.0, name="A_e1")
    Q_k = m.addVars(N1E, K, vtype=GRB.CONTINUOUS,
                    lb=0.0, ub=problem.e1_weight_cap, name="Q_k")
    Vol_k = m.addVars(N1E, K, vtype=GRB.CONTINUOUS,
                      lb=0.0, ub=problem.e1_volume_cap, name="Vol_k")

    # --- E2 路由与状态变量 (统一 Q/V) ---
    z = {}
    A_e2 = {}
    Q_r = {}
    Vol_r = {}
    for sat_id in F:
        vehs = V_by_sat_dict[sat_id]
        valid_nodes = [sat_id] + [t.id for t in e2_tasks_by_sat.get(sat_id, [])]
        for v_key in vehs:
            for i in valid_nodes:
                Q_r[i, v_key] = m.addVar(
                    lb=0.0, ub=problem.e2_weight_cap,
                    name=f"Q_{i}_{v_key}")
                Vol_r[i, v_key] = m.addVar(
                    lb=0.0, ub=problem.e2_volume_cap,
                    name=f"Vol_{i}_{v_key}")
                A_e2[i, v_key] = m.addVar(
                    lb=0.0, name=f"A_e2_{i}_{v_key}")
                for j in valid_nodes:
                    if i != j:
                        z[i, j, v_key] = m.addVar(
                            vtype=GRB.BINARY, name=f"z_{i}_{j}_{v_key}")

    # --- 衔接变量 ---
    A_start = m.addVars(F, range(vehicles_per_satellite),
                         vtype=GRB.CONTINUOUS, lb=0.0, name="A_start")
    A_return = m.addVars(F, range(vehicles_per_satellite),
                          vtype=GRB.CONTINUOUS, lb=0.0, name="A_return")

    vars_dict = {
        'x': x, 'A_e1': A_e1, 'Q_k': Q_k, 'Vol_k': Vol_k,
        'z': z, 'A_e2': A_e2, 'Q_r': Q_r, 'Vol_r': Vol_r,
        'A_start': A_start, 'A_return': A_return,
    }

    set_objective(m, vars_dict, ctx)
    add_all_constraints(m, problem, e1_tasks, e2_tasks_by_sat, vars_dict, ctx)

    return m, vars_dict, ctx
