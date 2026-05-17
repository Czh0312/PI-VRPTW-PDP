# models/model_3/alns_solver.py
# -*- coding: utf-8 -*-
"""
Model 3 (取送货 + 中转) 的 ALNS 求解器
在 Model 2 基础上扩展：
  - E1/E2 共享容量: L+R ≤ Q, W+V ≤ C^vol
  - E2 中转: 改变 pickup 节点的取货物理位置，减少总距离
  - 中转在 ALNS 迭代中搜索，配合 transshipment_destroy 撤销

用法：
    from models.model_3.alns_solver import solve_alns
    e1_routes, e2_routes = solve_alns(problem, e1_tasks, e2_tasks_by_sat,
                                       vehicles_per_satellite)
"""
import math
import random
import copy
import time


# =====================================================================
#  数据结构
# =====================================================================

class Route:
    def __init__(self, base_id, nodes=None):
        self.base = base_id
        self.nodes = nodes or []
        # 虚拟搬运节点载量: {virtual_node_id: (w, v)}
        self.transport_loads = {}

    def full_path(self):
        return [self.base] + self.nodes + [self.base]

    def copy(self):
        r = Route(self.base, list(self.nodes))
        r.transport_loads = dict(self.transport_loads)
        return r


class Solution:
    def __init__(self):
        self.e1_routes = []
        self.e2_routes = {}
        self.cost = float('inf')
        # 中转记录: {(sat_id, beneficiary_idx, pick_node, new_phys):
        #   (old_phys, carrier_idx, container_w, container_v)}
        # beneficiary 取货车: pick_node 位置从 old_phys 改为 new_phys
        # carrier 搬运车: 在路线中插入 TRANS_PICK_old_phys 和 TRANS_DROP_new_phys
        self.transshipments = {}

    def copy(self):
        s = Solution()
        s.e1_routes = [r.copy() for r in self.e1_routes]
        s.e2_routes = {
            sat: [r.copy() for r in routes]
            for sat, routes in self.e2_routes.items()
        }
        s.cost = self.cost
        s.transshipments = dict(self.transshipments)
        return s


# =====================================================================
#  评估器 (Model 3 共享容量 + 中转感知)
# =====================================================================

