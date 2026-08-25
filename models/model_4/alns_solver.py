# -*- coding: utf-8 -*-
"""
Model 4 的 ALNS 求解器。

与 Model 3 的唯一区别：顾客归属哪个前置仓是决策变量（z_cs），而不是数据里
固定输入的 assigned_sat。其余机制与 M3 保持一致：

  - 同一顾客的配送/回收任务必须归属同一前置仓（单分配，不做送收分离）；
  - E2 车队按前置仓独立，路线锚定本仓（vehicles_per_satellite）；
  - E1 配送量 / 回收量 / 截止时间由当前分配聚合决定，随解动态重建；
  - E1 车队、容量、时间窗与 E1-E2 衔接规则与 M3 相同。

数据结构沿用 alns_base 的 Route / Solution：e2_routes 是按前置仓分桶的
dict，顾客分配由"任务出现在哪个仓的桶里"隐式表达；e1_routes 由求值器在
每次评估前按当前分配做确定性重建，保证 E1 任务集与分配一致。
"""
import random
import time

from core.node import Task
from models.alns_base import (
    Route,
    Solution,
    FEAS_TOL,
    clean_empty_routes,
    is_tfp_active,
)
from models.alns_engine import ALNSConfig, ALNSHooks, run_alns_search
from models.strong_initial_solution import (
    build_strong_initial_solution,
    _apply_positions,
    _best_e2_item_insert,
    _build_e1_delivery_routes,
    _insert_e1_pickups_after_e2_returns,
)
from utils.task_builder import build_tasks_for_model
from models.model_3.alns_solver import Evaluator as _M3Evaluator


# =====================================================================
#  分配感知评估器
# =====================================================================

