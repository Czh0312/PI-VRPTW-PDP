# models/model_3/solver.py
# -*- coding: utf-8 -*-
"""
统一 Q/V 变量: Q_k/Vol_k (E1), Q_r/Vol_r (E2)
A_start/A_return (衔接), y_trans/dq_trans/dw_trans (中转)
"""
from gurobipy import GRB

from .model import build_transshipment_model


def solve_exact(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
                transship_nodes=None, verbose=True):
    total_w_d = sum(t.w for t in e1_tasks if t.kind == 'd')
    total_w_p = sum(t.w for t in e1_tasks if t.kind == 'p')
    total_v_d = sum(t.v for t in e1_tasks if t.kind == 'd')
    total_v_p = sum(t.v for t in e1_tasks if t.kind == 'p')
    print(f"\n[DEBUG] E1 任务总览:")
    print(f"  卡车数 K = {problem.e1_vehicle_count}")
    print(f"  单车 weight cap = {problem.e1_weight_cap}")
    print(f"  单车 volume cap = {problem.e1_volume_cap}")
    print(f"  total delivery weight = {total_w_d}, volume = {total_v_d}")
    print(f"  total pickup  weight = {total_w_p}, volume = {total_v_p}")
    for t in e1_tasks:
        print(f"    {t.id}: kind={t.kind}, w={t.w}, v={t.v}, tw={t.tw}")

    e2_total_d = sum(
        t.w for tasks in e2_tasks_by_sat.values()
        for t in tasks if t.kind == 'd')
    e2_total_p = sum(
        t.w for tasks in e2_tasks_by_sat.values()
        for t in tasks if t.kind == 'p')
    print(f"\n[DEBUG] E2 任务总览:")
    print(f"  每卫星车辆数 = {vehicles_per_satellite}")
    print(f"  单车 weight cap = {problem.e2_weight_cap}")
    print(f"  单车 volume cap = {problem.e2_volume_cap}")
    print(f"  total delivery weight = {e2_total_d}, pickup weight = {e2_total_p}")
    for sat_id, tasks in e2_tasks_by_sat.items():
        print(f"  satellite {sat_id}: {len(tasks)} tasks")
        for t in tasks:
            print(f"    {t.id}: kind={t.kind}, w={t.w}, v={t.v}")

    m, vars_dict, ctx = build_transshipment_model(
        problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
        transship_nodes=transship_nodes
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
        m.write("test_m3_iis.ilp")
        print("✓ IIS 已写入 test_m3_iis.ilp")

    print("\n未找到可行解")
    return None, None


def _print_solution(m, vars_dict, ctx, e2_tasks_by_sat, problem):
    print("\n" + "=" * 60)
    print(f"求解完成!  目标值: {m.ObjVal:.2f}")
    print("=" * 60)

    depot_id = ctx["depot_id"]
    K = ctx["K"]
    F = ctx["F"]
    C1 = ctx["C1"]

    sol_x = m.getAttr("X", vars_dict["x"])
    sol_z = {k: v.X for k, v in vars_dict["z"].items()}

    # ============ E1 诊断 ============
    print("\n[DEBUG] 所有被激活的 E1 弧 (x > 0.5):")
    for (i, j, k), val in sol_x.items():
        if val > 0.5:
            print(f"  x[{i:>6}, {j:>6}, k={k}] = {val:.2f}")
    print("[DEBUG] E1 弧总数:", sum(1 for v in sol_x.values() if v > 0.5))

    print("\n[DEBUG] 所有卡车的 D0 初始载量 (只显示非零):")
    Q_k = vars_dict["Q_k"]
    Vol_k = vars_dict["Vol_k"]
    any_loaded = False
    for k in K:
        q = Q_k[depot_id, k].X
        v = Vol_k[depot_id, k].X
        if q > 0.01 or v > 0.01:
            print(f"  卡车 {k}: Q={q:.1f}, V={v:.1f}")
            any_loaded = True
    if not any_loaded:
        print("  (所有卡车初始载量都是 0)")

    # ============ E1 路线 ============
    print("\n--- E1 路线 ---")
    e1_routes_with_k = _extract_routes_e1(sol_x, depot_id, C1, K)
    for k, route in e1_routes_with_k:
        print(f"  卡车 {k}: {' -> '.join(route)}")
    _print_e1_loads(vars_dict, e1_routes_with_k)

    # ============ E2 路线 ============
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

    # ============ 中转诊断 ============
    if vars_dict["y_trans"]:
        print("\n[DEBUG] 中转活动:")
        any_trans = False
        for (i, v1, v2), y_var in vars_dict["y_trans"].items():
            if y_var.X > 0.5:
                dq = vars_dict["dq_trans"].get((i, v1, v2))
                dw = vars_dict["dw_trans"].get((i, v1, v2))
                print(f"  y[{i}, {v1}->{v2}]: Δq={dq.X:.1f}, Δw={dw.X:.1f}")
                any_trans = True
        if not any_trans:
            print("  (无中转活动)")

    # ============ 衔接时间 ============
    A_start = vars_dict["A_start"]
    A_return = vars_dict["A_return"]
    print("\n[DEBUG] 衔接时间:")
    for f in F:
        for v_idx in range(ctx["vehicles_per_satellite"]):
            s = A_start[f, v_idx].X
            r = A_return[f, v_idx].X
            if s > 0.01 or r > 0.01:
                print(f"  sat {f}, 车 {v_idx}: start={s:.1f}, return={r:.1f}")

    e1_routes = [r for _, r in e1_routes_with_k]
    return e1_routes, out_e2_routes


def _print_e1_loads(vars_dict, e1_routes_with_k):
    Q_k = vars_dict["Q_k"]
    Vol_k = vars_dict["Vol_k"]
    for k, route in e1_routes_with_k:
        loads = []
        for idx, node in enumerate(route):
            is_first = (idx == 0)
            is_last = (idx == len(route) - 1 and node == route[0])
            if is_last:
                prev = route[idx - 1]
                q = Q_k[prev, k].X if (prev, k) in Q_k else 0.0
                v = Vol_k[prev, k].X if (prev, k) in Vol_k else 0.0
                loads.append(f"{node}_arr(Q={q:.1f},V={v:.1f})")
            elif is_first:
                q = Q_k[node, k].X if (node, k) in Q_k else 0.0
                v = Vol_k[node, k].X if (node, k) in Vol_k else 0.0
                loads.append(f"{node}(Q={q:.1f},V={v:.1f})")
            else:
                q = Q_k[node, k].X if (node, k) in Q_k else 0.0
                v = Vol_k[node, k].X if (node, k) in Vol_k else 0.0
                loads.append(f"{node}(Q={q:.1f},V={v:.1f})")
        print(f"    载量(k={k}): {' -> '.join(loads)}")


def _print_e2_loads(vars_dict, used_v, routes):
    Q_r = vars_dict["Q_r"]
    Vol_r = vars_dict["Vol_r"]
    for v_key, route in zip(used_v, routes):
        loads = []
        for idx, node in enumerate(route):
            is_first = (idx == 0)
            is_last = (idx == len(route) - 1 and node == route[0])
            if is_last:
                prev = route[idx - 1]
                q = Q_r[prev, v_key].X if (prev, v_key) in Q_r else 0.0
                v = Vol_r[prev, v_key].X if (prev, v_key) in Vol_r else 0.0
                loads.append(f"{node}_arr(Q={q:.1f},V={v:.1f})")
            elif is_first:
                q = Q_r[node, v_key].X if (node, v_key) in Q_r else 0.0
                v = Vol_r[node, v_key].X if (node, v_key) in Vol_r else 0.0
                loads.append(f"{node}(Q={q:.1f},V={v:.1f})")
            else:
                q = Q_r[node, v_key].X if (node, v_key) in Q_r else 0.0
                v = Vol_r[node, v_key].X if (node, v_key) in Vol_r else 0.0
                loads.append(f"{node}(Q={q:.1f},V={v:.1f})")
        print(f"      载量: {' -> '.join(loads)}")


def _extract_routes_e1(sol, depot, tasks, vehicles):
    routes = []
    nodes = [depot] + tasks
    for k in vehicles:
        succ = {
            i: j
            for i in nodes for j in nodes
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
            for i in nodes for j in nodes
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