class Evaluator:
    def __init__(self, problem, e1_tasks, e2_tasks_by_sat,
                 vehicles_per_satellite=2):
        self.problem = problem
        self.vehicles_per_satellite = vehicles_per_satellite
        self.e1_task_map = {t.id: t for t in e1_tasks}
        self.e2_task_map = {}
        self.e2_tasks_by_sat = e2_tasks_by_sat
        for sat_id, tasks in e2_tasks_by_sat.items():
            for t in tasks:
                self.e2_task_map[t.id] = t

        self.task_to_phys = {}
        for t in e1_tasks:
            self.task_to_phys[t.id] = t.cust
        for tasks in e2_tasks_by_sat.values():
            for t in tasks:
                self.task_to_phys[t.id] = t.cust

        self.e1_del_nodes = [t.id for t in e1_tasks if t.kind == 'd']
        self.e1_pick_nodes = [t.id for t in e1_tasks if t.kind == 'p']

        # 禁止同卫星 pickup→delivery (必须先送货后取货)
        self.forbidden_e1 = set()
        sat_ids = [s.id for s in problem.satellites]
        for s_id in sat_ids:
            pid, did = f"{s_id}p", f"{s_id}d"
            if pid in self.e1_task_map and did in self.e1_task_map:
                self.forbidden_e1.add((pid, did))

    def signed_demand(self, task):
        """配送为正, 回收为负"""
        return task.w if task.kind == 'd' else -task.w

    def signed_volume(self, task):
        return task.v if task.kind == 'd' else -task.v

    def phys(self, node_id):
        return self.task_to_phys.get(node_id, node_id)

    def dist(self, a, b):
        return self.problem.distance(self.phys(a), self.phys(b))

    def service_time(self, node_id):
        phys_id = self.phys(node_id)
        node_obj = self.problem.nodes.get(phys_id)
        return getattr(node_obj, 'service_time', 0.0) if node_obj else 0.0

    def route_distance(self, route: Route):
        path = route.full_path()
        total = 0.0
        for i in range(len(path) - 1):
            total += self.dist(path[i], path[i + 1])
        return total

    def total_cost(self, sol: Solution):
        cost = 0.0
        for r in sol.e1_routes:
            cost += self.route_distance(r)
        for sat_id, routes in sol.e2_routes.items():
            for r in routes:
                cost += self.route_distance(r)
        return cost

    def check_e1_route_feasible(self, route: Route):
        """E1 统一 Q/V 可行性: 0 ≤ Q ≤ cap, 0 ≤ V ≤ cap + 禁止 p→d"""
        path = route.full_path()
        for i in range(len(path) - 1):
            if (path[i], path[i + 1]) in self.forbidden_e1:
                return False

        # 初始装载 = 路径上所有 delivery 的总需求
        init_w = 0.0
        init_v = 0.0
        for nid in route.nodes:
            t = self.e1_task_map.get(nid)
            if t and t.kind == 'd':
                init_w += t.w
                init_v += t.v

        if init_w > self.problem.e1_weight_cap + 1e-6:
            return False
        if init_v > self.problem.e1_volume_cap + 1e-6:
            return False

        Q_load, V_load = init_w, init_v
        current_time = 0.0
        prev = route.base

        for nid in route.nodes:
            travel = self.dist(prev, nid)
            current_time += travel
            t = self.e1_task_map.get(nid)
            if t:
                if current_time < t.tw[0]:
                    current_time = t.tw[0]
                if current_time > t.tw[1] + 1e-6:
                    return False

                Q_load -= self.signed_demand(t)
                V_load -= self.signed_volume(t)

                if Q_load < -1e-6 or Q_load > self.problem.e1_weight_cap + 1e-6:
                    return False
                if V_load < -1e-6 or V_load > self.problem.e1_volume_cap + 1e-6:
                    return False

                current_time += self.service_time(nid)
            prev = nid
        return True

    def check_e2_route_feasible(self, route: Route, sat_id):
        """E2 可行性检查（载量 + 时间窗），支持虚拟搬运节点"""
        init_w = 0.0
        init_v = 0.0
        for nid in route.nodes:
            if nid.startswith("TRANS_"):
                continue
            t = self.e2_task_map.get(nid)
            if t and t.kind == 'd':
                init_w += t.w
                init_v += t.v

        if init_w > self.problem.e2_weight_cap + 1e-6:
            return False
        if init_v > self.problem.e2_volume_cap + 1e-6:
            return False

        Q_load, V_load = init_w, init_v
        current_time = 0.0
        prev = sat_id

        for nid in route.nodes:
            travel = self.dist(prev, nid)
            current_time += travel

            if nid.startswith("TRANS_PICK_"):
                tw, tv = route.transport_loads.get(nid, (0.0, 0.0))
                Q_load += tw
                V_load += tv
                if Q_load > self.problem.e2_weight_cap + 1e-6:
                    return False
                if V_load > self.problem.e2_volume_cap + 1e-6:
                    return False
                current_time += self.service_time(nid)
            elif nid.startswith("TRANS_DROP_"):
                tw, tv = route.transport_loads.get(nid, (0.0, 0.0))
                Q_load -= tw
                V_load -= tv
                if Q_load < -1e-6:
                    return False
                if V_load < -1e-6:
                    return False
                current_time += self.service_time(nid)
            else:
                t = self.e2_task_map.get(nid)
                if t:
                    if current_time < t.tw[0]:
                        current_time = t.tw[0]
                    if current_time > t.tw[1] + 1e-6:
                        return False

                    Q_load -= self.signed_demand(t)
                    V_load -= self.signed_volume(t)

                    if Q_load < -1e-6 or Q_load > self.problem.e2_weight_cap + 1e-6:
                        return False
                    if V_load < -1e-6 or V_load > self.problem.e2_volume_cap + 1e-6:
                        return False

                    current_time += self.service_time(nid)
            prev = nid
        return True

    def is_feasible(self, sol: Solution):
        visited_e1 = set()
        for r in sol.e1_routes:
            if not self.check_e1_route_feasible(r):
                return False
            visited_e1.update(r.nodes)
        for tid in self.e1_task_map:
            if tid not in visited_e1:
                return False

        for sat_id, routes in sol.e2_routes.items():
            visited_e2 = set()
            for r in routes:
                if not self.check_e2_route_feasible(r, sat_id):
                    return False
                visited_e2.update(n for n in r.nodes
                                  if not n.startswith("TRANS_"))
            required = {t.id for t in self.e2_tasks_by_sat.get(sat_id, [])}
            if visited_e2 != required:
                return False
        return True


# =====================================================================
#  初始解构造
# =====================================================================