class Evaluator:
    """从解的 E2 路由推导分配，并据此动态重建 E1/E2 任务与求值。"""

    def __init__(self, problem, TFp=9999.0, vehicles_per_satellite=4):
        self.problem = problem
        self.TFp = TFp
        self.vehicles_per_satellite = vehicles_per_satellite
        self.sat_ids = [s.id for s in problem.satellites]

        cw = problem.fleet.container_weight
        cv = problem.fleet.container_volume
        # 每个顾客的 d/p 任务目录（与分配的卫星无关）
        self.catalog = {}
        for c in problem.customers:
            d_task = Task(f"{c.id}d", c.id, "d",
                          c.weight_del + cw, cv, c.tw_del)
            p_task = Task(f"{c.id}p", c.id, "p",
                          cw, cv, c.tw_pick)
            self.catalog[c.id] = {"d": d_task, "p": p_task}

        self._reset_maps()

    def _reset_maps(self):
        self.e1_task_map = {}
        self.e2_task_map = {}
        self.e2_task_sat = {}
        self.e2_tasks_by_sat = {s: [] for s in self.sat_ids}
        self.e1_del_nodes = []
        self.e1_pick_nodes = []
        self.forbidden_e1 = set()
        self.task_to_phys = {}

    # ---------------- 分配推导与任务重建 ----------------

    def _catalog_task(self, node_id):
        if len(node_id) > 1 and node_id[-1] in ("d", "p"):
            pair = self.catalog.get(node_id[:-1])
            if pair is not None:
                return pair.get(node_id[-1])
        return None

    def assignment(self, sol):
        """返回 {customer_id: satellite_id}，由解中的 E2 路由推导。"""
        assign = {}
        for sat_id, routes in sol.e2_routes.items():
            for r in routes:
                for nid in r.nodes:
                    task = self._catalog_task(nid)
                    if task is not None:
                        assign[task.cust] = sat_id
        return assign

    def rebuild_from_solution(self, sol):
        """从解的 E2 路由重建 E1/E2 任务映射，并确定性重建 E1 路线。"""
        assign = self.assignment(sol)
        cw = self.problem.fleet.container_weight
        cv = self.problem.fleet.container_volume

        agg = {
            s: {"count": 0, "del_w": 0.0, "del_v": 0.0,
                "pick_w": 0.0, "pick_v": 0.0,
                "due": float("inf"), "e2_tasks": []}
            for s in self.sat_ids
        }
        for cust_id, sat_id in assign.items():
            pair = self.catalog[cust_id]
            a = agg[sat_id]
            a["count"] += 1
            a["del_w"] += pair["d"].w
            a["del_v"] += pair["d"].v
            a["pick_w"] += pair["p"].w
            a["pick_v"] += pair["p"].v
            a["due"] = min(a["due"], pair["d"].tw[1])
            a["e2_tasks"].extend((pair["d"], pair["p"]))

        self.e2_tasks_by_sat = {s: [] for s in self.sat_ids}
        self.e2_task_map = {}
        self.e2_task_sat = {}
        for s in self.sat_ids:
            for t in agg[s]["e2_tasks"]:
                self.e2_tasks_by_sat[s].append(t)
                self.e2_task_map[t.id] = t
                self.e2_task_sat[t.id] = s

        self.e1_task_map = {}
        self.e1_del_nodes = []
        self.e1_pick_nodes = []
        for s in self.sat_ids:
            a = agg[s]
            if a["count"] <= 0:
                continue
            ready = getattr(self.problem.nodes[s], "e1_pickup_ready_time", 0.0)
            d_node = Task(f"{s}d", s, "d", a["del_w"], a["del_v"],
                          (0.0, a["due"]))
            p_node = Task(f"{s}p", s, "p", a["pick_w"], a["pick_v"],
                          (ready, float("inf")))
            self.e1_task_map[d_node.id] = d_node
            self.e1_task_map[p_node.id] = p_node
            self.e1_del_nodes.append(d_node.id)
            self.e1_pick_nodes.append(p_node.id)

        # 物理位置与分配无关：所有顾客任务都映射到顾客自身坐标，
        # 便于在插入检查中计算被临时搬移任务的成本/时间。
        self.task_to_phys = {t.id: t.cust for t in self.e1_task_map.values()}
        for pair in self.catalog.values():
            self.task_to_phys[pair["d"].id] = pair["d"].cust
            self.task_to_phys[pair["p"].id] = pair["p"].cust

        self.forbidden_e1 = set()
        for s in self.sat_ids:
            pid, did = f"{s}p", f"{s}d"
            if pid in self.e1_task_map and did in self.e1_task_map:
                self.forbidden_e1.add((pid, did))

        self._canonicalize_e1_routes(sol)

    def _canonicalize_e1_routes(self, sol):
        """按当前分配重建 E1 路线：先配送后回收，回收需晚于 E2 返回。"""
        depot_id = self.problem.depot.id
        required_del = {
            nid: self.e1_task_map[nid]
            for nid in self.e1_del_nodes
        }

        # 配送节点集合与重量均未变时保留现有 E1 路线（保留局部改进），
        # 否则按确定性贪心重建。
        current = {}
        for r in sol.e1_routes:
            for nid in r.nodes:
                t = self.e1_task_map.get(nid)
                if t is not None and t.kind == "d":
                    current[nid] = t

        # 配送节点集合不变时保留 E1 路线结构（E2 发车时刻稳定），
        # 仅在载量/时间在新重量下不可行时才重建。
        keep = set(current) == set(required_del)

        sat_urgency = {}
        for s in self.sat_ids:
            due = [t.tw[1] for t in self.e2_tasks_by_sat[s]
                   if t.kind == "d"]
            sat_urgency[s] = min(due) if due else float("inf")

        if keep:
            for r in sol.e1_routes:
                r.nodes = [
                    n for n in r.nodes
                    if n in self.e1_task_map
                    and self.e1_task_map[n].kind == "d"
                ]
            kept_ok = all(
                self.check_e1_route_feasible(r) for r in sol.e1_routes)
            if not kept_ok:
                del_tasks = [self.e1_task_map[n] for n in self.e1_del_nodes]
                sol.e1_routes = _build_e1_delivery_routes(
                    self.problem, self, depot_id, del_tasks, sat_urgency)
        else:
            del_tasks = [self.e1_task_map[n] for n in self.e1_del_nodes]
            sol.e1_routes = _build_e1_delivery_routes(
                self.problem, self, depot_id, del_tasks, sat_urgency)

        sol.e1_routes = [r for r in sol.e1_routes if r.nodes]

        e2_returns = self._e2_returns(sol)
        _insert_e1_pickups_after_e2_returns(
            self.problem, self, depot_id, sol.e1_routes,
            [self.e1_task_map[n] for n in self.e1_pick_nodes],
            e2_returns, sat_urgency)

    def _e2_returns(self, sol):
        """按当前 E1 配送时刻计算每个前置仓所有 E2 车的最晚返回时刻。"""
        e1_departures, _ = self.e1_schedule(sol)
        if e1_departures is None:
            e1_departures = {}
        returns = {}
        for sat_id in self.sat_ids:
            start = e1_departures.get(sat_id, 0.0)
            last = 0.0
            for r in sol.e2_routes.get(sat_id, []):
                feasible, _, ret = self.simulate_e2_route(r, sat_id, start)
                if feasible and ret is not None:
                    last = max(last, ret)
            returns[sat_id] = last
        return returns

    # ---------------- 与 M3 相同的求值接口 ----------------

    def e2_task_satellite(self, task_id):
        return self.e2_task_sat.get(task_id)

    def e2_start_times(self, sol):
        e2_start_by_sat, _ = self.e1_schedule(sol)
        return e2_start_by_sat or {}

    def phys(self, node_id):
        return self.task_to_phys.get(node_id, node_id)

    def dist(self, a, b):
        return self.problem.distance(self.phys(a), self.phys(b))

    def travel(self, a, b):
        return self.problem.travel_time(self.phys(a), self.phys(b))

    def service_time(self, node_id):
        phys_id = self.phys(node_id)
        node_obj = self.problem.nodes.get(phys_id)
        return getattr(node_obj, "service_time", 0.0) if node_obj else 0.0

    def signed_demand(self, task):
        return task.w if task.kind == "d" else -task.w

    def signed_volume(self, task):
        return task.v if task.kind == "d" else -task.v

    def route_distance(self, route):
        path = route.full_path()
        total = 0.0
        for i in range(len(path) - 1):
            total += self.dist(path[i], path[i + 1])
        return total

    def total_cost(self, sol):
        self.rebuild_from_solution(sol)
        cost = 0.0
        for r in sol.e1_routes:
            cost += self.route_distance(r)
        for sat_id, routes in sol.e2_routes.items():
            for r in routes:
                cost += self.route_distance(r)
        return cost

    def check_e1_route_feasible(self, route):
        path = route.full_path()
        for i in range(len(path) - 1):
            if (path[i], path[i + 1]) in self.forbidden_e1:
                return False

        init_w = 0.0
        init_v = 0.0
        for nid in route.nodes:
            t = self.e1_task_map.get(nid)
            if t and t.kind == "d":
                init_w += t.w
                init_v += t.v
        if init_w > self.problem.e1_weight_cap + FEAS_TOL:
            return False
        if init_v > self.problem.e1_volume_cap + FEAS_TOL:
            return False

        Q_load, V_load = init_w, init_v
        current_time = 0.0
        prev = route.base

        for nid in route.nodes:
            travel = self.travel(prev, nid)
            current_time += travel
            t = self.e1_task_map.get(nid)
            if t:
                if current_time < t.tw[0]:
                    current_time = t.tw[0]
                if current_time > t.tw[1] + FEAS_TOL:
                    return False
                if (t.kind == "p" and is_tfp_active(self.TFp)
                        and current_time < self.TFp - FEAS_TOL):
                    return False

                Q_load -= self.signed_demand(t)
                V_load -= self.signed_volume(t)
                if (Q_load < -FEAS_TOL
                        or Q_load > self.problem.e1_weight_cap + FEAS_TOL):
                    return False
                if (V_load < -FEAS_TOL
                        or V_load > self.problem.e1_volume_cap + FEAS_TOL):
                    return False
                current_time += self.service_time(nid)
            prev = nid
        return True

    def e1_schedule(self, sol):
        delivery_departures = {}
        pickup_arrivals = {}

        for route in sol.e1_routes:
            current_time = 0.0
            prev = route.base
            for nid in route.nodes:
                current_time += self.travel(prev, nid)
                task = self.e1_task_map.get(nid)
                if task:
                    if current_time < task.tw[0]:
                        current_time = task.tw[0]
                    if current_time > task.tw[1] + FEAS_TOL:
                        return None, None
                    if (task.kind == "p" and is_tfp_active(self.TFp)
                            and current_time < self.TFp - FEAS_TOL):
                        return None, None

                    if task.kind == "d":
                        current_time += self.service_time(nid)
                        delivery_departures[task.cust] = current_time
                    else:
                        pickup_arrivals[task.cust] = current_time
                        current_time += self.service_time(nid)
                prev = nid

        return delivery_departures, pickup_arrivals

    def simulate_e2_route(self, route, sat_id, start_time=0.0):
        init_w = 0.0
        init_v = 0.0
        for nid in route.nodes:
            t = self.e2_task_map.get(nid) or self._catalog_task(nid)
            if t and t.kind == "d":
                init_w += t.w
                init_v += t.v
        if init_w > self.problem.e2_weight_cap + FEAS_TOL:
            return False, {}, None
        if init_v > self.problem.e2_volume_cap + FEAS_TOL:
            return False, {}, None

        Q_load, V_load = init_w, init_v
        current_time = 0.0
        prev = sat_id
        arrivals = {}

        for nid in route.nodes:
            travel = self.travel(prev, nid)
            current_time += travel
            abs_arrival = start_time + current_time
            t = self.e2_task_map.get(nid) or self._catalog_task(nid)
            if t:
                if abs_arrival < t.tw[0]:
                    current_time = t.tw[0] - start_time
                    abs_arrival = t.tw[0]
                if abs_arrival > t.tw[1] + FEAS_TOL:
                    return False, {}, None

                arrivals[nid] = abs_arrival

                Q_load -= self.signed_demand(t)
                V_load -= self.signed_volume(t)
                if (Q_load < -FEAS_TOL
                        or Q_load > self.problem.e2_weight_cap + FEAS_TOL):
                    return False, {}, None
                if (V_load < -FEAS_TOL
                        or V_load > self.problem.e2_volume_cap + FEAS_TOL):
                    return False, {}, None

                current_time += self.service_time(nid)
            prev = nid

        return_time = start_time + current_time + self.travel(prev, sat_id)
        return True, arrivals, return_time

    def check_e2_route_feasible(self, route, sat_id, start_time):
        feasible, _, _ = self.simulate_e2_route(route, sat_id, start_time)
        return feasible

    def is_feasible(self, sol):
        self.rebuild_from_solution(sol)

        assign = self.assignment(sol)
        required_customers = {c.id for c in self.problem.customers}
        if set(assign) != required_customers:
            return False

        if len(sol.e1_routes) > self.problem.e1_vehicle_count:
            return False

        visited_e1 = []
        for r in sol.e1_routes:
            if not self.check_e1_route_feasible(r):
                return False
            visited_e1.extend(r.nodes)
        required_e1 = set(self.e1_task_map)
        if set(visited_e1) != required_e1 or len(visited_e1) != len(required_e1):
            return False

        e1_departures, e1_pickups = self.e1_schedule(sol)
        if e1_departures is None:
            return False

        e2_arrivals = {}
        e2_last_return_by_sat = {}
        for sat_id, routes in sol.e2_routes.items():
            if len(routes) > self.vehicles_per_satellite:
                return False
            start_time = e1_departures.get(sat_id, 0.0)
            req = {t.id for t in self.e2_tasks_by_sat.get(sat_id, [])}
            has_delivery = any(
                t.kind == "d" for t in self.e2_tasks_by_sat.get(sat_id, []))
            if routes and req and has_delivery and sat_id not in e1_departures:
                return False

            visited_e2 = []
            for r in routes:
                feasible, arrivals, return_time = self.simulate_e2_route(
                    r, sat_id, start_time)
                if not feasible:
                    return False
                e2_arrivals.update(arrivals)
                if return_time is not None:
                    old = e2_last_return_by_sat.get(sat_id, 0.0)
                    e2_last_return_by_sat[sat_id] = max(old, return_time)
                visited_e2.extend(r.nodes)
            if set(visited_e2) != req or len(visited_e2) != len(req):
                return False

        for task_id, task in self.e2_task_map.items():
            if task.kind != "p":
                continue
            d_id = f"{task.cust}d"
            if d_id not in self.e2_task_map:
                continue
            if task_id not in e2_arrivals or d_id not in e2_arrivals:
                return False
            if (e2_arrivals[task_id]
                    < e2_arrivals[d_id] + self.service_time(d_id) - FEAS_TOL):
                return False

        # E1 回收允许在前置仓等待至所有 E2 车辆返回
        # （与 MIP Link_Pickup 语义一致），早到不再视为不可行。
        # e1_pickups 仅用于此耦合检查，无下游依赖，故无需调整时刻。

        return True


