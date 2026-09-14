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

from models.routing.common.domain.node import Task
from models.routing.alns.types import (
    Route,
    Solution,
    FEAS_TOL,
    clean_empty_routes,
    is_tfp_active,
)
from models.routing.alns.engine import (
    ALNSConfig,
    ALNSHooks,
    evaluate_solution,
    run_alns_search,
)
from models.routing.alns.assignment_operators import (
    assignment_boundary_removal,
    two_satellite_removal,
)
from models.routing.alns.initial_solution import (
    build_strong_initial_solution,
    _apply_positions,
    _best_e2_item_insert,
    _build_e1_delivery_routes,
    _insert_e1_pickups_after_e2_returns,
)
from models.routing.common.task_builder import build_tasks_for_model
from models.routing.alns.variants.model_3 import Evaluator as _M3Evaluator
from models.routing.alns.variants import model_3 as _fixed_search


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
        self._active_assignment = None
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
        if assign == self._active_assignment:
            # Routes and E2 return times may have changed, even when aggregate
            # demands have not. Reuse tasks only, never the E1 schedule.
            self._canonicalize_e1_routes(sol)
            return
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

        self._active_assignment = dict(assign)
        self._canonicalize_e1_routes(sol)

    def _canonicalize_e1_routes(self, sol):
        """按当前分配重建 E1 路线：先配送后回收，回收需晚于 E2 返回。"""
        depot_id = self.problem.depot.id
        required_del = {
            nid: self.e1_task_map[nid]
            for nid in self.e1_del_nodes
        }

        # A fixed-assignment M3 incumbent is already a valid member of M4's
        # feasible set.  Preserve its complete E1 route structure when it is
        # still feasible under the rebuilt task catalogue.  The old logic
        # unconditionally removed and greedily reinserted every pickup, which
        # could make a warm-started M4 objective worse before ALNS even began.
        required_all = set(self.e1_task_map)
        current_all = [
            nid
            for route in sol.e1_routes
            for nid in route.nodes
        ]
        complete = (
            len(sol.e1_routes) <= self.problem.e1_vehicle_count
            and len(current_all) == len(required_all)
            and set(current_all) == required_all
        )
        if complete and all(
                self.check_e1_route_feasible(route)
                for route in sol.e1_routes):
            base_departures, _ = self.e1_schedule(sol)
            if base_departures is not None:
                e2_returns = self._e2_returns(sol)
                waited_departures, _ = self.e1_schedule(sol, e2_returns)
                if (waited_departures is not None
                        and all(abs(waited_departures.get(sat, dep) - dep)
                                <= FEAS_TOL
                                for sat, dep in base_departures.items())):
                    return

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

    def total_cost(self, sol, _rebuild=True):
        if _rebuild:
            self.rebuild_from_solution(sol)
        cost = 0.0
        for r in sol.e1_routes:
            cost += self.route_distance(r)
        for sat_id, routes in sol.e2_routes.items():
            for r in routes:
                cost += self.route_distance(r)
        return cost

    def evaluate(self, sol):
        """Return cost and feasibility after one assignment rebuild."""
        self.rebuild_from_solution(sol)
        cost = self.total_cost(sol, _rebuild=False)
        return cost, self.is_feasible(sol, _rebuild=False)

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

    def e1_schedule(self, sol, pickup_ready=None):
        pickup_ready = pickup_ready or {}
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
                    if task.kind == "p":
                        current_time = max(
                            current_time, pickup_ready.get(task.cust, 0.0))
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

    def is_feasible(self, sol, _rebuild=True):
        if _rebuild:
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

        waited_departures, _ = self.e1_schedule(sol, e2_last_return_by_sat)
        if waited_departures is None:
            return False
        if any(abs(waited_departures.get(sat, dep) - dep) > FEAS_TOL
               for sat, dep in e1_departures.items()):
            return False

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