def build_initial_solution(problem, e1_tasks, e2_tasks_by_sat, evaluator):
    sol = Solution()
    depot_id = problem.depot.id
    sat_ids = [s.id for s in problem.satellites]

    # ---- E1 ----
    del_tasks = [t for t in e1_tasks if t.kind == 'd']
    pick_tasks = [t for t in e1_tasks if t.kind == 'p']

    all_nodes = [t.id for t in del_tasks] + [t.id for t in pick_tasks]
    single_route = Route(depot_id, all_nodes)
    if evaluator.check_e1_route_feasible(single_route):
        sol.e1_routes = [single_route]
    else:
        remaining_del = list(del_tasks)
        remaining_del.sort(key=lambda t: evaluator.dist(depot_id, t.cust))
        routes = []
        while remaining_del:
            route = Route(depot_id)
            cur_w, cur_v = 0.0, 0.0
            to_remove = []
            for t in remaining_del:
                if (cur_w + t.w <= problem.e1_weight_cap and
                        cur_v + t.v <= problem.e1_volume_cap):
                    route.nodes.append(t.id)
                    cur_w += t.w
                    cur_v += t.v
                    to_remove.append(t)
            for t in to_remove:
                remaining_del.remove(t)
            if route.nodes:
                routes.append(route)
            elif remaining_del:
                forced = remaining_del.pop(0)
                routes.append(Route(depot_id, [forced.id]))

        for t in pick_tasks:
            inserted = False
            for r in routes:
                r.nodes.append(t.id)
                if evaluator.check_e1_route_feasible(r):
                    inserted = True
                    break
                r.nodes.pop()
            if not inserted:
                routes.append(Route(depot_id, [t.id]))
        sol.e1_routes = routes

    # ---- E2 ----
    max_v = evaluator.vehicles_per_satellite
    for sat_id in sat_ids:
        tasks = list(e2_tasks_by_sat.get(sat_id, []))
        if not tasks:
            sol.e2_routes[sat_id] = []
            continue

        del_t = [t for t in tasks if t.kind == 'd']
        pick_t = [t for t in tasks if t.kind == 'p']
        del_t.sort(key=lambda t: t.tw[0])
        pick_t.sort(key=lambda t: t.tw[0])

        all_sorted = del_t + pick_t
        routes = []
        unassigned = list(all_sorted)

        while unassigned and len(routes) < max_v:
            route = Route(sat_id)
            assigned = []
            for t in unassigned:
                test_route = Route(sat_id, route.nodes + [t.id])
                if evaluator.check_e2_route_feasible(test_route, sat_id):
                    route.nodes.append(t.id)
                    assigned.append(t)
            for t in assigned:
                unassigned.remove(t)
            if route.nodes:
                routes.append(route)
            elif unassigned:
                forced = unassigned.pop(0)
                routes.append(Route(sat_id, [forced.id]))

        # 剩余未分配任务强制插入现有路线 (已达车辆上限)
        for t in unassigned:
            best_r, best_pos, best_cost = None, 0, float('inf')
            for r in routes:
                for pos in range(len(r.nodes) + 1):
                    old_nodes = r.nodes[:]
                    r.nodes.insert(pos, t.id)
                    c = evaluator.route_distance(r)
                    r.nodes = old_nodes
                    if c < best_cost:
                        best_cost, best_r, best_pos = c, r, pos
            if best_r:
                best_r.nodes.insert(best_pos, t.id)
            else:
                routes.append(Route(sat_id, [t.id]))

        sol.e2_routes[sat_id] = routes

    sol.cost = evaluator.total_cost(sol)
    return sol


# =====================================================================
#  中转优化算子（搬运车取放 + 取货车改位置）
# =====================================================================

def optimize_transshipment(sol, evaluator):
    """中转：搬运车绕路取放容器 + 取货车改取货位置。

    对每个 pickup 节点 Cp_X（位置 X），找搬运车 V1 在路线中
    插入"取容器@X"和"放容器@Y"，同时取货车 V2 的 Cp_X
    位置从 X 改为 Y。净收益 = V2 节省 - V1 绕路代价。
    """
    best_plan = None  # (net_saving, sat_id, ben_idx, pick_node, new_phys,
                      #  carrier_idx, pick_pos, drop_pos)

    relocated = {k[2] for k in sol.transshipments}

    for sat_id, routes in sol.e2_routes.items():
        all_tasks = evaluator.e2_tasks_by_sat.get(sat_id, [])
        all_phys = list(set(t.cust for t in all_tasks))

        for ben_idx, route in enumerate(routes):
            for pick_node in route.nodes:
                if pick_node.startswith("TRANS_"):
                    continue
                t = evaluator.e2_task_map.get(pick_node)
                if not t or t.kind != 'p':
                    continue
                if pick_node in relocated:
                    continue

                old_phys = evaluator.phys(pick_node)
                old_ben_dist = evaluator.route_distance(route)

                for new_phys in all_phys:
                    if new_phys == old_phys:
                        continue

                    # 1. 计算取货车 V2 的节省
                    evaluator.task_to_phys[pick_node] = new_phys
                    if not evaluator.check_e2_route_feasible(route, sat_id):
                        evaluator.task_to_phys[pick_node] = old_phys
                        continue
                    new_ben_dist = evaluator.route_distance(route)
                    v2_saving = old_ben_dist - new_ben_dist
                    evaluator.task_to_phys[pick_node] = old_phys

                    if v2_saving <= 0.01:
                        continue

                    # 2. 找最优搬运车 V1
                    for carrier_idx, carrier_route in enumerate(routes):
                        if carrier_idx == ben_idx:
                            continue

                        carrier_nodes = carrier_route.nodes
                        pick_vnode = f"TRANS_PICK_{old_phys}"
                        drop_vnode = f"TRANS_DROP_{new_phys}"

                        # 限制搜索范围避免太慢
                        n = len(carrier_nodes)
                        for pick_pos in range(n + 1):
                            for drop_pos in range(pick_pos + 1, n + 2):
                                # 插入虚拟节点
                                old_nodes = carrier_nodes[:]
                                old_loads = dict(carrier_route.transport_loads)

                                carrier_nodes.insert(pick_pos, pick_vnode)
                                carrier_route.transport_loads[pick_vnode] = (
                                    t.w, t.v)

                                carrier_nodes.insert(drop_pos, drop_vnode)
                                carrier_route.transport_loads[drop_vnode] = (
                                    t.w, t.v)

                                # 计算搬运车路线距离变化
                                if evaluator.check_e2_route_feasible(
                                        carrier_route, sat_id):
                                    old_carrier_dist = _calc_dist_without(
                                        old_nodes, carrier_route.base,
                                        evaluator)
                                    new_carrier_dist = evaluator.route_distance(
                                        carrier_route)
                                    detour_cost = (new_carrier_dist
                                                   - old_carrier_dist)

                                    net_saving = v2_saving - detour_cost

                                    if (best_plan is None
                                            or net_saving > best_plan[0]):
                                        best_plan = (
                                            net_saving, sat_id, ben_idx,
                                            pick_node, new_phys,
                                            carrier_idx, pick_pos, drop_pos)

                                # 恢复
                                carrier_route.nodes = old_nodes
                                carrier_route.transport_loads = old_loads

    # 3. 应用最优方案
    if best_plan and best_plan[0] > 0.01:
        (net_saving, sat_id, ben_idx, pick_node, new_phys,
         carrier_idx, pick_pos, drop_pos) = best_plan

        old_phys = evaluator.phys(pick_node)
        t = evaluator.e2_task_map[pick_node]

        # 取货车 V2：改位置
        evaluator.task_to_phys[pick_node] = new_phys

        # 搬运车 V1：插入虚拟节点
        carrier_route = sol.e2_routes[sat_id][carrier_idx]
        pick_vnode = f"TRANS_PICK_{old_phys}"
        drop_vnode = f"TRANS_DROP_{new_phys}"

        carrier_route.nodes.insert(pick_pos, pick_vnode)
        carrier_route.transport_loads[pick_vnode] = (t.w, t.v)

        carrier_route.nodes.insert(drop_pos, drop_vnode)
        carrier_route.transport_loads[drop_vnode] = (t.w, t.v)

        # 记录中转
        sol.transshipments[(sat_id, ben_idx, pick_node, new_phys)] = (
            old_phys, carrier_idx, t.w, t.v)

        sol.cost = evaluator.total_cost(sol)
        return True

    return False


