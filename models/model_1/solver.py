# models/model_1/solver.py
# -*- coding: utf-8 -*-
from gurobipy import GRB
from .model import build_pure_delivery_model

def solve_exact(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite, verbose=True):
    """
    运行精确求解器并解析输出
    """
    m, vars_dict, ctx = build_pure_delivery_model(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite)
    
    if not verbose:
        m.setParam('OutputFlag', 0)

    if verbose:
        print("\n" + "=" * 60)
        print("求解器配置: 精确最优解模式 (Model 1 纯配送)")
        print("=" * 60)
        print("✓ MIPGap = 0.0 (要求精确最优解)")
        print("✓ 时间限制 300 秒")
        print("=" * 60)

    m.setParam('MIPGap', 0.0) 
    m.setParam('TimeLimit', 300)
    m.optimize()

    if m.status == GRB.OPTIMAL:
        # 修正：捕获 _print_solution 返回的路径数据并 return
        e1_routes, e2_routes_out = _print_solution(m, problem, vars_dict, ctx, e2_tasks_by_sat)
        return e1_routes, e2_routes_out
    else:
        print("\n✗ 未找到证明最优的解")
        if m.status == GRB.INFEASIBLE:
            print("模型不可行，正在计算 IIS（可能需要几秒到几十秒）...")
            m.computeIIS()
            m.write("test3_iis.ilp")
            print("✓ IIS 已写入 test3_iis.ilp")
        return None, None

def _print_solution(m, problem, vars_dict, ctx, e2_tasks_by_sat):
    """解析并打印 Gurobi 的结果，同时返回结构化路径供绘图"""
    print("\n" + "=" * 60)
    print("✓ 求解成功! 已证明最优性")
    print(f"最优目标值: {m.ObjVal:.2f}")
    print(f"求解时间: {m.Runtime:.2f} 秒")
    print("=" * 60)
    
    x = vars_dict['x']
    z = vars_dict['z']
    sol_x = m.getAttr('X', x)
    sol_z = m.getAttr('X', z)
    
    depot_id = problem.depot.id
    F = ctx['F']
    N1E = ctx['N1E']
    K = ctx['K']
    vehicles_per_satellite = ctx['vehicles_per_satellite']

    # 用于返回给外部绘图的数据结构
    out_e1_routes = []
    out_e2_routes = {f: [] for f in F}

    # 1. 提取 E1 路线
    print("\n--- E1 路线 ---")
    for k in K:
        route = [depot_id]
        cur = next((j for j in F if sol_x.get((depot_id, j, k), 0) > 0.5), None)
        visited = {depot_id}
        
        while cur and cur not in visited:
            route.append(cur)
            visited.add(cur)
            next_node = next((j for j in N1E if cur != j and sol_x.get((cur, j, k), 0) > 0.5), None)
            if next_node == depot_id or not next_node:
                route.append(depot_id)
                break
            cur = next_node
        
        if len(route) > 2:
            print(f"卡车{k}: {' → '.join(route)}")
            out_e1_routes.append(route)

    # 2. 提取 E2 路线
    print("\n" + "=" * 60)
    print("E2 前置仓独立车队统计")
    print("=" * 60)
    
    total_e2_used = 0
    for f in F:
        tasks_f = e2_tasks_by_sat.get(f, [])
        task_ids = [t.id for t in tasks_f]
        used_v = []
        
        for v_idx in range(vehicles_per_satellite):
            v_key = (f, v_idx)
            if any(sol_z.get((f, j, v_key), 0) > 0.5 for j in task_ids):
                used_v.append(v_idx)
        
        print(f"\n【前置仓 {f}】")
        print(f"  配置车辆数: {vehicles_per_satellite} 辆")
        print(f"  实际使用数: {len(used_v)} 辆")
        
        if used_v:
            print(f"  使用率: {len(used_v) / vehicles_per_satellite * 100:.1f}%")
            for v_idx in used_v:
                v_key = (f, v_idx)
                route = [f]
                cur = next((j for j in task_ids if sol_z.get((f, j, v_key), 0) > 0.5), None)
                visited = {f}
                
                while cur and cur not in visited:
                    route.append(cur)
                    visited.add(cur)
                    next_node = next((j for j in [f] + task_ids if cur != j and sol_z.get((cur, j, v_key), 0) > 0.5), None)
                    if next_node == f or not next_node:
                        route.append(f)
                        break
                    cur = next_node
                
                print(f"  车辆{v_idx}: {' → '.join(route)}")
                out_e2_routes[f].append(route)
                
        total_e2_used += len(used_v)
    
    # 3. 统计汇总
    total_e2_veh = len(F) * vehicles_per_satellite
    print("\n" + "-" * 60)
    print(f"E2总体利用率: {total_e2_used / total_e2_veh * 100:.1f}%")
    print("=" * 60)

    # 返回提取好的路径
    return out_e1_routes, out_e2_routes