def _deadline_repaired_assignment(problem, fixed):
    """Move only assignments that miss their due time even on a direct path."""
    repaired = dict(fixed)
    depot_id = problem.depot.id

    def direct_arrival(customer, satellite):
        service = getattr(problem.nodes[satellite.id], "service_time", 0.0)
        return (
            problem.travel_time(depot_id, satellite.id)
            + service
            + problem.travel_time(satellite.id, customer.id)
        )

    for customer in problem.customers:
        current = repaired.get(customer.id)
        current_sat = next(
            (sat for sat in problem.satellites if sat.id == current), None)
        if current_sat is None:
            continue
        due = customer.tw_del[1]
        if direct_arrival(customer, current_sat) <= due + FEAS_TOL:
            continue
        repaired[customer.id] = min(
            problem.satellites,
            key=lambda sat: direct_arrival(customer, sat),
        ).id
    return repaired


def _candidate_seeds(problem):
    """初始分配的候选种子：数据固定分配 + 各阶最近前置仓。"""
    seeds = []
    fixed = _fixed_assignment(problem)
    if fixed:
        seeds.append(fixed)
        seeds.append(_deadline_repaired_assignment(problem, fixed))

    nearest = {}
    second = {}
    for c in problem.customers:
        sats = sorted(problem.satellites,
                      key=lambda s: problem.distance(c.id, s.id))
        nearest[c.id] = sats[0].id
        if len(sats) > 1:
            second[c.id] = sats[1].id
    seeds.append(nearest)
    if second and len(problem.customers) <= 20:
        seeds.append(second)
    if len(problem.customers) <= 20:
        for satellite in problem.satellites:
            seeds.append({c.id: satellite.id for c in problem.customers})
    else:
        # Do not trap a large free-assignment search in an infeasible input
        # partition.  The nearest-satellite seed provides a deterministic
        # feasibility escape while the fixed seed preserves the baseline.
        seeds = ([fixed, _deadline_repaired_assignment(problem, fixed), nearest]
                 if fixed else [nearest])

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
    best_feasible = None
    best_any = None
    for seed in _candidate_seeds(problem):
        e1_tasks, e2_tasks_by_sat = build_tasks_from_assignment(problem, seed)
        m3_eval = _M3Evaluator(problem, e1_tasks, e2_tasks_by_sat, TFp, vps)
        sol = build_strong_initial_solution(
            problem, e1_tasks, e2_tasks_by_sat, m3_eval)
        evaluator.rebuild_from_solution(sol)
        sol.cost = evaluator.total_cost(sol)
        if evaluator.is_feasible(sol):
            if (best_feasible is None
                    or sol.cost < best_feasible.cost - FEAS_TOL):
                best_feasible = sol.copy()
        if best_any is None or sol.cost < best_any.cost - FEAS_TOL:
            best_any = sol.copy()
    return best_feasible or best_any


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


def satellite_cluster_removal(sol, evaluator, num_remove, echelon="e2"):
    """Remove every delivery/pickup pair assigned to one satellite."""
    assignment = evaluator.assignment(sol)
    by_sat = {}
    for cust, sat_id in assignment.items():
        by_sat.setdefault(sat_id, []).append(cust)
    if not by_sat:
        return []
    sat_id = random.choice(sorted(by_sat))
    removed = by_sat[sat_id]
    for cust in removed:
        _remove_customer(sol, cust)
    return removed


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
    deadline = getattr(evaluator, "_alns_deadline", float("inf"))
    for r in routes:
        if time.time() >= deadline:
            break
        old_cost = evaluator.route_distance(r)
        for pos in range(len(r.nodes) + 1):
            if time.time() >= deadline:
                break
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


def _copy_solution_into(target, source):
    target.e1_routes = [route.copy() for route in source.e1_routes]
    target.e2_routes = {
        sat: [route.copy() for route in routes]
        for sat, routes in source.e2_routes.items()
    }
    target.cost = source.cost


