# models/routing/variants/model_1/model.py
# -*- coding: utf-8 -*-
import gurobipy as gp
from gurobipy import GRB

from .formulation.constraints import add_all_constraints
from .formulation.objective import set_objective


def build_model(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite):
    depot_id = problem.depot.id
    # Model 1 只应访问有配送需求的卫星；否则 exact 会为零需求卫星
    # 强制生成无意义访问。
    active_sats = {t.cust for t in e1_tasks}
    active_sats.update(
        sat_id for sat_id, tasks in e2_tasks_by_sat.items() if tasks
    )
    f_set = [s.id for s in problem.satellites if s.id in active_sats]

    task_to_phys = {t.id: t.cust for t in e1_tasks}
    for sat_id, tasks in e2_tasks_by_sat.items():
        for task in tasks:
            task_to_phys[task.id] = task.cust

    def get_phys(node_id: str) -> str:
        return task_to_phys.get(node_id, node_id)

    def get_st(node_id: str) -> float:
        phys_id = get_phys(node_id)
        if phys_id == depot_id:
            return 0.0
        node_obj = problem.nodes.get(phys_id)
        return getattr(node_obj, "service_time", 0.0)

    # 构建时间映射表（与 Model 3/5 保持一致）
    all_node_ids = list(set(
        [depot_id] + f_set +
        [t.id for t in e1_tasks] +
        [t.id for tasks in e2_tasks_by_sat.values() for t in tasks]
    ))
    time_map = {}
    for i in all_node_ids:
        for j in all_node_ids:
            if i == j:
                time_map[i, j] = 0.0
            else:
                time_map[i, j] = problem.travel_time(get_phys(i), get_phys(j))

    m = gp.Model("2E_VRPTW_Pure_Delivery")
    k_set = list(range(problem.e1_vehicle_count))
    n1e = [depot_id] + f_set
    v_by_sat = {
        sat_id: [(sat_id, v_idx) for v_idx in range(vehicles_per_satellite)] for sat_id in f_set
    }

    ctx = {
        "F": f_set,
        "N1E": n1e,
        "K": k_set,
        "V_by_sat": v_by_sat,
        "get_phys": get_phys,
        "get_st": get_st,
        "time_map": time_map,
        "vehicles_per_satellite": vehicles_per_satellite,
    }

    x = m.addVars(n1e, n1e, k_set, vtype=GRB.BINARY, name="x")
    a_e1 = m.addVars(n1e, k_set, lb=0, name="A_e1")
    l_e1 = m.addVars(n1e, k_set, lb=0, name="L_e1")
    w_e1 = m.addVars(n1e, k_set, lb=0, name="W_e1")

    z, a_e2, l_e2, w_e2 = {}, {}, {}, {}
    a_start = m.addVars(f_set, range(vehicles_per_satellite), lb=0, name="A_start")
    for sat_id in f_set:
        nodes_f = [sat_id] + [t.id for t in e2_tasks_by_sat.get(sat_id, [])]
        for v_key in v_by_sat[sat_id]:
            for i in nodes_f:
                l_e2[i, v_key] = m.addVar(lb=0, name=f"L2_{i}_{v_key}")
                w_e2[i, v_key] = m.addVar(lb=0, name=f"W2_{i}_{v_key}")
                a_e2[i, v_key] = m.addVar(lb=0, name=f"A2_{i}_{v_key}")
                for j in nodes_f:
                    if i != j:
                        z[i, j, v_key] = m.addVar(vtype=GRB.BINARY, name=f"z_{i}_{j}_{v_key}")

    vars_dict = {
        "x": x,
        "A_e1": a_e1,
        "L_e1": l_e1,
        "W_e1": w_e1,
        "z": z,
        "A_e2": a_e2,
        "L_e2": l_e2,
        "W_e2": w_e2,
        "A_start": a_start,
    }

    set_objective(m, problem, vars_dict, ctx)
    add_all_constraints(m, problem, e1_tasks, e2_tasks_by_sat, vars_dict, ctx)
    return m, vars_dict, ctx