def _calc_dist_without(nodes, base, evaluator):
    """计算不含虚拟节点的路线距离"""
    path = [base] + nodes + [base]
    total = 0.0
    for i in range(len(path) - 1):
        total += evaluator.dist(path[i], path[i + 1])
    return total


def transshipment_destroy(sol, evaluator, num_remove=1):
    """撤销中转：从搬运车移除虚拟节点 + 恢复取货车位置"""
    if not sol.transshipments:
        return []

    keys = list(sol.transshipments.keys())
    random.shuffle(keys)
    undone = 0

    for key in keys:
        if undone >= num_remove:
            break
        sat_id, ben_idx, pick_node, new_phys = key
        old_phys, carrier_idx, cw, cv = sol.transshipments[key]

        # 恢复取货车位置
        evaluator.task_to_phys[pick_node] = old_phys

        # 从搬运车移除虚拟节点
        pick_vnode = f"TRANS_PICK_{old_phys}"
        drop_vnode = f"TRANS_DROP_{new_phys}"
        carrier_route = sol.e2_routes[sat_id][carrier_idx]

        if pick_vnode in carrier_route.nodes:
            carrier_route.nodes.remove(pick_vnode)
            carrier_route.transport_loads.pop(pick_vnode, None)
        if drop_vnode in carrier_route.nodes:
            carrier_route.nodes.remove(drop_vnode)
            carrier_route.transport_loads.pop(drop_vnode, None)

        del sol.transshipments[key]
        undone += 1

    return []


# =====================================================================
#  破坏算子
# =====================================================================

def random_removal(sol, evaluator, num_remove, echelon='e2'):
    removed = []
    if echelon == 'e2':
        all_nodes = []
        for sat_id, routes in sol.e2_routes.items():
            for r_idx, r in enumerate(routes):
                for nid in r.nodes:
                    if not nid.startswith("TRANS_"):
                        all_nodes.append((sat_id, r, nid))
        random.shuffle(all_nodes)
        for sat_id, r, nid in all_nodes[:num_remove]:
            if nid in r.nodes:
                r.nodes.remove(nid)
                removed.append(nid)
    elif echelon == 'e1':
        all_nodes = []
        for r in sol.e1_routes:
            for nid in r.nodes:
                all_nodes.append((r, nid))
        random.shuffle(all_nodes)
        for r, nid in all_nodes[:num_remove]:
            if nid in r.nodes:
                r.nodes.remove(nid)
                removed.append(nid)
    return removed