# =====================================================================
#  任务构建辅助（显式分配，不修改 problem 状态）
# =====================================================================

def build_tasks_from_assignment(problem, assignment):
    """给定 {customer_id: satellite_id}，直接构造 M3 风格的 e1/e2 任务。"""
    cw = problem.fleet.container_weight
    cv = problem.fleet.container_volume
    sat_ids = [s.id for s in problem.satellites]

    e2_tasks_by_sat = {s: [] for s in sat_ids}
    agg = {
        s: {"count": 0, "del_w": 0.0, "del_v": 0.0,
            "pick_w": 0.0, "pick_v": 0.0, "due": float("inf")}
        for s in sat_ids
    }
    for c in problem.customers:
        sat = assignment.get(c.id)
        if sat is None or sat not in agg:
            continue
        a = agg[sat]
        a["count"] += 1
        a["del_w"] += c.weight_del + cw
        a["del_v"] += cv
        a["pick_w"] += cw
        a["pick_v"] += cv
        a["due"] = min(a["due"], c.tw_del[1])
        e2_tasks_by_sat[sat].append(
            Task(f"{c.id}d", c.id, "d", c.weight_del + cw, cv, c.tw_del))
        e2_tasks_by_sat[sat].append(
            Task(f"{c.id}p", c.id, "p", cw, cv, c.tw_pick))

    e1_tasks = []
    for s in sat_ids:
        a = agg[s]
        if a["count"] <= 0:
            continue
        ready = getattr(problem.nodes[s], "e1_pickup_ready_time", 0.0)
        e1_tasks.append(Task(f"{s}d", s, "d", a["del_w"], a["del_v"],
                             (0.0, a["due"])))
        e1_tasks.append(Task(f"{s}p", s, "p", a["pick_w"], a["pick_v"],
                             (ready, float("inf"))))
    return e1_tasks, e2_tasks_by_sat


