"""Model 3-specific ALNS evaluator and operators."""
# -*- coding: utf-8 -*-
"""
Model 3 (取送货) 的 ALNS 求解器
"""
import random
import time

from models.routing.alns.types import (
    Route, Solution, FEAS_TOL, clean_empty_routes,
    is_tfp_active,
)
from models.routing.alns.engine import ALNSConfig, run_alns_search
from models.routing.alns.fixed_repair import FixedRouteInsertionCache
from models.routing.alns.initial_solution import build_strong_initial_solution
from models.routing.alns.paired_operators import (
    insert_complete_customer_pairs,
    route_customer_pair_removal,
)


# =====================================================================
#  评估器
# =====================================================================

class Evaluator:
    def __init__(self, problem, e1_tasks, e2_tasks_by_sat, TFp=9999.0,
                 vehicles_per_satellite=2):
        self.problem = problem
        self.TFp = TFp
        self.vehicles_per_satellite = vehicles_per_satellite

        self.e1_task_map = {t.id: t for t in e1_tasks}
        self.e2_task_map = {}
        self.e2_task_sat = {}
        self.e2_tasks_by_sat = e2_tasks_by_sat
        for sat_id, tasks in e2_tasks_by_sat.items():
            for t in tasks:
                self.e2_task_map[t.id] = t
                self.e2_task_sat[t.id] = sat_id

        self.task_to_phys = {}
        for t in e1_tasks:
            self.task_to_phys[t.id] = t.cust
        for tasks in e2_tasks_by_sat.values():
            for t in tasks:
                self.task_to_phys[t.id] = t.cust

        self.e1_del_nodes = [t.id for t in e1_tasks if t.kind == 'd']
        self.e1_pick_nodes = [t.id for t in e1_tasks if t.kind == 'p']

        self.forbidden_e1 = set()
        sat_ids = [s.id for s in problem.satellites]
        for s_id in sat_ids:
            pid, did = f"{s_id}p", f"{s_id}d"
            if pid in self.e1_task_map and did in self.e1_task_map:
                self.forbidden_e1.add((pid, did))
        from models.routing.alns.evaluation_cache import cache_static_evaluator
        cache_static_evaluator(self)

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
        return getattr(node_obj, 'service_time', 0.0) if node_obj else 0.0

    def signed_demand(self, task):
        return task.w if task.kind == 'd' else -task.w

    def signed_volume(self, task):
        return task.v if task.kind == 'd' else -task.v

    def route_distance(self, route):
        path = route.full_path()
        total = 0.0
        for i in range(len(path) - 1):
            total += self.dist(path[i], path[i + 1])
        return total

    def total_cost(self, sol):
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
            if t and t.kind == 'd':
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
                if t.kind == 'p' and is_tfp_active(self.TFp) and current_time < self.TFp - FEAS_TOL:
                    return False

                Q_load -= self.signed_demand(t)
                V_load -= self.signed_volume(t)
                if Q_load < -FEAS_TOL or Q_load > self.problem.e1_weight_cap + FEAS_TOL:
                    return False
                if V_load < -FEAS_TOL or V_load > self.problem.e1_volume_cap + FEAS_TOL:
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
                    if task.kind == 'p':
                        current_time = max(
                            current_time, pickup_ready.get(task.cust, 0.0))
                    if current_time > task.tw[1] + FEAS_TOL:
                        return None, None
                    if task.kind == 'p' and is_tfp_active(self.TFp) and current_time < self.TFp - FEAS_TOL:
                        return None, None

                    if task.kind == 'd':
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
            t = self.e2_task_map.get(nid)
            if t and t.kind == 'd':
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
            t = self.e2_task_map.get(nid)
            if t:
                if abs_arrival < t.tw[0]:
                    current_time = t.tw[0] - start_time
                    abs_arrival = t.tw[0]
                if abs_arrival > t.tw[1] + FEAS_TOL:
                    return False, {}, None

                arrivals[nid] = abs_arrival

                Q_load -= self.signed_demand(t)
                V_load -= self.signed_volume(t)
                if Q_load < -FEAS_TOL or Q_load > self.problem.e2_weight_cap + FEAS_TOL:
                    return False, {}, None
                if V_load < -FEAS_TOL or V_load > self.problem.e2_volume_cap + FEAS_TOL:
                    return False, {}, None

                current_time += self.service_time(nid)
            prev = nid

        return_time = start_time + current_time + self.travel(prev, sat_id)
        return True, arrivals, return_time

    def check_e2_route_feasible(self, route, sat_id, start_time):
        feasible, _, _ = self.simulate_e2_route(route, sat_id, start_time)
        return feasible

    def is_feasible(self, sol):
        if len(sol.e1_routes) > self.problem.e1_vehicle_count:
            return False

        visited_e1 = []
        for r in sol.e1_routes:
            if not self.check_e1_route_feasible(r):
                return False
            visited_e1.extend(r.nodes)
        required_e1 = set(self.e1_task_map)
        if set(visited_e1) != required_e1:
            return False
        if len(visited_e1) != len(required_e1):
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
            if routes and any(t.kind == 'd' for t in self.e2_tasks_by_sat.get(sat_id, [])):
                if sat_id not in e1_departures:
                    return False

            visited_e2 = []
            for r in routes:
                feasible, arrivals, return_time = self.simulate_e2_route(
                    r, sat_id, start_time)
                if not feasible:
                    return False
                e2_arrivals.update(arrivals)
                if return_time is not None:
                    old_return = e2_last_return_by_sat.get(sat_id, 0.0)
                    e2_last_return_by_sat[sat_id] = max(old_return, return_time)
                visited_e2.extend(r.nodes)
            required = {t.id for t in self.e2_tasks_by_sat.get(sat_id, [])}
            if set(visited_e2) != required:
                return False
            if len(visited_e2) != len(required):
                return False

        for task_id, task in self.e2_task_map.items():
            if task.kind != 'p':
                continue
            d_id = f"{task.cust}d"
            if d_id not in self.e2_task_map:
                continue
            if task_id not in e2_arrivals or d_id not in e2_arrivals:
                return False
            if e2_arrivals[task_id] < e2_arrivals[d_id] + self.service_time(d_id) - FEAS_TOL:
                return False

        # E1 车辆可以提前到达并等待 E2 返回。等待必须向后
        # 传播；若它推迟了同一 E1 路线上的后续配送，则当前
        # 一次计算的 E2 出发时刻已失效，该候选解予以拒绝。
        waited_departures, _ = self.e1_schedule(sol, e2_last_return_by_sat)
        if waited_departures is None:
            return False
        if any(abs(waited_departures.get(sat, dep) - dep) > FEAS_TOL
               for sat, dep in e1_departures.items()):
            return False

        return True


