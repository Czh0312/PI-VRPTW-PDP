# models/model_2/solver.py
# -*- coding: utf-8 -*-
"""
适配文档2变量命名: Q_k, Vol_k, Q_r, Vol_r
"""
from gurobipy import GRB

from .model import build_unified_pdp_model


def solve_exact(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                TFp=9999.0, verbose=True):
    # ============ 诊断打印 ============
    total_w = sum(t.w for t in e1_tasks if t.kind == 'd')
    total_v = sum(t.v for t in e1_tasks if t.kind == 'd')
    print(f"\n[DEBUG] E1 任务总览:")
    print(f"  卡车数 K = {problem.e1_vehicle_count}")
    print(f"  单车 weight cap = {problem.e1_weight_cap}")
    print(f"  单车 volume cap = {problem.e1_volume_cap}")
    print(f"  total delivery weight = {total_w}")
    print(f"  total delivery volume = {total_v}")
    for t in e1_tasks:
        print(f"    {t.id}: kind={t.kind}, w={t.w}, v={t.v}")

    m, vars_dict, ctx = build_unified_pdp_model(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite, TFp=TFp
    )
    if not verbose:
        m.setParam("OutputFlag", 0)

    m.setParam("MIPGap", 0.0)
    m.setParam("TimeLimit", 300)
    m.optimize()

    if m.status == GRB.OPTIMAL:
        return _print_solution(m, vars_dict, ctx, e2_tasks_by_sat, problem)

    if m.status == GRB.TIME_LIMIT and m.SolCount > 0:
        print(f"\n达到时间限制, 当前最优解: {m.ObjVal:.2f}  (Gap: {m.MIPGap:.2%})")
        return _print_solution(m, vars_dict, ctx, e2_tasks_by_sat, problem)

    if m.status == GRB.INFEASIBLE:
        print("\n模型不可行，正在计算 IIS...")
        m.computeIIS()
        m.write("test_m2_iis.ilp")
        print("✓ IIS 已写入 test_m2_iis.ilp")

    print("\n未找到可行解")
    return None, None


def _print_solution(m, vars_dict, ctx, e2_tasks_by_sat, problem):
    print("\n" + "=" * 60)
    print(f"求解完成!  目标值: {m.ObjVal:.2f}")
    print("=" * 60)

    # 先把 ctx 里要用的东西解包出来
    depot_id = ctx["depot_id"]
    K = ctx["K"]
    F = ctx["F"]
    N_E1_Tasks = ctx["N_E1_Tasks"]

    sol_x = m.getAttr("X", vars_dict["x"])
    sol_z = {k: v.X for k, v in vars_dict["z"].items()}

    # ============ 诊断打印 ============
    print("\n[DEBUG] 所有被激活的 E1 弧 (x > 0.5):")
    for (i, j, k), val in sol_x.items():
        if val > 0.5:
            print(f"  x[{i:>6}, {j:>6}, k={k}] = {val:.2f}")
    print("[DEBUG] E1 弧总数:", sum(1 for v in sol_x.values() if v > 0.5))

    print("\n[DEBUG] 所有卡车的 D0 初始载量 (只显示非零):")
    Q_k_var = vars_dict["Q_k"]
    Vol_k_var = vars_dict["Vol_k"]
    any_loaded = False
    for k in K:
        q = Q_k_var[depot_id, k].X
        v = Vol_k_var[depot_id, k].X
        if q > 0.01 or v > 0.01:
            print(f"  卡车 {k}: Q={q:.1f}, V={v:.1f}")
            any_loaded = True
    if not any_loaded:
        print("  (所有卡车初始载量都是 0 —— 这肯定有问题!)")

    # ============ E1 路线提取与打印 ============
    print("\n--- E1 路线 ---")
    e1_routes_with_k = _extract_routes_e1(sol_x, depot_id, N_E1_Tasks, K)
    for k, route in e1_routes_with_k:
        print(f"  卡车 {k}: {' -> '.join(route)}")
    _print_e1_loads(vars_dict, e1_routes_with_k)

    # ============ E2 路线提取与打印 ============
    out_e2_routes = {f: [] for f in F}
    print("\n--- E2 路线 ---")
    for sat_id in F:
        vehs = ctx["V_by_sat"][sat_id]
        t_ids = [t.id for t in e2_tasks_by_sat.get(sat_id, [])]
        used_v = [
            v for v in vehs
            if sum(sol_z.get((sat_id, j, v), 0) for j in t_ids) > 0.5
        ]
        if not used_v:
            continue
        print(f"  前置仓 {sat_id}:")
        routes = _extract_routes_e2(sol_z, sat_id, t_ids, used_v)
        out_e2_routes[sat_id] = routes

        for v_key, route in zip(used_v, routes):
            print(f"    车辆{v_key[1]}: {' -> '.join(route)}")
        _print_e2_loads(vars_dict, used_v, routes)

    # 返回纯 route 列表以兼容 main.py 的绘图
    e1_routes = [r for _, r in e1_routes_with_k]
    return e1_routes, out_e2_routes