def _fixed_assignment(problem):
    return {c.id: c.assigned_sat for c in problem.customers
            if c.assigned_sat}


def _candidate_seeds(problem):
    """初始分配的候选种子：数据固定分配 + 各阶最近前置仓。"""
    seeds = []
    fixed = _fixed_assignment(problem)
    if fixed:
        seeds.append(fixed)

    nearest = {}
    second = {}
    for c in problem.customers:
        sats = sorted(problem.satellites,
                      key=lambda s: problem.distance(c.id, s.id))
        nearest[c.id] = sats[0].id
        if len(sats) > 1:
            second[c.id] = sats[1].id
    seeds.append(nearest)
    if second:
        seeds.append(second)

    # 去重（保持确定性）
    seen = set()
    out = []
    for s in seeds:
        key = tuple(sorted(s.items()))
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def build_initial_solution(problem, evaluator, TFp=9999.0):
    """多种子构造初始解：优先数据固定分配，其次最近/次近前置仓。"""
    vps = evaluator.vehicles_per_satellite
    best_any = None
    for seed in _candidate_seeds(problem):
        e1_tasks, e2_tasks_by_sat = build_tasks_from_assignment(problem, seed)
        m3_eval = _M3Evaluator(problem, e1_tasks, e2_tasks_by_sat, TFp, vps)
        sol = build_strong_initial_solution(
            problem, e1_tasks, e2_tasks_by_sat, m3_eval)
        evaluator.rebuild_from_solution(sol)
        if evaluator.is_feasible(sol):
            sol.cost = evaluator.total_cost(sol)
            return sol
        if best_any is None or evaluator.total_cost(sol) < best_any.cost:
            best_any = sol
    return best_any