def worst_removal(sol, evaluator, num_remove, echelon='e2'):
    savings = []
    if echelon == 'e2':
        for sat_id, routes in sol.e2_routes.items():
            for r in routes:
                path = r.full_path()
                for idx, nid in enumerate(r.nodes):
                    if nid.startswith("TRANS_"):
                        continue
                    pos = idx + 1
                    if pos >= len(path) - 1:
                        continue
                    prev_n = path[pos - 1]
                    next_n = path[pos + 1]
                    saving = (evaluator.dist(prev_n, nid) +
                              evaluator.dist(nid, next_n) -
                              evaluator.dist(prev_n, next_n))
                    savings.append((saving, sat_id, r, nid))
    elif echelon == 'e1':
        for r in sol.e1_routes:
            path = r.full_path()
            for idx, nid in enumerate(r.nodes):
                pos = idx + 1
                if pos >= len(path) - 1:
                    continue
                prev_n = path[pos - 1]
                next_n = path[pos + 1]
                saving = (evaluator.dist(prev_n, nid) +
                          evaluator.dist(nid, next_n) -
                          evaluator.dist(prev_n, next_n))
                savings.append((saving, None, r, nid))

    savings.sort(key=lambda x: -x[0])
    removed = []
    for saving, sat_id, r, nid in savings[:num_remove]:
        if nid in r.nodes:
            r.nodes.remove(nid)
            removed.append(nid)
    return removed


def shaw_removal(sol, evaluator, num_remove, echelon='e2'):
    if echelon == 'e2':
        all_nodes = []
        for sat_id, routes in sol.e2_routes.items():
            for r in routes:
                for nid in r.nodes:
                    if not nid.startswith("TRANS_"):
                        all_nodes.append((sat_id, r, nid))
        if not all_nodes:
            return []

        seed_sat, seed_r, seed_nid = random.choice(all_nodes)
        seed_task = evaluator.e2_task_map.get(seed_nid)

        similarities = []
        for sat_id, r, nid in all_nodes:
            if nid == seed_nid:
                continue
            t = evaluator.e2_task_map.get(nid)
            if not t or not seed_task:
                continue
            dist_sim = evaluator.dist(seed_nid, nid)
            tw_sim = abs(seed_task.tw[0] - t.tw[0])
            type_sim = 0.0 if seed_task.kind == t.kind else 50.0
            score = dist_sim + 0.1 * tw_sim + type_sim
            similarities.append((score, sat_id, r, nid))

        similarities.sort(key=lambda x: x[0])
        removed = [seed_nid]
        seed_r.nodes.remove(seed_nid)

        for score, sat_id, r, nid in similarities[:num_remove - 1]:
            if nid in r.nodes:
                r.nodes.remove(nid)
                removed.append(nid)
        return removed
    else:
        return random_removal(sol, evaluator, num_remove, echelon)


# =====================================================================
#  修复算子
# =====================================================================

def greedy_insertion(sol, removed, evaluator, echelon='e2'):
    if echelon == 'e2':
        # 只插入真实节点，跳过虚拟节点
        real_removed = [n for n in removed if not n.startswith("TRANS_")]
        for nid in real_removed:
            task = evaluator.e2_task_map.get(nid)
            if not task:
                continue
            sat_id = None
            for s, tasks in evaluator.e2_tasks_by_sat.items():
                if any(t.id == nid for t in tasks):
                    sat_id = s
                    break
            if not sat_id:
                continue

            best_cost = float('inf')
            best_route = None
            best_pos = None

            routes = sol.e2_routes.get(sat_id, [])
            for r in routes:
                for pos in range(len(r.nodes) + 1):
                    old_nodes = r.nodes[:]
                    r.nodes.insert(pos, nid)
                    if evaluator.check_e2_route_feasible(r, sat_id):
                        insert_cost = evaluator.route_distance(r)
                        if insert_cost < best_cost:
                            best_cost = insert_cost
                            best_route = r
                            best_pos = pos
                    r.nodes = old_nodes

            if best_route is not None:
                best_route.nodes.insert(best_pos, nid)
            else:
                # 已达车辆上限则强制插入最优路线, 否则开新路线
                routes = sol.e2_routes.get(sat_id, [])
                max_v = evaluator.vehicles_per_satellite
                if len(routes) >= max_v:
                    # 强制插入距离最短的路线
                    best_r, best_pos2, best_c2 = None, 0, float('inf')
                    for r in routes:
                        for pos in range(len(r.nodes) + 1):
                            old_nodes = r.nodes[:]
                            r.nodes.insert(pos, nid)
                            c = evaluator.route_distance(r)
                            r.nodes = old_nodes
                            if c < best_c2:
                                best_c2, best_r, best_pos2 = c, r, pos
                    if best_r:
                        best_r.nodes.insert(best_pos2, nid)
                else:
                    new_route = Route(sat_id, [nid])
                    if sat_id not in sol.e2_routes:
                        sol.e2_routes[sat_id] = []
                    sol.e2_routes[sat_id].append(new_route)

    elif echelon == 'e1':
        depot_id = evaluator.problem.depot.id
        for nid in removed:
            task = evaluator.e1_task_map.get(nid)
            if not task:
                continue

            best_cost = float('inf')
            best_route = None
            best_pos = None

            for r in sol.e1_routes:
                for pos in range(len(r.nodes) + 1):
                    old_nodes = r.nodes[:]
                    r.nodes.insert(pos, nid)
                    if evaluator.check_e1_route_feasible(r):
                        insert_cost = evaluator.route_distance(r)
                        if insert_cost < best_cost:
                            best_cost = insert_cost
                            best_route = r
                            best_pos = pos
                    r.nodes = old_nodes

            if best_route is not None:
                best_route.nodes.insert(best_pos, nid)
            else:
                sol.e1_routes.append(Route(depot_id, [nid]))