# =====================================================================
#  初始解构造
# =====================================================================

def build_initial_solution(problem, e1_tasks, e2_tasks_by_sat, evaluator):
    sol = Solution()
    depot_id = problem.depot.id
    sat_ids = [s.id for s in problem.satellites]

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

    e2_start_by_sat, _ = evaluator.e1_schedule(sol)
    e2_start_by_sat = e2_start_by_sat or {}

    for sat_id in sat_ids:
        tasks = list(evaluator.e2_tasks_by_sat.get(sat_id, []))
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

        while unassigned:
            route = Route(sat_id)
            assigned_this_route = []
            start_time = e2_start_by_sat.get(sat_id, 0.0)

            for t in unassigned:
                test_route = Route(sat_id, route.nodes + [t.id])
                if evaluator.check_e2_route_feasible(test_route, sat_id,
                                                     start_time):
                    route.nodes.append(t.id)
                    assigned_this_route.append(t)

            for t in assigned_this_route:
                unassigned.remove(t)

            if route.nodes:
                routes.append(route)
            elif unassigned:
                forced = unassigned.pop(0)
                routes.append(Route(sat_id, [forced.id]))

        sol.e2_routes[sat_id] = routes

    sol.cost = evaluator.total_cost(sol)
    return sol


# =====================================================================
#  破坏算子
# =====================================================================

def random_removal(sol, evaluator, num_remove, echelon='e2'):
    removed = []
    if echelon == 'e2':
        all_nodes = []
        for sat_id, routes in sol.e2_routes.items():
            for r in routes:
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