# =====================================================================
#  跨仓破坏/修复算子（顾客级：d+p 成对搬移，保持单分配不变式）
# =====================================================================

def _remove_customer(sol, cust_id):
    d_id, p_id = f"{cust_id}d", f"{cust_id}p"
    for routes in sol.e2_routes.values():
        for r in routes:
            r.nodes = [n for n in r.nodes if n != d_id and n != p_id]
    clean_empty_routes(sol)


def _customers(sol, evaluator):
    return sorted(evaluator.assignment(sol))


def random_customer_removal(sol, evaluator, num_remove, echelon="e2"):
    custs = _customers(sol, evaluator)
    random.shuffle(custs)
    removed = []
    for cust in custs[:num_remove]:
        _remove_customer(sol, cust)
        removed.append(cust)
    return removed


def worst_customer_removal(sol, evaluator, num_remove, echelon="e2"):
    savings = []
    for cust in _customers(sol, evaluator):
        d_id, p_id = f"{cust}d", f"{cust}p"
        saving = 0.0
        for routes in sol.e2_routes.values():
            for r in routes:
                path = r.full_path()
                for idx, nid in enumerate(r.nodes):
                    if nid != d_id and nid != p_id:
                        continue
                    pos = idx + 1
                    prev_n = path[pos - 1]
                    next_n = path[pos + 1]
                    saving += (evaluator.dist(prev_n, nid)
                               + evaluator.dist(nid, next_n)
                               - evaluator.dist(prev_n, next_n))
        savings.append((saving, cust))
    savings.sort(key=lambda x: -x[0])
    removed = []
    for saving, cust in savings[:num_remove]:
        _remove_customer(sol, cust)
        removed.append(cust)
    return removed


