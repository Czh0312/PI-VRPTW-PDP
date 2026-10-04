# models/routing/variants/model_3/model.py
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

from .formulation.objective import set_objective
from .formulation.constraints import add_all_constraints
from models.routing.common.operating_policy import policy_for, validate_task_inputs


def build_model(problem, e1_tasks, e2_tasks_by_sat,
                vehicles_per_satellite, TFp=9999.0,
                            e2_cost_lower_bounds=None,
                            e1_cost_lower_bound=None):
    """
    构建带取送货的 2E-VRP-PDP 数学模型 (文档2)
    """
    policy = policy_for(problem)
    validate_task_inputs(problem, e1_tasks, e2_tasks_by_sat)
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

    # Safe arc-specific time Big-M values and impossible-arc elimination.
    # A constructive global horizon is substantially tighter than the old
    # 100000 fallback while preserving every feasible route: start every
    # linked E1/E2 activity as early as possible, wait only for an earliest
    # time window, and charge at most one longest arc/service for every task.
    # This also bounds time variables of unvisited infinite-window nodes.
    inf = float("inf")
    e1_tw_map = {depot_id: (policy.dispatch_time, policy.dispatch_time)}
    e1_tw_map.update({task_id: task.tw for task_id, task in e1_task_map.items()})
    e2_tw_map = {sat_id: (0.0, 0.0) for sat_id in F}
    e2_tw_map.update({task_id: task.tw for task_id, task in e2_task_map.items()})

    finite_window_times = [0.0]
    for earliest, latest in list(e1_tw_map.values()) + list(e2_tw_map.values()):
        if earliest < inf:
            finite_window_times.append(earliest)
        if latest < inf:
            finite_window_times.append(latest)
    # 时间上界应来自实例本身，而不是本次传入的任务集：route-master 以空 E2
    # 任务集构建 E1 基模型后再加入 E2 路线列，其客户时间窗必须仍被 horizon
    # 覆盖，否则 RouteTWLo 会超出 task_time 上界而不可行。
    for _c in problem.customers:
        for _tw in (_c.tw_del, _c.tw_pick):
            if _tw[0] < inf:
                finite_window_times.append(_tw[0])
            if _tw[1] < inf:
                finite_window_times.append(_tw[1])
    if TFp < 9000.0:
        finite_window_times.append(TFp)
    max_arc_time = max(time_map.values(), default=0.0)
    total_service = (sum(e1_service_time_map.values())
                     + sum(e2_service_time_map.values()))
    task_count = len(e1_task_nodes) + len(e2_task_nodes)
    time_horizon = (
        max(finite_window_times)
        + (task_count + 2) * max_arc_time
        + total_service
        + 1.0
    )
    if policy.single_day:
        time_horizon = max(time_horizon, policy.day_end)
    # ``fallback_time_m`` must also deactivate an arc whose source is at the
    # horizon and whose target is an unvisited node at time zero.
    fallback_time_m = (time_horizon + max_arc_time + policy.e2_dispatch_prep_time
                       + policy.e2_return_unload_time + policy.depot_return_unload_time)

    def _arc_time_m(tw_map, service_map, i, j):
        latest_i = tw_map.get(i, (0.0, inf))[1]
        if latest_i == inf:
            return fallback_time_m
        return max(
            service_map.get(i, 0.0) + time_map[i, j],
            latest_i + service_map.get(i, 0.0) + time_map[i, j],
        )

    def _time_infeasible(tw_map, service_map, i, j):
        earliest_i = tw_map.get(i, (0.0, inf))[0]
        latest_j = tw_map.get(j, (0.0, inf))[1]
        return (latest_j < inf and earliest_i + service_map.get(i, 0.0)
                + time_map[i, j] > latest_j + 1e-6)

    e1_infeasible_arcs = {
        (i, j) for i in [depot_id] + e1_task_nodes for j in e1_task_nodes
        if i != j and _time_infeasible(e1_tw_map, e1_service_time_map, i, j)
    }
    e1_arc_time_m = {
        (i, j): _arc_time_m(e1_tw_map, e1_service_time_map, i, j)
        for i in [depot_id] + e1_task_nodes for j in e1_task_nodes if i != j
    }
    e2_infeasible_arcs, e2_arc_time_m = set(), {}
    for sat_id, tasks in e2_tasks_by_sat.items():
        valid_nodes = [sat_id] + [task.id for task in tasks]
        for i in valid_nodes:
            for j in [task.id for task in tasks]:
                if i == j:
                    continue
                if _time_infeasible(e2_tw_map, e2_service_time_map, i, j):
                    e2_infeasible_arcs.add((i, j))
                e2_arc_time_m[i, j] = _arc_time_m(
                    e2_tw_map, e2_service_time_map, i, j
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
        'e1_infeasible_arcs': e1_infeasible_arcs,
        'e2_infeasible_arcs': e2_infeasible_arcs,
        'e1_arc_time_m': e1_arc_time_m,
        'e2_arc_time_m': e2_arc_time_m,
        'e2_cost_lower_bounds': e2_cost_lower_bounds or {},
        'e1_cost_lower_bound': e1_cost_lower_bound or 0.0,
        'TFp': TFp,
        'time_horizon': time_horizon,
        'operating_policy': policy,
        'problem': problem,
        # Big-M is derived from the safe instance horizon above.
        'M_time': fallback_time_m,
        'M_weight_e1': 2 * problem.e1_weight_cap + 1000.0,
        'M_weight_e2': 2 * problem.e2_weight_cap + 1000.0,
        'M_vol_e1': 2 * problem.e1_volume_cap + 1000.0,
        'M_vol_e2': 2 * problem.e2_volume_cap + 1000.0,
        # Needed by the warm-start helper to rebuild same-customer forbidden
        # arcs and replay E2 schedules (the e2_task_map loses the per-sat grouping).
        'e2_tasks_by_sat': {sid: list(ts) for sid, ts in e2_tasks_by_sat.items()},
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
    A_e1 = m.addVars(N1E, K, vtype=GRB.CONTINUOUS, lb=0.0,
                     ub=time_horizon, name="A_e1")
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
                    lb=0.0, ub=time_horizon, name=f"A_e2_{i}_{v}")
                for j in valid_nodes:
                    if i != j:
                        z[i, j, v] = m.addVar(
                            vtype=GRB.BINARY, name=f"z_{i}_{j}_{v}")

    # --- E2 发车/返回时刻（衔接用） ---
    A_start = m.addVars(F, range(vehicles_per_satellite),
                        vtype=GRB.CONTINUOUS, lb=0.0, ub=time_horizon,
                        name="A_start")
    A_return = m.addVars(F, range(vehicles_per_satellite),
                         vtype=GRB.CONTINUOUS, lb=0.0, ub=time_horizon,
                         name="A_return")

    vars_dict = {
        'x': x, 'A_e1': A_e1, 'Q_k': Q_k, 'Vol_k': Vol_k,
        'z': z, 'A_e2': A_e2, 'Q_r': Q_r, 'Vol_r': Vol_r,
        'A_start': A_start, 'A_return': A_return,
    }

    set_objective(m, vars_dict, ctx)
    add_all_constraints(m, problem, e1_tasks, e2_tasks_by_sat, vars_dict, ctx)

    return m, vars_dict, ctx