def regret_insertion(sol, removed, evaluator, echelon='e2', k=2):
    if echelon == 'e2':
        pending = [n for n in removed if not n.startswith("TRANS_")]
        while pending:
            best_regret = -float('inf')
            best_nid = None
            best_route_info = None

            for nid in list(pending):
                task = evaluator.e2_task_map.get(nid)
                if not task:
                    pending.remove(nid)
                    continue

                sat_id = None
                for s, tasks in evaluator.e2_tasks_by_sat.items():
                    if any(t.id == nid for t in tasks):
                        sat_id = s
                        break
                if not sat_id:
                    pending.remove(nid)
                    continue

                insertion_costs = []
                routes = sol.e2_routes.get(sat_id, [])
                for r in routes:
                    for pos in range(len(r.nodes) + 1):
                        old_nodes = r.nodes[:]
                        r.nodes.insert(pos, nid)
                        if evaluator.check_e2_route_feasible(r, sat_id):
                            c = evaluator.route_distance(r)
                            insertion_costs.append((c, r, pos))
                        r.nodes = old_nodes

                if not insertion_costs:
                    insertion_costs.append((
                        evaluator.dist(sat_id, nid) * 2, None, 0))

                insertion_costs.sort(key=lambda x: x[0])
                regret = 0.0
                for i in range(1, min(k, len(insertion_costs))):
                    regret += insertion_costs[i][0] - insertion_costs[0][0]

                if regret > best_regret:
                    best_regret = regret
                    best_nid = nid
                    best_route_info = insertion_costs[0]

            if best_nid is None:
                break

            assert best_route_info is not None
            pending.remove(best_nid)
            _, route, pos = best_route_info
            if route is not None:
                route.nodes.insert(pos, best_nid)
            else:
                sat_id = None
                for s, tasks in evaluator.e2_tasks_by_sat.items():
                    if any(t.id == best_nid for t in tasks):
                        sat_id = s
                        break
                if sat_id:
                    routes = sol.e2_routes.get(sat_id, [])
                    max_v = evaluator.vehicles_per_satellite
                    if len(routes) >= max_v:
                        # 已达上限, 强制插入最优路线
                        best_r2, best_pos2, best_c2 = None, 0, float('inf')
                        for r in routes:
                            for p in range(len(r.nodes) + 1):
                                old_nodes = r.nodes[:]
                                r.nodes.insert(p, best_nid)
                                c = evaluator.route_distance(r)
                                r.nodes = old_nodes
                                if c < best_c2:
                                    best_c2, best_r2, best_pos2 = c, r, p
                        if best_r2:
                            best_r2.nodes.insert(best_pos2, best_nid)
                    else:
                        new_route = Route(sat_id, [best_nid])
                        if sat_id not in sol.e2_routes:
                            sol.e2_routes[sat_id] = []
                        sol.e2_routes[sat_id].append(new_route)

    elif echelon == 'e1':
        greedy_insertion(sol, removed, evaluator, echelon='e1')


# =====================================================================
#  清理 + ALNS 主循环
# =====================================================================

def clean_empty_routes(sol):
    sol.e1_routes = [r for r in sol.e1_routes if r.nodes]
    for sat_id in sol.e2_routes:
        sol.e2_routes[sat_id] = [
            r for r in sol.e2_routes[sat_id] if r.nodes
        ]


def _roulette(weights):
    total = sum(weights)
    if total < 1e-10:
        return random.randint(0, len(weights) - 1)
    r = random.random() * total
    cumsum = 0.0
    for i, w in enumerate(weights):
        cumsum += w
        if cumsum >= r:
            return i
    return len(weights) - 1