def shaw_customer_removal(sol, evaluator, num_remove, echelon="e2"):
    custs = _customers(sol, evaluator)
    if not custs:
        return []
    seed = random.choice(custs)
    seed_d = evaluator.catalog[seed]["d"]

    scored = []
    for cust in custs:
        if cust == seed:
            continue
        t = evaluator.catalog[cust]["d"]
        dist_sim = evaluator.dist(f"{seed}d", f"{cust}d")
        tw_sim = abs(seed_d.tw[0] - t.tw[0])
        scored.append((dist_sim + 0.1 * tw_sim, cust))
    scored.sort(key=lambda x: x[0])
    chosen = [seed] + [cust for _, cust in scored[:num_remove - 1]]
    for cust in chosen:
        _remove_customer(sol, cust)
    return chosen


def _best_task_insert(sol, evaluator, sat_id, task, start_time,
                      after=None):
    """在指定前置仓找 task 的最优插入位置。

    after: (d_id, d_arrival, service_d) —— 插入 p 时要求
    arrival(p) >= arrival(d) + service(d)，允许 d/p 分属同仓不同车。
    """
    routes = sol.e2_routes.get(sat_id, [])
    svc_d = 0.0
    if after is not None:
        d_id, d_arr, svc_d = after
        svc_d = evaluator.service_time(d_id)
    else:
        d_id = d_arr = None

    best = None
    for r in routes:
        old_cost = evaluator.route_distance(r)
        for pos in range(len(r.nodes) + 1):
            r.nodes.insert(pos, task.id)
            feasible, arrivals, _ = evaluator.simulate_e2_route(
                r, sat_id, start_time)
            if feasible and after is not None:
                a_p = arrivals.get(task.id)
                a_d = arrivals.get(d_id, d_arr)
                if (a_p is None or a_d is None
                        or a_p < a_d + svc_d - FEAS_TOL):
                    feasible = False
            new_cost = evaluator.route_distance(r) if feasible else None
            r.nodes.pop(pos)
            if feasible:
                inc = new_cost - old_cost
                if best is None or inc < best[0] - FEAS_TOL:
                    best = (inc, r, pos)
    if len(routes) < evaluator.vehicles_per_satellite:
        new_route = Route(sat_id, [task.id])
        ok = evaluator.check_e2_route_feasible(new_route, sat_id, start_time)
        if ok and after is not None:
            feasible, arrivals, _ = evaluator.simulate_e2_route(
                new_route, sat_id, start_time)
            a_p = arrivals.get(task.id)
            a_d = arrivals.get(d_id, d_arr)
            ok = (a_p is not None and a_d is not None
                  and a_p >= a_d + svc_d - FEAS_TOL)
        if ok:
            inc = evaluator.route_distance(new_route)
            if best is None or inc < best[0] - FEAS_TOL:
                best = (inc, None, None)
    return best