def _insert_pair_cross_at_sat(sol, evaluator, cust, sat_id):
    """Insert one d/p pair at a fixed satellite, allowing different routes.

    Every feasible delivery position is retained until the pickup has been
    inserted.  This avoids the former dangling-route bug when the delivery
    opened a new route and also captures cases where the cheapest delivery
    insertion leaves no good pickup insertion.
    """
    pair = evaluator.catalog[cust]
    d_task, p_task = pair["d"], pair["p"]
    deadline = getattr(evaluator, "_alns_deadline", float("inf"))
    if time.time() >= deadline:
        return None
    routes = sol.e2_routes.get(sat_id, [])
    delivery_options = [
        (route_idx, pos)
        for route_idx, route in enumerate(routes)
        for pos in range(len(route.nodes) + 1)
    ]
    if len(routes) < evaluator.vehicles_per_satellite:
        delivery_options.append((None, None))

    # Exhaustive enumeration is useful for the certified small benchmark, but
    # becomes too costly inside a large-instance ALNS iteration.  On larger
    # instances retain the geometrically best delivery positions plus the
    # new-route option; pickup positions are still checked exactly afterwards.
    if len(evaluator.catalog) > 20 and len(delivery_options) > 12:
        def approximate_delta(option):
            route_idx, pos = option
            if route_idx is None:
                return 2.0 * evaluator.dist(sat_id, d_task.id)
            nodes = routes[route_idx].nodes
            prev_id = sat_id if pos == 0 else nodes[pos - 1]
            next_id = sat_id if pos == len(nodes) else nodes[pos]
            return (evaluator.dist(prev_id, d_task.id)
                    + evaluator.dist(d_task.id, next_id)
                    - evaluator.dist(prev_id, next_id))

        delivery_options.sort(key=approximate_delta)
        delivery_options = delivery_options[:12]

    # Every option below assigns the same customer to the same satellite.
    # Consequently the aggregated E1 delivery tasks and this satellite's E2
    # departure time are identical; only the position inside an E2 route
    # changes.  Build that assignment state once instead of rebuilding E1 for
    # every delivery position.  Final candidates are still fully rebuilt by
    # ``total_cost`` after pickup insertion, so E2 return-dependent E1 pickup
    # placement and all downstream feasibility checks remain unchanged.
    assignment_template = sol.copy()
    template_routes = assignment_template.e2_routes.setdefault(sat_id, [])
    if template_routes:
        template_routes[0].nodes.append(d_task.id)
    else:
        template_routes.append(Route(sat_id, [d_task.id]))
    evaluator.rebuild_from_solution(assignment_template)
    start_time = evaluator.e2_start_times(
        assignment_template).get(sat_id, 0.0)

    best = None
    for route_idx, pos in delivery_options:
        if time.time() >= deadline:
            break
        candidate = sol.copy()
        candidate_routes = candidate.e2_routes.setdefault(sat_id, [])
        if route_idx is None:
            candidate_routes.append(Route(sat_id, [d_task.id]))
            d_route = candidate_routes[-1]
        else:
            candidate_routes[route_idx].nodes.insert(pos, d_task.id)
            d_route = candidate_routes[route_idx]

        feasible, arrivals, _ = evaluator.simulate_e2_route(
            d_route, sat_id, start_time)
        if not feasible or d_task.id not in arrivals:
            continue
        d_arrival = arrivals[d_task.id]
        p_best = _best_task_insert(
            candidate, evaluator, sat_id, p_task, start_time,
            after=(d_task.id, d_arrival,
                   evaluator.service_time(d_task.id)))
        if p_best is None:
            continue
        _inc, p_route, p_pos = p_best
        if p_route is None:
            candidate.e2_routes.setdefault(sat_id, []).append(
                Route(sat_id, [p_task.id]))
        else:
            p_route.nodes.insert(p_pos, p_task.id)
        clean_empty_routes(candidate)
        candidate.cost = evaluator.total_cost(candidate)
        if best is None or candidate.cost < best.cost - FEAS_TOL:
            best = candidate
    return best