def _print_e1_loads(vars_dict, e1_routes_with_k):
    """打印 E1 载量。
    注意: Q_k[i,k] 表示"离开节点 i 时"的载量。
    路径终点是 depot，此时车上装的是回收总量，需要从上一个节点的 Q_k 推出。
    """
    Q_k = vars_dict["Q_k"]
    Vol_k = vars_dict["Vol_k"]
    for k, route in e1_routes_with_k:
        loads = []
        for idx, node in enumerate(route):
            is_final_depot = (idx == len(route) - 1 and node == route[0])
            if is_final_depot:
                # 终点 depot: 展示"到达时"的载量 = 上一个节点离开时的载量
                prev = route[idx - 1]
                q_val = Q_k[prev, k].X if (prev, k) in Q_k else 0.0
                v_val = Vol_k[prev, k].X if (prev, k) in Vol_k else 0.0
                loads.append(f"{node}_arr(Q={q_val:.1f},V={v_val:.1f})")
            else:
                q_val = Q_k[node, k].X if (node, k) in Q_k else 0.0
                v_val = Vol_k[node, k].X if (node, k) in Vol_k else 0.0
                loads.append(f"{node}(Q={q_val:.1f},V={v_val:.1f})")
        print(f"    载量(k={k}): {' -> '.join(loads)}")


def _print_e2_loads(vars_dict, used_v, routes):
    """打印 E2 载量。
    注意: Q_r[i,v] 表示"离开节点 i 时"的载量。
    路径终点是 satellite，此时车上装的是回收总量，需要从上一个节点的 Q_r 推出。
    """
    Q_r = vars_dict["Q_r"]
    Vol_r = vars_dict["Vol_r"]
    for v_key, route in zip(used_v, routes):
        loads = []
        for idx, node in enumerate(route):
            is_final_sat = (idx == len(route) - 1 and node == route[0])
            if is_final_sat:
                # 终点 satellite: 展示"到达时"的载量 = 上一个节点离开时的载量
                prev = route[idx - 1]
                q_val = Q_r[prev, v_key].X if (prev, v_key) in Q_r else 0.0
                v_val = Vol_r[prev, v_key].X if (prev, v_key) in Vol_r else 0.0
                loads.append(f"{node}_arr(Q={q_val:.1f},V={v_val:.1f})")
            else:
                q_val = Q_r[node, v_key].X if (node, v_key) in Q_r else 0.0
                v_val = Vol_r[node, v_key].X if (node, v_key) in Vol_r else 0.0
                loads.append(f"{node}(Q={q_val:.1f},V={v_val:.1f})")
        print(f"      载量: {' -> '.join(loads)}")


def _extract_routes_e1(sol, depot, tasks, vehicles):
    """返回 [(k, route), ...], route 形如 [depot, n1, n2, ..., depot]"""
    routes = []
    nodes = [depot] + tasks
    for k in vehicles:
        succ = {
            i: j
            for i in nodes
            for j in nodes
            if i != j and sol.get((i, j, k), 0) > 0.5
        }
        cur = succ.get(depot)
        if not cur:
            continue
        route, visited = [depot], {depot}
        while cur and cur != depot:
            route.append(cur)
            visited.add(cur)
            cur = succ.get(cur)
            if cur in visited and cur != depot:
                break
        if cur == depot:
            route.append(depot)
        if len(route) > 2:
            routes.append((k, route))
    return routes


def _extract_routes_e2(sol, sat_id, tasks, vehicles):
    routes = []
    nodes = [sat_id] + tasks
    for v_key in vehicles:
        succ = {
            i: j
            for i in nodes
            for j in nodes
            if i != j and sol.get((i, j, v_key), 0) > 0.5
        }
        cur = succ.get(sat_id)
        if not cur:
            continue
        route, visited = [sat_id], {sat_id}
        while cur and cur != sat_id:
            route.append(cur)
            visited.add(cur)
            cur = succ.get(cur)
            if cur in visited and cur != sat_id:
                break
        if cur == sat_id:
            route.append(sat_id)
        if len(route) > 2:
            routes.append(route)
    return routes