def multi_sat_insertion_cross(sol, removed, evaluator, echelon="e2", k=2):
    """顺序插入 d 再插 p 的跨车版本（p 可进入同仓另一辆车）。

    顺序插入 d 再插 p：p 可以进入同仓的另一辆车（保持 M3 的跨车自由度），
    但 d/p 必须同仓（单分配不变式）。
    """
    pending = list(removed)
    random.shuffle(pending)
    for cust in pending:
        evaluator.rebuild_from_solution(sol)
        start_by_sat = evaluator.e2_start_times(sol)
        pair = evaluator.catalog[cust]
        d_task, p_task = pair["d"], pair["p"]

        best = None  # (delta, sat, d_route, d_pos, p_route, p_pos)
        for sat_id in evaluator.sat_ids:
            start_time = start_by_sat.get(sat_id, 0.0)
            d_best = _best_task_insert(
                sol, evaluator, sat_id, d_task, start_time)
            if d_best is None:
                continue
            d_inc, d_route, d_pos = d_best
            d_route_orig, d_pos_orig = d_route, d_pos

            # 提交 d，准备 p 的插入（记录各车到达时刻用于先送后取）
            if d_route is None:
                new_r = Route(sat_id, [d_task.id])
                sol.e2_routes.setdefault(sat_id, []).append(new_r)
                d_route, d_pos = new_r, 0
            else:
                d_route.nodes.insert(d_pos, d_task.id)

            _, d_arrivals, _ = evaluator.simulate_e2_route(
                d_route, sat_id, start_time)
            d_arr = d_arrivals.get(d_task.id)

            p_best = _best_task_insert(
                sol, evaluator, sat_id, p_task, start_time,
                after=(d_task.id, d_arr, evaluator.service_time(d_task.id)))

            # 撤销 d
            d_route.nodes.pop(d_pos)
            if not d_route.nodes:
                sol.e2_routes[sat_id].remove(d_route)

            if p_best is not None:
                p_inc, p_route, p_pos = p_best
                total = d_inc + p_inc
                if best is None or total < best[0] - FEAS_TOL:
                    best = (total, sat_id, d_route_orig, d_pos_orig,
                            p_route, p_pos)

        if best is not None:
            _, sat_id, d_route, d_pos, p_route, p_pos = best
            if d_route is None:
                d_route = Route(sat_id, [d_task.id])
                sol.e2_routes.setdefault(sat_id, []).append(d_route)
                d_pos = 0
            else:
                d_route.nodes.insert(d_pos, d_task.id)
            if p_route is None:
                p_route = Route(sat_id, [p_task.id])
                sol.e2_routes.setdefault(sat_id, []).append(p_route)
                p_pos = 0
            else:
                p_route.nodes.insert(p_pos, p_task.id)
        else:
            # 找不到可行插入：就近强制建新仓路线（候选会被 is_feasible 拒绝）
            sat_id = min(evaluator.sat_ids,
                         key=lambda s: evaluator.dist(s, f"{cust}d"))
            sol.e2_routes.setdefault(sat_id, []).append(
                Route(sat_id, [d_task.id, p_task.id]))

    evaluator.rebuild_from_solution(sol)
    clean_empty_routes(sol)


def multi_sat_insertion(sol, removed, evaluator, echelon="e2", k=2):
    """把被移除的顾客成对（同车）插回任意前置仓的最优位置。"""
    pending = list(removed)
    random.shuffle(pending)
    for cust in pending:
        evaluator.rebuild_from_solution(sol)
        start_by_sat = evaluator.e2_start_times(sol)
        pair = evaluator.catalog[cust]
        task_ids = [pair["d"].id, pair["p"].id]

        best = None  # (cost_delta, sat_id, route, positions)
        for sat_id in evaluator.sat_ids:
            start_time = start_by_sat.get(sat_id, 0.0)
            routes = sol.e2_routes.get(sat_id, [])
            best_here = _best_e2_item_insert(
                routes, task_ids, evaluator, sat_id, start_time)
            if best_here is not None:
                inc, route, positions = best_here
                if best is None or inc < best[0] - FEAS_TOL:
                    best = (inc, sat_id, route, positions)
            if len(routes) < evaluator.vehicles_per_satellite:
                new_route = Route(sat_id, list(task_ids))
                if evaluator.check_e2_route_feasible(
                        new_route, sat_id, start_time):
                    inc = evaluator.route_distance(new_route)
                    if best is None or inc < best[0] - FEAS_TOL:
                        best = (inc, sat_id, None, None)

        if best is not None:
            inc, sat_id, route, positions = best
            if route is None:
                sol.e2_routes.setdefault(sat_id, []).append(
                    Route(sat_id, list(task_ids)))
            else:
                _apply_positions(route, task_ids, positions)
        else:
            # 找不到可行插入：就近强制建新仓路线（候选会被 is_feasible 拒绝）
            sat_id = min(evaluator.sat_ids,
                         key=lambda s: evaluator.dist(s, f"{cust}d"))
            sol.e2_routes.setdefault(sat_id, []).append(
                Route(sat_id, list(task_ids)))

    evaluator.rebuild_from_solution(sol)
    clean_empty_routes(sol)


# =====================================================================
#  E1 局部搜索（M3 的 e1 echelon 有类似作用，M4 需自行补上）
# =====================================================================

