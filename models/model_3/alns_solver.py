# models/model_3/alns_solver.py
# -*- coding: utf-8 -*-
"""
Model 3 (取送货 + 横向中转) 的 ALNS 求解器
在 Model 2 基础上扩展：
  - E1/E2 共享容量: L+R ≤ Q, W+V ≤ C^vol
  - E2 横向中转: 同卫星车辆可在共同访问的顾客点卸货/接力
  - 中转优化算子: 后处理阶段识别中转机会并应用

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

    def full_path(self):
        return [self.base] + self.nodes + [self.base]

    def copy(self):
        return Route(self.base, list(self.nodes))


class Solution:
    def __init__(self):
        self.e1_routes = []
        self.e2_routes = {}
        self.cost = float('inf')
        # 中转记录: {(sat_id, v_idx_1, v_idx_2, node_id): (dq, dw)}
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

    def check_e2_route_feasible(self, route: Route, sat_id,
                                  trans_in=None, trans_out=None):
        """E2 统一 Q/V 可行性: 0 ≤ Q ≤ cap, 送减取增 + 中转

        trans_in:  dict[phys_node] = (dq, dw) 本车从其他车接收的
        trans_out: dict[phys_node] = (dq, dw) 本车转移给其他车的
        """
        trans_in = trans_in or {}
        trans_out = trans_out or {}

        init_w = 0.0
        init_v = 0.0
        for nid in route.nodes:
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
            t = self.e2_task_map.get(nid)
            if t:
                if current_time < t.tw[0]:
                    current_time = t.tw[0]
                if current_time > t.tw[1] + 1e-6:
                    return False

                # 中转
                phys_n = self.phys(nid)
                if phys_n in trans_in:
                    dq_in, dw_in = trans_in[phys_n]
                    Q_load += dq_in; V_load += dw_in
                if phys_n in trans_out:
                    dq_out, dw_out = trans_out[phys_n]
                    Q_load -= dq_out; V_load -= dw_out

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
            # 构建该卫星下每辆车的 trans_in/trans_out
            r_trans = self._build_transshipment_maps(sol, sat_id)
            for idx, r in enumerate(routes):
                trans_in = r_trans['in'].get(idx, {})
                trans_out = r_trans['out'].get(idx, {})
                if not self.check_e2_route_feasible(r, sat_id, trans_in, trans_out):
                    return False
                visited_e2.update(r.nodes)
            required = {t.id for t in self.e2_tasks_by_sat.get(sat_id, [])}
            if visited_e2 != required:
                return False
        return True

    def _build_transshipment_maps(self, sol, sat_id):
        """根据 sol.transshipments 构建每辆车的 trans_in/trans_out 映射"""
        routes = sol.e2_routes.get(sat_id, [])
        r_trans_in = {i: {} for i in range(len(routes))}
        r_trans_out = {i: {} for i in range(len(routes))}

        for (s_id, v1, v2, node_id), (dq, dw) in sol.transshipments.items():
            if s_id != sat_id:
                continue
            phys = self.phys(node_id)
            # v1 转出给 v2
            if phys not in r_trans_out[v1]:
                r_trans_out[v1][phys] = [0.0, 0.0]
            r_trans_out[v1][phys][0] += dq
            r_trans_out[v1][phys][1] += dw
            if phys not in r_trans_in[v2]:
                r_trans_in[v2][phys] = [0.0, 0.0]
            r_trans_in[v2][phys][0] += dq
            r_trans_in[v2][phys][1] += dw

        # 转换回 tuple 格式
        r_in = {i: {k: (v[0], v[1]) for k, v in d.items()}
                for i, d in r_trans_in.items()}
        r_out = {i: {k: (v[0], v[1]) for k, v in d.items()}
                 for i, d in r_trans_out.items()}
        return {'in': r_in, 'out': r_out}


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
#  中转优化算子
# =====================================================================

def optimize_transshipment(sol, evaluator):
    """后处理：识别并应用同卫星车辆之间的中转机会。

    逻辑：
    1. 对每个卫星，找出所有 E2 路线
    2. 找到两两路线之间的共同物理顾客节点
    3. 计算该节点处的 pickup 余量和容量空间
    4. 如果车辆 A pickup 满载但还有路线未完，可将部分 pickup 转给 B
    5. 选择使总成本降低最多的中转方案
    """
    improved = False

    for sat_id, routes in sol.e2_routes.items():
        if len(routes) < 2:
            continue

        # 对每对路线搜索共同物理节点
        for i in range(len(routes)):
            for j in range(i + 1, len(routes)):
                ri, rj = routes[i], routes[j]
                phys_i = {evaluator.phys(n): n for n in ri.nodes}
                phys_j = {evaluator.phys(n): n for n in rj.nodes}
                common_phys = set(phys_i.keys()) & set(phys_j.keys())

                for phys_n in common_phys:
                    # ri 在该节点的 Q 载量 (转出方可提供的)
                    load_i = _compute_load_at_node(ri, phys_i[phys_n], evaluator)
                    # rj 的剩余容量 (接收方可接受的)
                    spare_j = _compute_spare_capacity_at_node(rj, phys_j[phys_n], evaluator)

                    transfer_w = min(load_i * 0.5, spare_j)  # 最多转移一半载量
                    transfer_v = min(transfer_w, spare_j)    # 体积按重量比例

                    if transfer_w > 0.01:
                        # 尝试应用中转并检查可行性
                        old_cost = evaluator.route_distance(ri) + evaluator.route_distance(rj)

                        # 记录中转
                        sol.transshipments[(sat_id, i, j, phys_i[phys_n])] = (
                            transfer_w, transfer_v)

                        # 检查可行性
                        r_trans = evaluator._build_transshipment_maps(sol, sat_id)
                        ti = r_trans['in'].get(i, {})
                        to_i = r_trans['out'].get(i, {})
                        tj = r_trans['in'].get(j, {})
                        to_j = r_trans['out'].get(j, {})

                        feasible = (
                            evaluator.check_e2_route_feasible(ri, sat_id, ti, to_i) and
                            evaluator.check_e2_route_feasible(rj, sat_id, tj, to_j)
                        )

                        if feasible:
                            new_cost = evaluator.total_cost(sol)
                            if new_cost < sol.cost:
                                sol.cost = new_cost
                                improved = True
                            else:
                                del sol.transshipments[(sat_id, i, j, phys_i[phys_n])]
                        else:
                            del sol.transshipments[(sat_id, i, j, phys_i[phys_n])]

    return improved


def _compute_load_at_node(route, node_id, evaluator):
    """计算车辆在 node_id 处的统一 Q 载量"""
    Q_load = sum(evaluator.e2_task_map[n].w
                 for n in route.nodes if evaluator.e2_task_map.get(n) and
                 evaluator.e2_task_map[n].kind == 'd')
    for nid in route.nodes:
        t = evaluator.e2_task_map.get(nid)
        if t:
            Q_load -= evaluator.signed_demand(t)
        if nid == node_id:
            break
    return Q_load


def _compute_spare_capacity_at_node(route, node_id, evaluator):
    """计算车辆在 node_id 处的剩余容量"""
    Q_load = _compute_load_at_node(route, node_id, evaluator)
    return max(0.0, evaluator.problem.e2_weight_cap - Q_load)


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
            # 同类型 (delivery/pickup) 相似度更高
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
        for nid in removed:
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
            r_trans = evaluator._build_transshipment_maps(sol, sat_id)
            for idx, r in enumerate(routes):
                ti = r_trans['in'].get(idx, {})
                to = r_trans['out'].get(idx, {})
                for pos in range(len(r.nodes) + 1):
                    old_nodes = r.nodes[:]
                    r.nodes.insert(pos, nid)
                    if evaluator.check_e2_route_feasible(r, sat_id, ti, to):
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
        pending = list(removed)
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
                r_trans = evaluator._build_transshipment_maps(sol, sat_id)
                for idx, r in enumerate(routes):
                    ti = r_trans['in'].get(idx, {})
                    to = r_trans['out'].get(idx, {})
                    for pos in range(len(r.nodes) + 1):
                        old_nodes = r.nodes[:]
                        r.nodes.insert(pos, nid)
                        if evaluator.check_e2_route_feasible(r, sat_id, ti, to):
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
    destroy_ops = [random_removal, worst_removal, shaw_removal]
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
        removed = destroy_ops[d_idx](candidate, evaluator, num_remove, echelon)

        if r_idx == 1:
            repair_ops[r_idx](candidate, removed, evaluator, echelon, k=2)
        else:
            repair_ops[r_idx](candidate, removed, evaluator, echelon)

        clean_empty_routes(candidate)
        candidate.cost = evaluator.total_cost(candidate)

        # 中转优化
        optimize_transshipment(candidate, evaluator)

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


def _simulate_e2_load(route, sat_id, evaluator, trans_in=None, trans_out=None):
    """模拟 E2 路线载重变化"""
    trans_in = trans_in or {}
    trans_out = trans_out or {}
    init_w = sum(evaluator.e2_task_map[n].w
                 for n in route.nodes if evaluator.e2_task_map.get(n) and
                 evaluator.e2_task_map[n].kind == 'd')
    init_v = sum(evaluator.e2_task_map[n].v
                 for n in route.nodes if evaluator.e2_task_map.get(n) and
                 evaluator.e2_task_map[n].kind == 'd')
    Q_load, V_load = init_w, init_v
    result = [(sat_id, Q_load, V_load)]
    for nid in route.nodes:
        phys_n = evaluator.phys(nid)
        if phys_n in trans_in:
            dq_in, dw_in = trans_in[phys_n]
            Q_load += dq_in; V_load += dw_in
        if phys_n in trans_out:
            dq_out, dw_out = trans_out[phys_n]
            Q_load -= dq_out; V_load -= dw_out
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
        r_trans = evaluator._build_transshipment_maps(sol, sat_id)
        for j, r in enumerate(routes):
            path = r.full_path()
            dist = evaluator.route_distance(r)
            print(f"    车辆 {j}: {' -> '.join(path)}  (距离={dist:.2f})")
            ti = r_trans['in'].get(j, {})
            to = r_trans['out'].get(j, {})
            loads = _simulate_e2_load(r, sat_id, evaluator, ti, to)
            load_strs = []
            for idx, (n, Qv, Vv) in enumerate(loads):
                is_first = (idx == 0)
                is_last = (idx == len(loads) - 1)
                tag = n if is_first else f"{n}_arr" if is_last else str(n)
                load_strs.append(f"{tag}(Q={Qv:.1f},V={Vv:.1f})")
            print(f"      载量: {' -> '.join(load_strs)}")

    if sol.transshipments:
        print("\n--- 中转操作 ---")
        for (sat_id, v1, v2, node_id), (dq, dw) in sol.transshipments.items():
            print(f"  sat={sat_id}, 车{v1}→车{v2} @{node_id}: "
                  f"Δq={dq:.1f}, Δw={dw:.1f}")
