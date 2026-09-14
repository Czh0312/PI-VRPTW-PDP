# models/routing/common/exact_utils.py
# -*- coding: utf-8 -*-
"""
精确求解器公共工具函数。
共享的路线提取和载量打印函数。
"""


def extract_routes_e1(sol, depot, tasks, vehicles):
    """返回 [(k, route), ...], route 形如 [depot, n1, n2, ..., depot]"""
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


def extract_routes_e2(sol, sat_id, tasks, vehicles):
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


def print_e1_loads(vars_dict, e1_routes_with_k):
    Q_k = vars_dict["Q_k"]
    Vol_k = vars_dict["Vol_k"]
    for k, route in e1_routes_with_k:
        loads = []
        for idx, node in enumerate(route):
            is_final_depot = (idx == len(route) - 1 and node == route[0])
            if is_final_depot:
                prev = route[idx - 1]
                q_val = Q_k[prev, k].X if (prev, k) in Q_k else 0.0
                v_val = Vol_k[prev, k].X if (prev, k) in Vol_k else 0.0
                loads.append(f"{node}_arr(Q={q_val:.1f},V={v_val:.1f})")
            else:
                q_val = Q_k[node, k].X if (node, k) in Q_k else 0.0
                v_val = Vol_k[node, k].X if (node, k) in Vol_k else 0.0
                loads.append(f"{node}(Q={q_val:.1f},V={v_val:.1f})")
        print(f"    载量(k={k}): {' -> '.join(loads)}")


def print_e2_loads(vars_dict, used_v, routes):
    Q_r = vars_dict["Q_r"]
    Vol_r = vars_dict["Vol_r"]
    for v_key, route in zip(used_v, routes):
        loads = []
        for idx, node in enumerate(route):
            is_final_sat = (idx == len(route) - 1 and node == route[0])
            if is_final_sat:
                prev = route[idx - 1]
                q_val = Q_r[prev, v_key].X if (prev, v_key) in Q_r else 0.0
                v_val = Vol_r[prev, v_key].X if (prev, v_key) in Vol_r else 0.0
                loads.append(f"{node}_arr(Q={q_val:.1f},V={v_val:.1f})")
            else:
                q_val = Q_r[node, v_key].X if (node, v_key) in Q_r else 0.0
                v_val = Vol_r[node, v_key].X if (node, v_key) in Vol_r else 0.0
                loads.append(f"{node}(Q={q_val:.1f},V={v_val:.1f})")
        print(f"      载量: {' -> '.join(loads)}")