def paired_customer_removal(sol, evaluator, num_remove, echelon='e2'):
    """Remove delivery and pickup of selected customers together."""
    if echelon != 'e2':
        return random_removal(sol, evaluator, num_remove, echelon)
    customers = {}
    for routes in sol.e2_routes.values():
        for route in routes:
            for node_id in route.nodes:
                task = evaluator.e2_task_map.get(node_id)
                if task is not None:
                    customers.setdefault(task.cust, set()).add(node_id)
    customer_ids = list(customers)
    random.shuffle(customer_ids)
    pair_count = max(1, (num_remove + 1) // 2)
    removed = []
    for cust_id in customer_ids[:pair_count]:
        task_ids = customers[cust_id]
        ordered = sorted(
            task_ids,
            key=lambda nid: 0 if evaluator.e2_task_map[nid].kind == 'd' else 1)
        for routes in sol.e2_routes.values():
            for route in routes:
                for node_id in ordered:
                    if node_id in route.nodes:
                        route.nodes.remove(node_id)
        removed.extend(ordered)
    clean_empty_routes(sol)
    return removed


# =====================================================================
#  修复算子
# =====================================================================

def greedy_insertion(sol, removed, evaluator, echelon='e2'):
    if echelon == 'e2':
        e2_start_by_sat = evaluator.e2_start_times(sol)
        for nid in removed:
            task = evaluator.e2_task_map.get(nid)
            if not task:
                continue
            sat_id = evaluator.e2_task_satellite(nid)
            if not sat_id:
                continue
            start_time = e2_start_by_sat.get(sat_id, 0.0)

            best_cost = float('inf')
            best_route = None
            best_pos = None

            routes = sol.e2_routes.get(sat_id, [])
            for r in routes:
                old_cost = evaluator.route_distance(r)
                for pos in range(len(r.nodes) + 1):
                    old_nodes = r.nodes[:]
                    r.nodes.insert(pos, nid)
                    if evaluator.check_e2_route_feasible(r, sat_id, start_time):
                        insert_cost = evaluator.route_distance(r) - old_cost
                        if insert_cost < best_cost:
                            best_cost = insert_cost
                            best_route = r
                            best_pos = pos
                    r.nodes = old_nodes

            if len(routes) < evaluator.vehicles_per_satellite:
                new_route = Route(sat_id, [nid])
                if evaluator.check_e2_route_feasible(
                        new_route, sat_id, start_time):
                    new_cost = evaluator.route_distance(new_route)
                    if new_cost < best_cost:
                        best_cost = new_cost
                        best_route = None
                        best_pos = None

            if best_pos is not None:
                best_route.nodes.insert(best_pos, nid)
            else:
                routes = sol.e2_routes.get(sat_id, [])
                max_v = evaluator.vehicles_per_satellite
                if len(routes) >= max_v:
                    best_r2, best_p2, best_c2 = None, 0, float('inf')
                    for r in routes:
                        for p in range(len(r.nodes) + 1):
                            old = r.nodes[:]
                            r.nodes.insert(p, nid)
                            c = evaluator.route_distance(r)
                            r.nodes = old
                            if c < best_c2:
                                best_c2, best_r2, best_p2 = c, r, p
                    if best_r2:
                        best_r2.nodes.insert(best_p2, nid)
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
                old_cost = evaluator.route_distance(r)
                for pos in range(len(r.nodes) + 1):
                    old_nodes = r.nodes[:]
                    r.nodes.insert(pos, nid)
                    if evaluator.check_e1_route_feasible(r):
                        insert_cost = evaluator.route_distance(r) - old_cost
                        if insert_cost < best_cost:
                            best_cost = insert_cost
                            best_route = r
                            best_pos = pos
                    r.nodes = old_nodes

            if len(sol.e1_routes) < evaluator.problem.e1_vehicle_count:
                new_route = Route(depot_id, [nid])
                if evaluator.check_e1_route_feasible(new_route):
                    new_cost = evaluator.route_distance(new_route)
                    if new_cost < best_cost:
                        best_cost = new_cost
                        best_route = None
                        best_pos = None

            if best_pos is not None:
                best_route.nodes.insert(best_pos, nid)
            else:
                sol.e1_routes.append(Route(depot_id, [nid]))


def regret_insertion(sol, removed, evaluator, echelon='e2', k=2):
    if echelon == 'e2':
        e2_start_by_sat = evaluator.e2_start_times(sol)
        pending = list(removed)
        insertion_cache = FixedRouteInsertionCache(evaluator)
        while pending:
            best_regret = -float('inf')
            best_nid = None
            best_route_info = None

            for nid in list(pending):
                task = evaluator.e2_task_map.get(nid)
                if not task:
                    pending.remove(nid)
                    continue

                sat_id = evaluator.e2_task_satellite(nid)
                if not sat_id:
                    pending.remove(nid)
                    continue
                start_time = e2_start_by_sat.get(sat_id, 0.0)

                insertion_costs = []
                routes = sol.e2_routes.get(sat_id, [])
                for r in routes:
                    insertion_costs.extend(insertion_cache.options(
                        r, nid, sat_id, start_time))

                if len(routes) < evaluator.vehicles_per_satellite:
                    new_route = Route(sat_id, [nid])
                    if evaluator.check_e2_route_feasible(
                            new_route, sat_id, start_time):
                        insertion_costs.append((
                            evaluator.route_distance(new_route), None, 0))

                if not insertion_costs:
                    continue

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
            if best_route_info is None:
                break
            pending.remove(best_nid)
            _, route, pos = best_route_info
            if route is not None:
                route.nodes.insert(pos, best_nid)
                insertion_cache.invalidate(route)
            else:
                sat_id = evaluator.e2_task_satellite(best_nid)
                if sat_id:
                    routes = sol.e2_routes.get(sat_id, [])
                    max_v = evaluator.vehicles_per_satellite
                    if len(routes) >= max_v:
                        best_r2, best_p2, best_c2 = None, 0, float('inf')
                        for r in routes:
                            for p in range(len(r.nodes) + 1):
                                old = r.nodes[:]
                                r.nodes.insert(p, best_nid)
                                c = evaluator.route_distance(r)
                                r.nodes = old
                                if c < best_c2:
                                    best_c2, best_r2, best_p2 = c, r, p
                        if best_r2:
                            best_r2.nodes.insert(best_p2, best_nid)
                    else:
                        new_route = Route(sat_id, [best_nid])
                        if sat_id not in sol.e2_routes:
                            sol.e2_routes[sat_id] = []
                        sol.e2_routes[sat_id].append(new_route)

    elif echelon == 'e1':
        greedy_insertion(sol, removed, evaluator, echelon='e1')


def paired_insertion(sol, removed, evaluator, echelon='e2', k=2):
    """Repair complete customer pairs jointly, including cross-route pairs."""
    if echelon != 'e2':
        return greedy_insertion(sol, removed, evaluator, echelon=echelon)
    residual = insert_complete_customer_pairs(sol, removed, evaluator)
    if residual:
        greedy_insertion(sol, residual, evaluator, echelon='e2')


# =====================================================================
#  ALNS 主循环
# =====================================================================

def solve_alns(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
               TFp=9999.0, max_iterations=5000, max_time=120,
               seed=42, verbose=True, strong_initial_solution=True,
               return_info=False, target_objective=None,
               target_tolerance=1e-6, alns_config_overrides=None):
    random.seed(seed)
    start_time = time.time()

    evaluator = Evaluator(problem, e1_tasks, e2_tasks_by_sat, TFp,
                          vehicles_per_satellite=vehicles_per_satellite)

    if strong_initial_solution:
        sol = build_strong_initial_solution(
            problem, e1_tasks, e2_tasks_by_sat, evaluator)
    else:
        sol = build_initial_solution(problem, e1_tasks, e2_tasks_by_sat,
                                     evaluator)
    if not evaluator.is_feasible(sol):
        if verbose:
            print("\n[WARN] Model 3 ALNS 初始解不满足全局时间/容量约束")
        info = {"objective": None, "runtime": time.time() - start_time,
                "iterations": 0, "feasible": False, "status": "NO_SOLUTION"}
        return (None, None, info) if return_info else (None, None)

    sol.cost = evaluator.total_cost(sol)
    best_sol = sol.copy()
    best_cost = sol.cost

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"ALNS 求解器 (Model 3 取送货)")
        print(f"{'=' * 60}")
        print(f"初始解目标值: {sol.cost:.2f}")
        print(f"初始解可行性: {evaluator.is_feasible(sol)}")
        print(f"强初始解: {strong_initial_solution}")
        print(f"E1 路径数: {len(sol.e1_routes)}")
        e2_count = sum(len(v) for v in sol.e2_routes.values())
        print(f"E2 路径数: {e2_count}")

    result = run_alns_search(
        initial_solution=sol,
        evaluator=evaluator,
        destroy_ops=[random_removal, worst_removal, shaw_removal,
                     paired_customer_removal, route_customer_pair_removal],
        repair_ops=[greedy_insertion, regret_insertion, paired_insertion],
        config=ALNSConfig(max_iterations=max_iterations, max_time=max_time,
                          target_cost=target_objective,
                          target_tolerance=target_tolerance,
                          **(alns_config_overrides or {})),
        verbose=verbose,
        start_time=start_time,
    )
    best_sol = result.best_solution
    if best_sol is None:
        info = {"objective": None, "runtime": result.elapsed,
                "iterations": result.iterations, "feasible": False,
                "status": "NO_SOLUTION"}
        return (None, None, info) if return_info else (None, None)
    best_cost = result.best_cost
    elapsed = result.elapsed
    if verbose:
        print(f"\n{'=' * 60}")
        print(f"ALNS 求解完成")
        print(f"最优目标值: {best_cost:.2f}")
        print(f"总耗时: {elapsed:.2f} 秒")
        print(f"{'=' * 60}")

    e1_routes_out = [r.full_path() for r in best_sol.e1_routes]
    e2_routes_out = {}
    for sat_id, routes in best_sol.e2_routes.items():
        e2_routes_out[sat_id] = [r.full_path() for r in routes]

    if verbose:
        _print_solution(best_sol, evaluator)
    info = {
        "objective": float(best_cost), "runtime": float(elapsed),
        "iterations": int(result.iterations),
        "feasible": bool(evaluator.is_feasible(best_sol)),
        "status": "FEASIBLE" if evaluator.is_feasible(best_sol) else "NO_SOLUTION",
        "strong_initial_solution": bool(strong_initial_solution),
        "best_found_time": result.best_found_time,
        "target_hit_time": result.target_hit_time,
        "termination_reason": result.termination_reason,
        "restarts": result.restarts,
        "local_search_improvements": result.local_search_improvements,
        "alns_diagnostics": result.diagnostics,
    }
    return ((e1_routes_out, e2_routes_out, info) if return_info
            else (e1_routes_out, e2_routes_out))


# =====================================================================
#  载量模拟和打印
# =====================================================================

def _simulate_e1_load_m3(route, evaluator):
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


def _simulate_e2_load_m3(route, sat_id, evaluator):
    init_w = sum(evaluator.e2_task_map[n].w
                 for n in route.nodes if evaluator.e2_task_map.get(n) and
                 evaluator.e2_task_map[n].kind == 'd')
    init_v = sum(evaluator.e2_task_map[n].v
                 for n in route.nodes if evaluator.e2_task_map.get(n) and
                 evaluator.e2_task_map[n].kind == 'd')
    Q_load, V_load = init_w, init_v
    result = [(sat_id, Q_load, V_load)]
    for nid in route.nodes:
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
        loads = _simulate_e1_load_m3(r, evaluator)
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
            loads = _simulate_e2_load_m3(r, sat_id, evaluator)
            load_strs = []
            for idx, (n, Qv, Vv) in enumerate(loads):
                is_first = (idx == 0)
                is_last = (idx == len(loads) - 1)
                tag = n if is_first else f"{n}_arr" if is_last else str(n)
                load_strs.append(f"{tag}(Q={Qv:.1f},V={Vv:.1f})")
            print(f"      载量: {' -> '.join(load_strs)}")