def solve_alns(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
               max_iterations=5000, max_time=120, seed=42, verbose=True):
    """
    ALNS 求解 Model 3 (取送货 + 横向中转)
    """
    random.seed(seed)
    start_time = time.time()

    evaluator = Evaluator(problem, e1_tasks, e2_tasks_by_sat,
                          vehicles_per_satellite=vehicles_per_satellite)

    # ---- 初始解 ----
    sol = build_initial_solution(problem, e1_tasks, e2_tasks_by_sat, evaluator)
    sol.cost = evaluator.total_cost(sol)
    best_sol = sol.copy()
    best_cost = sol.cost

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"ALNS 求解器 (Model 3 取送货 + 横向中转)")
        print(f"{'=' * 60}")
        print(f"初始解目标值: {sol.cost:.2f}")
        print(f"初始解可行性: {evaluator.is_feasible(sol)}")
        print(f"E1 路径数: {len(sol.e1_routes)}")
        e2_count = sum(len(v) for v in sol.e2_routes.values())
        print(f"E2 路径数: {e2_count}")

    # ---- 中转优化初始解 ----
    optimize_transshipment(sol, evaluator)
    if sol.cost < best_cost:
        best_sol = sol.copy()
        best_cost = sol.cost
    if verbose:
        print(f"中转优化后目标值: {sol.cost:.2f}")
        n_trans = len(sol.transshipments)
        print(f"中转操作数: {n_trans}")

    # ---- 算子与权重 ----
    destroy_ops = [random_removal, worst_removal, shaw_removal, transshipment_destroy]
    repair_ops = [greedy_insertion, regret_insertion]
    n_d, n_r = len(destroy_ops), len(repair_ops)

    d_weights = [1.0] * n_d
    r_weights = [1.0] * n_r
    d_scores = [0.0] * n_d
    r_scores = [0.0] * n_r
    d_counts = [0] * n_d
    r_counts = [0] * n_r

    sigma1, sigma2, sigma3 = 33, 9, 3
    decay = 0.8

    T_start = max(sol.cost * 0.05, 10.0)
    T_end = 0.01
    T = T_start
    cooling_rate = (T_end / T_start) ** (1.0 / max(max_iterations, 1))

    total_e2 = sum(len(r.nodes) for routes in sol.e2_routes.values() for r in routes)
    total_e1 = sum(len(r.nodes) for r in sol.e1_routes)

    segment_size = 100

    for iteration in range(max_iterations):
        if time.time() - start_time > max_time:
            if verbose:
                print(f"\n达到时间限制 {max_time}s")
            break

        d_idx = _roulette(d_weights)
        r_idx = _roulette(r_weights)
        d_counts[d_idx] += 1
        r_counts[r_idx] += 1

        # E1 vs E2
        if random.random() < 0.8 and total_e2 > 0:
            echelon = 'e2'
            n_tasks = total_e2
        else:
            echelon = 'e1'
            n_tasks = total_e1

        max_remove = max(2, min(n_tasks // 2, 20))
        num_remove = random.randint(1, max(1, max_remove))

        candidate = sol.copy()

        if d_idx == 3:
            # transshipment_destroy: 撤销中转，不需要常规 repair
            destroy_ops[d_idx](candidate, evaluator, num_remove=1)
            clean_empty_routes(candidate)
        else:
            removed = destroy_ops[d_idx](candidate, evaluator, num_remove, echelon)

            if r_idx == 1:
                repair_ops[r_idx](candidate, removed, evaluator, echelon, k=2)
            else:
                repair_ops[r_idx](candidate, removed, evaluator, echelon)

            clean_empty_routes(candidate)

        candidate.cost = evaluator.total_cost(candidate)

        # 中转优化：只在解改进时或每 20 次迭代调用，避免拖慢主循环
        trans_interval = 20
        if candidate.cost < sol.cost - 1e-6 or iteration % trans_interval == 0:
            optimize_transshipment(candidate, evaluator)
            candidate.cost = evaluator.total_cost(candidate)

        # 接受准则
        delta = candidate.cost - sol.cost
        if delta < -1e-6:
            sol = candidate
            if sol.cost < best_cost - 1e-6:
                best_sol = sol.copy()
                best_cost = sol.cost
                d_scores[d_idx] += sigma1
                r_scores[r_idx] += sigma1
                if verbose and iteration % 100 == 0:
                    print(f"  [{iteration:5d}] ★ 新最优: {best_cost:.2f}"
                          f"  中转: {len(best_sol.transshipments)}")
            else:
                d_scores[d_idx] += sigma2
                r_scores[r_idx] += sigma2
        elif T > 1e-10:
            prob = math.exp(-delta / T)
            if random.random() < prob:
                sol = candidate
                d_scores[d_idx] += sigma3
                r_scores[r_idx] += sigma3

        T *= cooling_rate

        # 自适应权重更新
        if (iteration + 1) % segment_size == 0:
            for i in range(n_d):
                if d_counts[i] > 0:
                    d_weights[i] = (decay * d_weights[i] +
                                    (1 - decay) * d_scores[i] / d_counts[i])
                d_scores[i] = 0.0
                d_counts[i] = 0
            for i in range(n_r):
                if r_counts[i] > 0:
                    r_weights[i] = (decay * r_weights[i] +
                                    (1 - decay) * r_scores[i] / r_counts[i])
                r_scores[i] = 0.0
                r_counts[i] = 0
            if verbose:
                elapsed = time.time() - start_time
                print(f"  [{iteration + 1:5d}] cost={sol.cost:.2f}  "
                      f"best={best_cost:.2f}  T={T:.4f}  "
                      f"中转={len(sol.transshipments)}  "
                      f"time={elapsed:.1f}s")

    elapsed = time.time() - start_time
    if verbose:
        print(f"\n{'=' * 60}")
        print(f"ALNS 求解完成")
        print(f"最优目标值: {best_cost:.2f}")
        print(f"中转操作数: {len(best_sol.transshipments)}")
        print(f"总耗时: {elapsed:.2f} 秒")
        print(f"{'=' * 60}")

    # 转换输出格式
    e1_routes_out = [r.full_path() for r in best_sol.e1_routes]
    e2_routes_out = {}
    for sat_id, routes in best_sol.e2_routes.items():
        e2_routes_out[sat_id] = [r.full_path() for r in routes]

    _print_solution(best_sol, evaluator)
    return e1_routes_out, e2_routes_out


def _simulate_e1_load(route, evaluator):
    """模拟 E1 路线载重变化, 返回 [(node, Q, V), ...]"""
    init_w = sum(evaluator.e1_task_map[n].w
                 for n in route.nodes if evaluator.e1_task_map.get(n) and
                 evaluator.e1_task_map[n].kind == 'd')
    init_v = sum(evaluator.e1_task_map[n].v
                 for n in route.nodes if evaluator.e1_task_map.get(n) and
                 evaluator.e1_task_map[n].kind == 'd')
    Q_load, V_load = init_w, init_v
    result = [(route.base, Q_load, V_load)]
    for nid in route.nodes:
        t = evaluator.e1_task_map.get(nid)
        if t:
            Q_load -= evaluator.signed_demand(t)
            V_load -= evaluator.signed_volume(t)
        result.append((nid, Q_load, V_load))
    result.append((route.base, Q_load, V_load))
    return result


def _simulate_e2_load(route, sat_id, evaluator):
    """模拟 E2 路线载重变化"""
    init_w = sum(evaluator.e2_task_map[n].w
                 for n in route.nodes
                 if not n.startswith("TRANS_")
                 and evaluator.e2_task_map.get(n)
                 and evaluator.e2_task_map[n].kind == 'd')
    init_v = sum(evaluator.e2_task_map[n].v
                 for n in route.nodes
                 if not n.startswith("TRANS_")
                 and evaluator.e2_task_map.get(n)
                 and evaluator.e2_task_map[n].kind == 'd')
    Q_load, V_load = init_w, init_v
    result = [(sat_id, Q_load, V_load)]
    for nid in route.nodes:
        if nid.startswith("TRANS_PICK_"):
            tw, tv = route.transport_loads.get(nid, (0.0, 0.0))
            Q_load += tw
            V_load += tv
        elif nid.startswith("TRANS_DROP_"):
            tw, tv = route.transport_loads.get(nid, (0.0, 0.0))
            Q_load -= tw
            V_load -= tv
        else:
            t = evaluator.e2_task_map.get(nid)
            if t:
                Q_load -= evaluator.signed_demand(t)
                V_load -= evaluator.signed_volume(t)
        result.append((nid, Q_load, V_load))
    result.append((sat_id, Q_load, V_load))
    return result


def _print_solution(sol, evaluator):
    print("\n--- E1 路线 ---")
    for i, r in enumerate(sol.e1_routes):
        path = r.full_path()
        dist = evaluator.route_distance(r)
        print(f"  卡车 {i}: {' -> '.join(path)}  (距离={dist:.2f})")
        loads = _simulate_e1_load(r, evaluator)
        load_strs = []
        for idx, (n, Qv, Vv) in enumerate(loads):
            is_first = (idx == 0)
            is_last = (idx == len(loads) - 1)
            tag = n if is_first else f"{n}_arr" if is_last else str(n)
            load_strs.append(f"{tag}(Q={Qv:.1f},V={Vv:.1f})")
        print(f"    载量: {' -> '.join(load_strs)}")

    print("\n--- E2 路线 ---")
    for sat_id, routes in sol.e2_routes.items():
        if not routes:
            continue
        print(f"  前置仓 {sat_id}:")
        for j, r in enumerate(routes):
            path = r.full_path()
            dist = evaluator.route_distance(r)
            print(f"    车辆 {j}: {' -> '.join(path)}  (距离={dist:.2f})")
            loads = _simulate_e2_load(r, sat_id, evaluator)
            load_strs = []
            for idx, (n, Qv, Vv) in enumerate(loads):
                is_first = (idx == 0)
                is_last = (idx == len(loads) - 1)
                tag = n if is_first else f"{n}_arr" if is_last else str(n)
                load_strs.append(f"{tag}(Q={Qv:.1f},V={Vv:.1f})")
            print(f"      载量: {' -> '.join(load_strs)}")

    if sol.transshipments:
        print("\n--- 中转操作 (搬运 + 取货位置变更) ---")
        for (sat_id, ben_idx, pick_node, new_phys), (
                old_phys, carrier_idx, cw, cv) in sol.transshipments.items():
            print(f"  sat={sat_id}: 搬运车{carrier_idx} "
                  f"取容器@{old_phys} → 放容器@{new_phys}, "
                  f"取货车{ben_idx} {pick_node}: "
                  f"取货位置 {old_phys} → {new_phys} "
                  f"(w={cw:.1f}, v={cv:.1f})")