def multi_sat_insertion_cross(sol, removed, evaluator, echelon="e2", k=2):
    """Insert d/p pairs at any satellite and allow cross-route precedence."""
    pending = list(removed)
    random.shuffle(pending)
    for cust in pending:
        if time.time() >= getattr(
                evaluator, "_alns_deadline", float("inf")):
            break
        best = None
        satellites = list(evaluator.sat_ids)
        random.shuffle(satellites)
        if len(evaluator.catalog) > 20 and len(satellites) > 4:
            d_task = evaluator.catalog[cust]["d"]

            def satellite_lower_bound(sat_id):
                routes = sol.e2_routes.get(sat_id, [])
                best_delta = 2.0 * evaluator.dist(sat_id, d_task.id)
                for route in routes:
                    for pos in range(len(route.nodes) + 1):
                        prev_id = sat_id if pos == 0 else route.nodes[pos - 1]
                        next_id = (sat_id if pos == len(route.nodes)
                                   else route.nodes[pos])
                        delta = (
                            evaluator.dist(prev_id, d_task.id)
                            + evaluator.dist(d_task.id, next_id)
                            - evaluator.dist(prev_id, next_id)
                        )
                        best_delta = min(best_delta, delta)
                return best_delta

            satellites.sort(key=satellite_lower_bound)
            satellites = satellites[:4]
        for sat_id in satellites:
            candidate = _insert_pair_cross_at_sat(
                sol, evaluator, cust, sat_id)
            if (candidate is not None
                    and (best is None
                         or candidate.cost < best.cost - FEAS_TOL)):
                best = candidate
        if best is not None:
            _copy_solution_into(sol, best)
        else:
            sat_id = min(evaluator.sat_ids,
                         key=lambda s: evaluator.dist(s, f"{cust}d"))
            pair = evaluator.catalog[cust]
            sol.e2_routes.setdefault(sat_id, []).append(Route(
                sat_id, [pair["d"].id, pair["p"].id]))

    evaluator.rebuild_from_solution(sol)
    clean_empty_routes(sol)


def multi_sat_insertion(sol, removed, evaluator, echelon="e2", k=2):
    """把被移除的顾客成对（同车）插回任意前置仓的最优位置。"""
    pending = list(removed)
    random.shuffle(pending)
    for cust in pending:
        if time.time() >= getattr(
                evaluator, "_alns_deadline", float("inf")):
            break
        evaluator.rebuild_from_solution(sol)
        start_by_sat = evaluator.e2_start_times(sol)
        pair = evaluator.catalog[cust]
        task_ids = [pair["d"].id, pair["p"].id]

        best = None  # (cost_delta, sat_id, route, positions)
        for sat_id in evaluator.sat_ids:
            if time.time() >= getattr(
                    evaluator, "_alns_deadline", float("inf")):
                break
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


def randomized_satellite_insertion(sol, removed, evaluator,
                                   echelon="e2", k=2):
    """Diversification repair that forces customers into random satellites."""
    pending = list(removed)
    random.shuffle(pending)
    for cust in pending:
        if time.time() >= getattr(
                evaluator, "_alns_deadline", float("inf")):
            break
        evaluator.rebuild_from_solution(sol)
        starts = evaluator.e2_start_times(sol)
        pair = evaluator.catalog[cust]
        task_ids = [pair["d"].id, pair["p"].id]
        satellites = list(evaluator.sat_ids)
        random.shuffle(satellites)
        inserted = False
        for sat_id in satellites:
            if time.time() >= getattr(
                    evaluator, "_alns_deadline", float("inf")):
                break
            routes = sol.e2_routes.get(sat_id, [])
            best_here = _best_e2_item_insert(
                routes, task_ids, evaluator, sat_id,
                starts.get(sat_id, 0.0))
            if best_here is not None:
                _inc, route, positions = best_here
                _apply_positions(route, task_ids, positions)
                inserted = True
                break
            if len(routes) < evaluator.vehicles_per_satellite:
                new_route = Route(sat_id, list(task_ids))
                if evaluator.check_e2_route_feasible(
                        new_route, sat_id, starts.get(sat_id, 0.0)):
                    sol.e2_routes.setdefault(sat_id, []).append(new_route)
                    inserted = True
                    break
        if not inserted:
            sat_id = min(evaluator.sat_ids,
                         key=lambda sat: evaluator.dist(sat, pair["d"].id))
            sol.e2_routes.setdefault(sat_id, []).append(
                Route(sat_id, list(task_ids)))
    evaluator.rebuild_from_solution(sol)
    clean_empty_routes(sol)


def consolidated_satellite_insertion(sol, removed, evaluator,
                                     echelon="e2", k=2):
    """Insert all removed customer pairs into one selected satellite."""
    satellites = list(evaluator.sat_ids)
    random.shuffle(satellites)
    pending = list(removed)
    random.shuffle(pending)
    for target_sat in satellites:
        if time.time() >= getattr(
                evaluator, "_alns_deadline", float("inf")):
            break
        candidate = sol.copy()
        feasible = True
        for cust in pending:
            inserted = _insert_pair_cross_at_sat(
                candidate, evaluator, cust, target_sat)
            if inserted is None:
                feasible = False
                break
            candidate = inserted
        if feasible:
            _, candidate_feasible = evaluate_solution(evaluator, candidate)
            if candidate_feasible:
                _copy_solution_into(sol, candidate)
                return
    multi_sat_insertion(sol, pending, evaluator, echelon=echelon, k=k)


