# models/model_1/constraints.py
# -*- coding: utf-8 -*-
import gurobipy as gp

def add_all_constraints(m: gp.Model, problem, e1_tasks, e2_tasks_by_sat, vars_dict: dict, ctx: dict):
    """
    添加 Model 1 的所有约束
    """
    x, z = vars_dict['x'], vars_dict['z']
    A_e1, L_e1, W_e1 = vars_dict['A_e1'], vars_dict['L_e1'], vars_dict['W_e1']
    A_e2, L_e2, W_e2, A_start = vars_dict['A_e2'], vars_dict['L_e2'], vars_dict['W_e2'], vars_dict['A_start']
    
    depot_id = problem.depot.id
    F = ctx['F']
    N1E = ctx['N1E']
    K = ctx['K']
    V_by_sat = ctx['V_by_sat']
    get_phys = ctx['get_phys']
    get_st = ctx['get_st']
    time_map = ctx['time_map']

    M = 100000.0  # Big-M

    # ================= E1 约束 =================
    for j in F:
        m.addConstr(gp.quicksum(x[i, j, k] for i in N1E for k in K if i != j) == 1, f"E1_Visit_{j}")
    
    for k in K:
        m.addConstr(gp.quicksum(x[depot_id, j, k] for j in F) <= 1, f"E1_Start_{k}")
        m.addConstr(gp.quicksum(x[i, depot_id, k] for i in F) <= 1, f"E1_End_{k}")
        
        for h in F:
            m.addConstr(
                gp.quicksum(x[i, h, k] for i in N1E if i != h) == gp.quicksum(x[h, j, k] for j in N1E if j != h), 
                f"E1_Flow_{h}_{k}"
            )
        
        for i in N1E:
            for j in F:
                if i == j: continue
                # 时间连续性
                m.addConstr(A_e1[j, k] >= A_e1[i, k] + get_st(i) + time_map[i, j] - M * (1 - x[i, j, k]))
                
                # 载重与体积单调递减
                task_j = next((t for t in e1_tasks if t.id == f"{j}d"), None)
                q_j, w_j = (task_j.w, task_j.v) if task_j else (0, 0)
                m.addConstr(L_e1[j, k] <= L_e1[i, k] - q_j + M * (1 - x[i, j, k]))
                m.addConstr(W_e1[j, k] <= W_e1[i, k] - w_j + M * (1 - x[i, j, k]))
        
        # E1 容量约束
        m.addConstr(L_e1[depot_id, k] <= problem.e1_weight_cap * gp.quicksum(x[depot_id, j, k] for j in F))
        m.addConstr(W_e1[depot_id, k] <= problem.e1_volume_cap * gp.quicksum(x[depot_id, j, k] for j in F))

    # ================= E2 约束 =================
    for f in F:
        tasks_f = e2_tasks_by_sat.get(f, [])
        task_ids = [t.id for t in tasks_f]
        nodes_f = [f] + task_ids
        
        # 每个任务节点被服务一次
        for j_id in task_ids:
            m.addConstr(gp.quicksum(z[i, j_id, v_key] for v_key in V_by_sat[f] for i in nodes_f if i != j_id) == 1)
        
        for v_key in V_by_sat[f]:
            # 起点终点必须是所属前置仓
            m.addConstr(gp.quicksum(z[f, j, v_key] for j in task_ids) == gp.quicksum(z[i, f, v_key] for i in task_ids))
            m.addConstr(gp.quicksum(z[f, j, v_key] for j in task_ids) <= 1)
            
            # 流量守恒
            for h in task_ids:
                m.addConstr(
                    gp.quicksum(z[i, h, v_key] for i in nodes_f if i != h) == gp.quicksum(z[h, j, v_key] for j in nodes_f if j != h)
                )
            
            # 时间、载重、体积递减约束
            for i in nodes_f:
                for j in task_ids:
                    if i == j: continue
                    dist_ij = time_map[i, j]
                    m.addConstr(A_e2[j, v_key] >= A_e2[i, v_key] + get_st(i) + dist_ij - M * (1 - z[i, j, v_key]))
                    
                    t_j = next((t for t in tasks_f if t.id == j), None)
                    if t_j is None:
                        continue
                    m.addConstr(L_e2[j, v_key] <= L_e2[i, v_key] - t_j.w + M * (1 - z[i, j, v_key]))
                    m.addConstr(W_e2[j, v_key] <= W_e2[i, v_key] - t_j.v + M * (1 - z[i, j, v_key]))
            
            # E2 容量约束
            m.addConstr(L_e2[f, v_key] <= problem.e2_weight_cap * gp.quicksum(z[f, j, v_key] for j in task_ids))
            m.addConstr(W_e2[f, v_key] <= problem.e2_volume_cap * gp.quicksum(z[f, j, v_key] for j in task_ids))

    # ================= 一二阶段衔接约束 =================
    for f in F:
        for v_idx in range(ctx['vehicles_per_satellite']):
            v_key = (f, v_idx)
        
            # E1 衔接约束保持不变
            for k in K:
                m.addConstr(
                A_start[f, v_idx] >= A_e1[f, k] + get_st(f) 
                - M * (1 - gp.quicksum(x[i, f, k] for i in N1E if i != f))
                )
        
            tasks_f = e2_tasks_by_sat.get(f, [])
            nodes_f = [f] + [t.id for t in tasks_f]
        
            for t_obj in tasks_f:
            # is_served = 1 当且仅当车 v_key 访问了 t_obj
                is_served = gp.quicksum(
                z[i, t_obj.id, v_key] for i in nodes_f if i != t_obj.id
                )
                abs_time = A_start[f, v_idx] + A_e2[t_obj.id, v_key]
            
            # 只有 is_served=1 时时间窗才有效
                m.addConstr(abs_time >= t_obj.tw[0] - M * (1 - is_served))
                m.addConstr(abs_time <= t_obj.tw[1] + M * (1 - is_served))