def _e1_local_improve(sol, evaluator, max_rounds=6):
    """在配送节点集合不变的前提下对 E1 路线做 2-opt 交换。"""
    rounds = 0
    while rounds < max_rounds:
        rounds += 1
        improved = False
        for r in sol.e1_routes:
            nodes = r.nodes
            n = len(nodes)
            if n < 2:
                continue
            for i in range(n - 1):
                for j in range(i + 1, n):
                    t_i = evaluator.e1_task_map.get(nodes[i])
                    t_j = evaluator.e1_task_map.get(nodes[j])
                    if t_i is None or t_j is None:
                        continue
                    if t_i.kind != "d" or t_j.kind != "d":
                        continue
                    old_cost = evaluator.route_distance(r)
                    nodes[i], nodes[j] = nodes[j], nodes[i]
                    ok = evaluator.check_e1_route_feasible(r)
                    new_cost = evaluator.route_distance(r) if ok else None
                    if ok and new_cost < old_cost - FEAS_TOL:
                        improved = True
                    else:
                        nodes[i], nodes[j] = nodes[j], nodes[i]
        if not improved:
            break


class M4Hooks(ALNSHooks):
    """每个候选算子后做一次 E1 2-opt 局部改进。"""

    def after_operators(self, candidate, evaluator, current_solution,
                        iteration, d_idx, r_idx):
        _e1_local_improve(candidate, evaluator)


# =====================================================================
#  ALNS 主循环
# =====================================================================

def solve_alns(problem, vehicles_per_satellite, TFp=9999.0,
               max_iterations=5000, max_time=120, seed=42, verbose=True):
    random.seed(seed)
    start_time = time.time()

    evaluator = Evaluator(problem, TFp, vehicles_per_satellite)
    sol = build_initial_solution(problem, evaluator, TFp)

    if not evaluator.is_feasible(sol):
        if sol is None or not any(
                r.nodes for routes in sol.e2_routes.values() for r in routes):
            if verbose:
                print("\n[WARN] Model 4 无法构造含顾客的初始解")
            return None, None, None
        if verbose:
            print("\n[WARN] Model 4 初始解不满足全局约束，"
                  "从该种子继续搜索（分配自由度可恢复可行性）")

    sol.cost = evaluator.total_cost(sol)
    best_sol = sol.copy()
    best_cost = sol.cost

    if verbose:
        print(f"\n{'=' * 60}")
        print("ALNS 求解器 (Model 4 自由分配 取送货)")
        print(f"{'=' * 60}")
        print(f"初始解目标值: {sol.cost:.2f}")
        print(f"初始解可行性: {evaluator.is_feasible(sol)}")
        print(f"E1 路径数: {len(sol.e1_routes)}")
        e2_count = sum(len(v) for v in sol.e2_routes.values())
        print(f"E2 路径数: {e2_count}")
        assign = evaluator.assignment(sol)
        print(f"初始分配: " + ", ".join(
            f"{s}:{sum(1 for v in assign.values() if v == s)}"
            for s in evaluator.sat_ids))

    result = run_alns_search(
        initial_solution=sol,
        evaluator=evaluator,
        destroy_ops=[
            random_customer_removal,
            worst_customer_removal,
            shaw_customer_removal,
        ],
        repair_ops=[multi_sat_insertion, multi_sat_insertion_cross],
        config=ALNSConfig(
            max_iterations=max_iterations,
            max_time=max_time,
            e2_probability=1.0,
        ),
        hooks=M4Hooks(),
        verbose=verbose,
        start_time=start_time,
    )
    best_sol = result.best_solution
    best_cost = result.best_cost
    elapsed = result.elapsed

    if verbose:
        print(f"\n{'=' * 60}")
        print("ALNS 求解完成")
        print(f"最优目标值: {best_cost:.2f}")
        print(f"总耗时: {elapsed:.2f} 秒")
        assign = evaluator.assignment(best_sol)
        print("最终分配: " + ", ".join(
            f"{s}:{sum(1 for v in assign.values() if v == s)}"
            for s in evaluator.sat_ids))
        print(f"{'=' * 60}")

    e1_routes_out = [r.full_path() for r in best_sol.e1_routes]
    e2_routes_out = {
        sat_id: [r.full_path() for r in routes]
        for sat_id, routes in best_sol.e2_routes.items()
    }
    return e1_routes_out, e2_routes_out, evaluator.assignment(best_sol)