# =====================================================================
#  E1 局部搜索（M3 的 e1 echelon 有类似作用，M4 需自行补上）
# =====================================================================

def _e1_local_improve(sol, evaluator, max_rounds=3, deadline=None):
    """在配送节点集合不变的前提下对 E1 路线做 2-opt 交换。"""
    rounds = 0
    while rounds < max_rounds:
        rounds += 1
        improved = False
        for r in sol.e1_routes:
            if deadline is not None and time.time() >= deadline:
                return
            nodes = r.nodes
            n = len(nodes)
            if n < 2:
                continue
            for i in range(n - 1):
                for j in range(i + 1, n):
                    if deadline is not None and time.time() >= deadline:
                        return
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


def _assignment_first_improvement(sol, evaluator):
    """Relocate one complete d/p pair using full E1+E2 objective cost."""
    base_cost = evaluator.total_cost(sol)
    assignment = evaluator.assignment(sol)
    customers = list(assignment)
    random.shuffle(customers)
    deadline = getattr(evaluator, "_alns_deadline", float("inf"))
    for customer in customers:
        if time.time() >= deadline:
            break
        current_sat = assignment[customer]
        removed = sol.copy()
        _remove_customer(removed, customer)
        evaluator.rebuild_from_solution(removed)
        alternatives = [sat for sat in evaluator.sat_ids
                        if sat != current_sat]
        random.shuffle(alternatives)
        for sat_id in alternatives:
            if time.time() >= deadline:
                break
            candidate = _insert_pair_cross_at_sat(
                removed, evaluator, customer, sat_id)
            if candidate is None:
                continue
            _, feasible = evaluate_solution(evaluator, candidate)
            if (candidate.cost < base_cost - FEAS_TOL and feasible):
                return candidate
    evaluator.rebuild_from_solution(sol)
    return None


def _assignment_local_improve(sol, evaluator, max_rounds=3):
    sol.cost = evaluator.total_cost(sol)
    for _ in range(max_rounds):
        candidate = _assignment_first_improvement(sol, evaluator)
        if candidate is None:
            break
        _copy_solution_into(sol, candidate)


class M4Hooks(ALNSHooks):
    """Mix fast route moves with assignment moves, then audit through M4.

    The fixed evaluator is rebuilt from the complete candidate before any
    removal. It belongs to this attempt only: rejected assignments must never
    leak task maps or insertion options into the next attempt.
    """

    def __init__(self, fixed_route_probability=0.8):
        self.fixed_route_probability = float(fixed_route_probability)
        if not 0.0 <= self.fixed_route_probability <= 1.0:
            raise ValueError("fixed_route_probability must be in [0, 1]")
        self.neighborhood_attempts = {"fixed_routes": 0, "assignment": 0}

    def apply_operators(self, candidate, evaluator, destroy_op, repair_op,
                        d_idx, r_idx, num_remove, echelon):
        use_fixed = (self.fixed_route_probability > 0
                     and random.random() < self.fixed_route_probability
                     and len(evaluator.assignment(candidate)) == len(evaluator.catalog))
        if not use_fixed:
            self.neighborhood_attempts["assignment"] += 1
            return super().apply_operators(
                candidate, evaluator, destroy_op, repair_op,
                d_idx, r_idx, num_remove, echelon)

        self.neighborhood_attempts["fixed_routes"] += 1
        evaluator.rebuild_from_solution(candidate)
        fixed = _M3Evaluator(
            evaluator.problem, list(evaluator.e1_task_map.values()),
            evaluator.e2_tasks_by_sat, evaluator.TFp,
            evaluator.vehicles_per_satellite)
        fixed._alns_deadline = getattr(evaluator, "_alns_deadline", float("inf"))
        destroys = (
            _fixed_search.random_removal, _fixed_search.worst_removal,
            _fixed_search.shaw_removal, _fixed_search.paired_customer_removal,
            _fixed_search.route_customer_pair_removal,
            _fixed_search.route_customer_pair_removal,
        )
        repairs = (
            _fixed_search.greedy_insertion, _fixed_search.regret_insertion,
            _fixed_search.paired_insertion, _fixed_search.greedy_insertion,
        )
        fixed_echelon = "e2" if random.random() < 0.8 else "e1"
        removed = destroys[d_idx % len(destroys)](
            candidate, fixed, num_remove, fixed_echelon)
        repairs[r_idx % len(repairs)](candidate, removed, fixed, fixed_echelon)
        clean_empty_routes(candidate)

    def after_operators(self, candidate, evaluator, current_solution,
                        iteration, d_idx, r_idx):
        customer_count = len(evaluator.problem.customers)
        cadence = 1 if customer_count <= 20 else 5
        if iteration % cadence == 0:
            _e1_local_improve(
                candidate, evaluator,
                max_rounds=3 if customer_count <= 20 else 1,
                deadline=getattr(evaluator, "_alns_deadline", None),
            )
        if customer_count <= 20 and iteration % 100 == 0:
            _assignment_local_improve(candidate, evaluator, max_rounds=1)


