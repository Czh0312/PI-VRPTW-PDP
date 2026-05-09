# models/model_2/model.py
# -*- coding: utf-8 -*-
"""
对齐文档2 (Unified Two-Echelon Pickup and Delivery Problem)
核心变化:
  E1: Q_k, Vol_k (统一载重/体积)
  E2: Q_r, Vol_r
  新增: TFp 参数, FD_nodes / FP_nodes 集合
  修复: A_e1 / A_e2 加车辆维度，新增 A_start 衔接变量
"""
import gurobipy as gp
from gurobipy import GRB

from .objective import set_objective
from .constraints import add_all_constraints


def build_unified_pdp_model(problem, e1_tasks, e2_tasks_by_sat,
                            vehicles_per_satellite, TFp=9999.0):
    """
    构建带取送货的 2E-VRP-PDP 数学模型 (文档2)
    """
    depot_id = problem.depot.id
    F = [s.id for s in problem.satellites]

    # ==========================================
    # 模块 1：节点与任务映射
    # ==========================================
    e1_task_nodes = [t.id for t in e1_tasks]
    e1_task_map = {t.id: t for t in e1_tasks}

    FD_nodes = [t.id for t in e1_tasks if t.kind == 'd']
    FP_nodes = [t.id for t in e1_tasks if t.kind == 'p']

    e2_task_nodes = []
    e2_task_map = {}
    e2_task_to_sat_map = {}
    for sat, tasks in e2_tasks_by_sat.items():
        for t in tasks:
            e2_task_nodes.append(t.id)
            e2_task_map[t.id] = t
            e2_task_to_sat_map[t.id] = sat

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
        s_time = getattr(problem.nodes[sat_id], "service_time", 0.0)
        e1_service_time_map[sat_id] = s_time
    for t_id, task in e1_task_map.items():
        # E1 任务节点的服务时间 = 对应物理 satellite 的 service_time
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

    ctx = {
        'depot_id': depot_id,
        'F': F,
        'FD_nodes': FD_nodes,
        'FP_nodes': FP_nodes,
        'N1E': [depot_id] + e1_task_nodes,
        'N2E': F + e2_task_nodes,
        'N_E1_Tasks': e1_task_nodes,
        'N_E2_Tasks': e2_task_nodes,
        'K': K_list,
        'V_by_sat': V_by_sat_dict,
        'vehicles_per_satellite': vehicles_per_satellite,
        'e1_task_map': e1_task_map,
        'e2_task_map': e2_task_map,
        'e2_task_to_sat_map': e2_task_to_sat_map,
        'dist_map': dist_map,
        'time_map': time_map,
        'e1_service_time_map': e1_service_time_map,
        'e2_service_time_map': e2_service_time_map,
        'TFp': TFp,
        # Big-M 取安全的大值
        'M_time': 100000.0,
        'M_weight_e1': 2 * problem.e1_weight_cap + 1000.0,
        'M_weight_e2': 2 * problem.e2_weight_cap + 1000.0,
        'M_vol_e1': 2 * problem.e1_volume_cap + 1000.0,
        'M_vol_e2': 2 * problem.e2_volume_cap + 1000.0,
    }

    # ==========================================
    # 模块 4：决策变量定义
    # ==========================================
    m = gp.Model("2E_VRP_PDP_Exact")
    N1E = ctx['N1E']
    N2E = ctx['N2E']
    K = ctx['K']

    # --- E1 路由与状态变量 ---
    x = m.addVars(N1E, N1E, K, vtype=GRB.BINARY, name="x")
    # A_e1 现在按 (node, k) 索引
    A_e1 = m.addVars(N1E, K, vtype=GRB.CONTINUOUS, lb=0.0, name="A_e1")
    # 载重/体积：直接用变量上界替代单独的容量约束
    Q_k = m.addVars(N1E, K, vtype=GRB.CONTINUOUS,
                    lb=0.0, ub=problem.e1_weight_cap, name="Q_k")
    Vol_k = m.addVars(N1E, K, vtype=GRB.CONTINUOUS,
                      lb=0.0, ub=problem.e1_volume_cap, name="Vol_k")

    # --- E2 路由与状态变量 ---
    z = {}
    Q_r = {}
    Vol_r = {}
    A_e2 = {}
    for sat_id in F:
        vehs = ctx['V_by_sat'][sat_id]
        valid_nodes = [sat_id] + [t.id for t in e2_tasks_by_sat.get(sat_id, [])]
        for v in vehs:
            for i in valid_nodes:
                Q_r[i, v] = m.addVar(
                    lb=0.0, ub=problem.e2_weight_cap, name=f"Q_r_{i}_{v}")
                Vol_r[i, v] = m.addVar(
                    lb=0.0, ub=problem.e2_volume_cap, name=f"Vol_r_{i}_{v}")
                A_e2[i, v] = m.addVar(
                    lb=0.0, name=f"A_e2_{i}_{v}")
                for j in valid_nodes:
                    if i != j:
                        z[i, j, v] = m.addVar(
                            vtype=GRB.BINARY, name=f"z_{i}_{j}_{v}")

    # --- E2 发车时刻（衔接用，同 model 1 的 A_start） ---
    A_start = m.addVars(F, range(vehicles_per_satellite),
                        vtype=GRB.CONTINUOUS, lb=0.0, name="A_start")

    vars_dict = {
        'x': x, 'A_e1': A_e1, 'Q_k': Q_k, 'Vol_k': Vol_k,
        'z': z, 'A_e2': A_e2, 'Q_r': Q_r, 'Vol_r': Vol_r,
        'A_start': A_start,
    }

    set_objective(m, vars_dict, ctx)
    add_all_constraints(m, problem, e1_tasks, e2_tasks_by_sat, vars_dict, ctx)

    return m, vars_dict, ctx