# =====================================================================
#  ALNS 主循环
# =====================================================================

def solve_alns(problem, vehicles_per_satellite, TFp=9999.0,
               max_iterations=5000, max_time=120, seed=42, verbose=True,
               return_info=False, initial_solution=None,
               target_objective=None, target_tolerance=1e-6,
               alns_config_overrides=None, fixed_route_probability=0.8,
               search_phase_fraction=1.0, exchange_polish=None):
    """Optionally restart learning from the incumbent within one total budget.

    A fraction below one gives the first cooling phase that share of the
    wall-clock budget. The second phase retains its best feasible solution,
    resets operator weights/temperature, and uses only remaining time/iterations.
    The default remains one phase; a split is an experimental opt-in.
    """
    if not 0.0 < search_phase_fraction <= 1.0:
        raise ValueError("search_phase_fraction must be in (0, 1]")
    random.seed(seed)
    start_time = time.time()
    if exchange_polish is None:
        exchange_polish = (len(problem.customers) <= 20
                           and (alns_config_overrides or {}).get("cooling_schedule", "time") == "time")
    polish_reserve = min(0.05, max(0.0, max_time * 0.01)) if exchange_polish else 0.0
    search_time = max_time - polish_reserve

    evaluator = Evaluator(problem, TFp, vehicles_per_satellite)
    sol = (initial_solution.copy() if initial_solution is not None
           else build_initial_solution(problem, evaluator, TFp))

    if not evaluator.is_feasible(sol):
        if sol is None or not any(
                r.nodes for routes in sol.e2_routes.values() for r in routes):
            if verbose:
                print("\n[WARN] Model 4 无法构造含顾客的初始解")
            info = {"objective": None, "runtime": time.time() - start_time,
                    "iterations": 0, "feasible": False, "status": "NO_SOLUTION"}
            return ((None, None, None, info) if return_info
                    else (None, None, None))
        if verbose:
            print("\n[WARN] Model 4 初始解不满足全局约束，"
                  "从该种子继续搜索（分配自由度可恢复可行性）")

    sol.cost = evaluator.total_cost(sol)
    initial_objective = float(sol.cost)
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

    search_hooks = M4Hooks(fixed_route_probability)
    destroy_ops = [
        random_customer_removal,
        worst_customer_removal,
        shaw_customer_removal,
        satellite_cluster_removal,
        assignment_boundary_removal,
        two_satellite_removal,
    ]
    repair_ops = [multi_sat_insertion, multi_sat_insertion_cross,
                  randomized_satellite_insertion, consolidated_satellite_insertion]

    def search(initial, deadline_seconds, iterations):
        return run_alns_search(
            initial_solution=initial, evaluator=evaluator,
            destroy_ops=destroy_ops, repair_ops=repair_ops,
            config=ALNSConfig(
                max_iterations=iterations, max_time=deadline_seconds,
                e2_probability=1.0, target_cost=target_objective,
                target_tolerance=target_tolerance,
                **(alns_config_overrides or {})),
            hooks=search_hooks, verbose=verbose, start_time=start_time)

    result = search(sol, search_time * search_phase_fraction, max_iterations)
    phase_results = [result]
    if (search_phase_fraction < 1.0 and result.best_solution is not None
            and result.target_hit_time is None and result.iterations < max_iterations
            and time.time() - start_time < search_time - 1e-3):
        first = result
        result = search(first.best_solution.copy(), search_time,
                        max_iterations - first.iterations)
        phase_results.append(result)
        # Never lose a feasible incumbent even if a later phase is interrupted.
        if result.best_solution is None or result.best_cost >= first.best_cost:
            result.best_solution = first.best_solution
            result.best_cost = first.best_cost
            result.best_found_time = first.best_found_time
    best_sol = result.best_solution
    if best_sol is None:
        info = {"objective": None, "runtime": result.elapsed,
                "iterations": result.iterations, "feasible": False,
                "status": "NO_SOLUTION"}
        return ((None, None, None, info) if return_info
                else (None, None, None))
    best_cost = result.best_cost
    polish_improvements = 0
    polish_started = time.time()
    if (exchange_polish and result.target_hit_time is None
            and polish_started < start_time + max_time):
        from models.routing.alns.route_exchange import improve_route_exchanges

        evaluator._alns_deadline = start_time + max_time
        polished, polish_improvements = improve_route_exchanges(
            best_sol, evaluator, start_time + max_time)
        if polished.cost < best_cost - FEAS_TOL:
            best_sol, best_cost = polished, polished.cost
            result.best_found_time = time.time() - start_time
            if target_objective is not None and best_cost <= target_objective + target_tolerance:
                result.target_hit_time = result.best_found_time
                result.termination_reason = "target"
    polish_elapsed = time.time() - polish_started
    elapsed = time.time() - start_time

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
    assignment = evaluator.assignment(best_sol)
    feasible = bool(evaluator.is_feasible(best_sol))
    diagnostics = dict(result.diagnostics)
    if len(phase_results) > 1:
        for field in ("weight_updates", "online_weight_updates",
                      "temperature_positive_delta_samples"):
            diagnostics[field] = sum(p.diagnostics.get(field, 0) for p in phase_results)
        diagnostics["temperature_calibrated"] = any(
            p.diagnostics.get("temperature_calibrated", False) for p in phase_results)
        pairs = {}
        for phase in phase_results:
            for entry in phase.diagnostics.get("operator_pairs", []):
                key = (entry["destroy_index"], entry["repair_index"])
                previous = pairs.get(key)
                merged = dict(entry)
                if previous is not None:
                    for field in ("attempts", "feasible", "accepted", "improving",
                                  "new_best", "infeasible", "runtime"):
                        merged[field] += previous[field]
                merged["feasible_rate"] = (merged["feasible"] / merged["attempts"]
                                           if merged["attempts"] else 0.0)
                merged["acceptance_rate"] = (merged["accepted"] / merged["feasible"]
                                             if merged["feasible"] else 0.0)
                pairs[key] = merged
        diagnostics["operator_pairs"] = list(pairs.values())
    info = {
        "objective": float(best_cost), "runtime": float(elapsed),
        "iterations": sum(p.iterations for p in phase_results), "feasible": feasible,
        "status": "FEASIBLE" if feasible else "NO_SOLUTION",
        "external_initial_solution": initial_solution is not None,
        "initial_objective": initial_objective,
        "best_found_time": result.best_found_time,
        "target_hit_time": result.target_hit_time,
        "termination_reason": result.termination_reason,
        "restarts": sum(p.restarts for p in phase_results),
        "local_search_improvements": sum(p.local_search_improvements for p in phase_results) + polish_improvements,
        "exchange_polish": bool(exchange_polish),
        "exchange_polish_improvements": polish_improvements,
        "exchange_polish_seconds": polish_elapsed,
        "alns_diagnostics": diagnostics,
        "search_phase_fraction": search_phase_fraction,
        "search_phases": len(phase_results),
        "phase_diagnostics": [dict(iterations=p.iterations, elapsed=p.elapsed,
                                   diagnostics=p.diagnostics) for p in phase_results],
        "fixed_route_probability": search_hooks.fixed_route_probability,
        "neighborhood_attempts": dict(search_hooks.neighborhood_attempts),
    }
    return ((e1_routes_out, e2_routes_out, assignment, info) if return_info
            else (e1_routes_out, e2_routes_out, assignment))
