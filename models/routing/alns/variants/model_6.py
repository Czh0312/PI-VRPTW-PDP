"""Model 6-specific ALNS evaluator and relay post-processing."""
# -*- coding: utf-8 -*-
"""
Model 6 (两阶段：先规划再中转) 的 ALNS 求解器
"""
import random
import time

from models.routing.alns.types import (
    Route, Solution, FEAS_TOL, clean_empty_routes,
    is_tfp_active,
)
from models.routing.alns.engine import ALNSConfig, ALNSHooks, run_alns_search
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

    def e2_task_satellite(self, task_id):
        return self.e2_task_sat.get(task_id)

    def e2_start_times(self, sol):
        e2_start_by_sat, _ = self.e1_schedule(sol)
        return e2_start_by_sat or {}

    def signed_demand(self, task):
        return task.w if task.kind == 'd' else -task.w

    def signed_volume(self, task):
        return task.v if task.kind == 'd' else -task.v

    def phys(self, node_id):
        if node_id.startswith("TRANS_PICK_"):
            return _transport_node_phys(node_id, "TRANS_PICK_")
        if node_id.startswith("TRANS_DROP_"):
            return _transport_node_phys(node_id, "TRANS_DROP_")
        return self.task_to_phys.get(node_id, node_id)

    def dist(self, a, b):
        return self.problem.distance(self.phys(a), self.phys(b))

    def travel(self, a, b):
        return self.problem.travel_time(self.phys(a), self.phys(b))

    def service_time(self, node_id):
        phys_id = self.phys(node_id)
        node_obj = self.problem.nodes.get(phys_id)
        return getattr(node_obj, 'service_time', 0.0) if node_obj else 0.0

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
            if nid.startswith("TRANS_"):
                continue
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

            if nid.startswith("TRANS_PICK_"):
                task_id = _transport_node_task(nid, "TRANS_PICK_")
                task = self.e2_task_map.get(task_id)
                if task:
                    if abs_arrival < task.tw[0]:
                        current_time = task.tw[0] - start_time
                        abs_arrival = task.tw[0]
                    if abs_arrival > task.tw[1] + FEAS_TOL:
                        return False, {}, None
                arrivals[nid] = abs_arrival
                tw, tv = route.transport_loads.get(nid, (0.0, 0.0))
                Q_load += tw
                V_load += tv
                if Q_load > self.problem.e2_weight_cap + FEAS_TOL:
                    return False, {}, None
                if V_load > self.problem.e2_volume_cap + FEAS_TOL:
                    return False, {}, None
                current_time += self.service_time(nid)
            elif nid.startswith("TRANS_DROP_"):
                arrivals[nid] = abs_arrival
                tw, tv = route.transport_loads.get(nid, (0.0, 0.0))
                Q_load -= tw
                V_load -= tv
                if Q_load < -FEAS_TOL:
                    return False, {}, None
                if V_load < -FEAS_TOL:
                    return False, {}, None
                current_time += self.service_time(nid)
            else:
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
        self.task_to_phys = dict(sol.task_to_phys or self.task_to_phys)
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

        e1_departures, _ = self.e1_schedule(sol)
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
                visited_e2.extend(n for n in r.nodes
                                  if not n.startswith("TRANS_"))
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

        for (sat_id, _ben_idx, pick_node, new_phys), payload in sol.transshipments.items():
            old_phys, _carrier_idx, _cw, _cv = payload
            pick_vnode = _transport_pick_node(pick_node, old_phys)
            drop_vnode = _transport_drop_node(pick_node, new_phys)
            if (pick_node not in e2_arrivals or pick_vnode not in e2_arrivals
                    or drop_vnode not in e2_arrivals):
                return False
            task = self.e2_task_map.get(pick_node)
            d_id = f"{task.cust}d" if task else None
            if d_id and d_id in self.e2_task_map:
                if d_id not in e2_arrivals:
                    return False
                if e2_arrivals[pick_vnode] < e2_arrivals[d_id] + self.service_time(d_id) - FEAS_TOL:
                    return False
            if e2_arrivals[pick_node] < e2_arrivals[drop_vnode] - FEAS_TOL:
                return False

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
            assigned = []
            start_time = e2_start_by_sat.get(sat_id, 0.0)
            for t in unassigned:
                test_route = Route(sat_id, route.nodes + [t.id])
                if evaluator.check_e2_route_feasible(test_route, sat_id,
                                                     start_time):
                    route.nodes.append(t.id)
                    assigned.append(t)
            for t in assigned:
                unassigned.remove(t)
            if route.nodes:
                routes.append(route)
            elif unassigned:
                forced = unassigned.pop(0)
                routes.append(Route(sat_id, [forced.id]))

        sol.e2_routes[sat_id] = routes

    sol.cost = evaluator.total_cost(sol)
    sol.task_to_phys = dict(evaluator.task_to_phys)
    return sol


# =====================================================================
#  破坏/修复算子（同 Model 3，无中转）
# =====================================================================

def random_removal(sol, evaluator, num_remove, echelon='e2'):
    removed = []
    if echelon == 'e2':
        all_nodes = []
        for sat_id, routes in sol.e2_routes.items():
            for r in routes:
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
                    old_cost = evaluator.route_distance(r)
                    for pos in range(len(r.nodes) + 1):
                        old_nodes = r.nodes[:]
                        r.nodes.insert(pos, nid)
                        if evaluator.check_e2_route_feasible(r, sat_id,
                                                             start_time):
                            c = evaluator.route_distance(r) - old_cost
                            insertion_costs.append((c, r, pos))
                        r.nodes = old_nodes

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
#  阶段二：后处理中转优化
# =====================================================================

def postprocess_transshipment(sol, evaluator, verbose=True,
                              time_budget=2.0,
                              sample_pickups=5,
                              sample_locations=3,
                              sample_carriers=3,
                              position_top_k=3,
                              min_net_saving=0.01,
                              max_transshipments=None,
                              max_transshipments_per_sat=None,
                              target_cost=None, target_tolerance=1e-6,
                              solver_start_time=None,
                              audit_log=None):
    solver_start_time = (time.time() if solver_start_time is None
                         else solver_start_time)
    if time_budget <= 0:
        sol.cost = evaluator.total_cost(sol)
        sol.task_to_phys = dict(evaluator.task_to_phys)
        return ((time.time() - solver_start_time)
                if target_cost is not None
                and sol.cost <= target_cost + target_tolerance else None)

    postprocess_start = time.time()
    iteration = 0
    while True:
        remaining = time_budget - (time.time() - postprocess_start)
        if remaining <= 0.0:
            break
        iteration += 1
        evaluator.task_to_phys = dict(sol.task_to_phys)
        before_cost = evaluator.total_cost(sol)
        improvement = _search_and_apply_relocation(
            sol, evaluator,
            time_budget=remaining,
            sample_pickups=sample_pickups,
            sample_locations=sample_locations,
            sample_carriers=sample_carriers,
            position_top_k=position_top_k,
            min_net_saving=min_net_saving,
            max_transshipments=max_transshipments,
            max_transshipments_per_sat=max_transshipments_per_sat,
        )
        if not improvement:
            exchange = _search_and_apply_transshipment_exchange(
                sol, evaluator,
                time_budget=remaining,
                sample_pickups=sample_pickups,
                sample_locations=sample_locations,
                sample_carriers=sample_carriers,
                position_top_k=position_top_k,
                min_net_saving=min_net_saving,
                max_transshipments=max_transshipments,
                max_transshipments_per_sat=max_transshipments_per_sat,
            )
            if not exchange:
                break
            improvement = exchange.get("added")
            if audit_log is not None:
                removed_pickup = exchange["removed_key"][2]
                audit_log[:] = [
                    event for event in audit_log
                    if event.get("pickup_node") != removed_pickup
                ]
                # An improving exchange may consist only of removing the old
                # handoff.  In that case ``added`` is False, so there is no
                # replacement event to append to the audit log.
                if isinstance(improvement, dict):
                    audit_log.append({
                        **improvement,
                        "distance_before": float(exchange["before_cost"]),
                        "distance_after": float(exchange["after_cost"]),
                        "actual_distance_saving": float(
                            exchange["before_cost"] - exchange["after_cost"]),
                        "exchange": True,
                    })
            sol.cost = evaluator.total_cost(sol)
            if verbose:
                print(f"  中转交换 {iteration}: cost={sol.cost:.2f}, "
                      f"中转数={len(sol.transshipments)}")
            if (target_cost is not None
                    and sol.cost <= target_cost + target_tolerance):
                sol.task_to_phys = dict(evaluator.task_to_phys)
                return time.time() - solver_start_time
            continue
        sol.cost = evaluator.total_cost(sol)
        if audit_log is not None:
            audit_log.append({
                **improvement,
                "distance_before": float(before_cost),
                "distance_after": float(sol.cost),
                "actual_distance_saving": float(before_cost - sol.cost),
            })
        if verbose:
            print(f"  中转迭代 {iteration}: cost={sol.cost:.2f}, "
                  f"中转数={len(sol.transshipments)}")
        if (target_cost is not None
                and sol.cost <= target_cost + target_tolerance):
            sol.task_to_phys = dict(evaluator.task_to_phys)
            return time.time() - solver_start_time

    sol.cost = evaluator.total_cost(sol)
    sol.task_to_phys = dict(evaluator.task_to_phys)
    return ((time.time() - solver_start_time)
            if target_cost is not None
            and sol.cost <= target_cost + target_tolerance else None)


def _adopt_solution(target, source):
    target.e1_routes = [route.copy() for route in source.e1_routes]
    target.e2_routes = {
        sat: [route.copy() for route in routes]
        for sat, routes in source.e2_routes.items()
    }
    target.cost = source.cost
    target.task_to_phys = dict(source.task_to_phys)
    target.transshipments = dict(source.transshipments)


def _undo_transshipment(sol, evaluator, key):
    payload = sol.transshipments.get(key)
    if payload is None:
        return False
    sat_id, _ben_idx, pick_node, new_phys = key
    old_phys, _carrier_idx, _cw, _cv = payload
    pick_vnode = _transport_pick_node(pick_node, old_phys)
    drop_vnode = _transport_drop_node(pick_node, new_phys)
    for route in sol.e2_routes.get(sat_id, []):
        route.nodes = [
            node_id for node_id in route.nodes
            if node_id not in (pick_vnode, drop_vnode)
        ]
        route.transport_loads.pop(pick_vnode, None)
        route.transport_loads.pop(drop_vnode, None)
    sol.transshipments.pop(key, None)
    sol.task_to_phys[pick_node] = old_phys
    evaluator.task_to_phys = dict(sol.task_to_phys)
    sol.cost = evaluator.total_cost(sol)
    return evaluator.is_feasible(sol)


def _search_and_apply_transshipment_exchange(
        sol, evaluator, time_budget,
        sample_pickups, sample_locations, sample_carriers, position_top_k,
        min_net_saving, max_transshipments, max_transshipments_per_sat):
    """One-exchange neighborhood: remove a handoff, then rebuild the best one.

    The former postprocessor could only append handoffs. This neighborhood can
    move or replace an early greedy decision and can also delete a handoff if
    later route changes make it unattractive.
    """
    if not sol.transshipments or time_budget <= 0:
        return False
    started = time.time()
    before_cost = evaluator.total_cost(sol)
    best = None
    keys = list(sol.transshipments)
    random.shuffle(keys)
    for index, key in enumerate(keys):
        remaining = time_budget - (time.time() - started)
        if remaining <= 0:
            break
        candidate = sol.copy()
        if not _undo_transshipment(candidate, evaluator, key):
            continue
        added = None
        # Split the remaining budget across unvisited exchange candidates.
        candidate_budget = remaining / max(1, len(keys) - index)
        added = _search_and_apply_relocation(
            candidate, evaluator,
            time_budget=candidate_budget,
            sample_pickups=sample_pickups,
            sample_locations=sample_locations,
            sample_carriers=sample_carriers,
            position_top_k=position_top_k,
            min_net_saving=min_net_saving,
            max_transshipments=max_transshipments,
            max_transshipments_per_sat=max_transshipments_per_sat,
        )
        candidate.cost = evaluator.total_cost(candidate)
        if (candidate.cost < before_cost - FEAS_TOL
                and evaluator.is_feasible(candidate)
                and (best is None
                     or candidate.cost < best[0].cost - FEAS_TOL)):
            best = (candidate, key, added)

    evaluator.task_to_phys = dict(sol.task_to_phys)
    if best is None:
        return False
    candidate, removed_key, added = best
    _adopt_solution(sol, candidate)
    evaluator.task_to_phys = dict(sol.task_to_phys)
    return {
        "removed_key": removed_key,
        "added": added,
        "before_cost": before_cost,
        "after_cost": sol.cost,
    }


def _search_and_apply_relocation(sol, evaluator, time_budget=2.0,
                                 sample_pickups=5, sample_locations=3,
                                 sample_carriers=3, position_top_k=3,
                                 min_net_saving=0.01,
                                 max_transshipments=None,
                                 max_transshipments_per_sat=None):
    if not evaluator.is_feasible(sol):
        return False
    if _transshipment_limit_reached(sol, max_transshipments):
        return False

    e2_start_by_sat, _ = evaluator.e1_schedule(sol)
    if e2_start_by_sat is None:
        return False

    t_start = time.time()

    best_plan = None

    relocated = {k[2] for k in sol.transshipments}

    for sat_id, routes in sol.e2_routes.items():
        if time.time() - t_start > time_budget:
            break
        if _sat_transshipment_limit_reached(
                sol, sat_id, max_transshipments_per_sat):
            continue

        all_tasks = evaluator.e2_tasks_by_sat.get(sat_id, [])
        all_phys = list(set(t.cust for t in all_tasks))
        start_time = e2_start_by_sat.get(sat_id, 0.0)

        pickups = []
        for ben_idx, route in enumerate(routes):
            for pick_node in route.nodes:
                if pick_node.startswith("TRANS_"):
                    continue
                t = evaluator.e2_task_map.get(pick_node)
                if t and t.kind == 'p' and pick_node not in relocated:
                    pickups.append((ben_idx, pick_node, t))

        if not pickups:
            continue
        if sample_pickups is not None and len(pickups) > sample_pickups:
            pickups = random.sample(pickups, sample_pickups)

        for ben_idx, pick_node, t in pickups:
            if time.time() - t_start > time_budget:
                break

            old_phys = evaluator.phys(pick_node)
            ben_route = sol.e2_routes[sat_id][ben_idx]
            old_ben_dist = evaluator.route_distance(ben_route)

            candidates = [p for p in all_phys if p != old_phys]
            if (sample_locations is not None
                    and len(candidates) > sample_locations):
                candidates = random.sample(candidates, sample_locations)

            for new_phys in candidates:
                if time.time() - t_start > time_budget:
                    break
                evaluator.task_to_phys[pick_node] = new_phys
                try:
                    saved_ben_nodes = ben_route.nodes[:]
                    try:
                        ben_route.nodes.remove(pick_node)
                    except ValueError:
                        continue

                    ben_positions = _quick_best_positions(
                        ben_route.nodes, ben_route.base, pick_node,
                        evaluator, top_k=position_top_k)

                    for bp in ben_positions:
                        if time.time() - t_start > time_budget:
                            break

                        ben_route.nodes.insert(bp, pick_node)
                        ben_ok, ben_arrivals, _ben_return = evaluator.simulate_e2_route(
                            ben_route, sat_id, start_time)

                        d_id = f"{t.cust}d"
                        if ben_ok and d_id in ben_arrivals:
                            if ben_arrivals[pick_node] < (
                                    ben_arrivals[d_id]
                                    + evaluator.service_time(d_id)
                                    - FEAS_TOL):
                                ben_ok = False

                        if not ben_ok:
                            ben_route.nodes.pop(bp)
                            continue

                        new_ben_dist = evaluator.route_distance(ben_route)
                        v2_saving = old_ben_dist - new_ben_dist
                        if v2_saving <= min_net_saving:
                            ben_route.nodes.pop(bp)
                            continue

                        carrier_list = [(ci, cr) for ci, cr in enumerate(routes)
                                        if ci != ben_idx]
                        if (sample_carriers is not None
                                and len(carrier_list) > sample_carriers):
                            carrier_list = random.sample(carrier_list, sample_carriers)

                        for carrier_idx, carrier_route in carrier_list:
                            if time.time() - t_start > time_budget:
                                break

                            old_cd = evaluator.route_distance(carrier_route)
                            pick_vnode = _transport_pick_node(pick_node, old_phys)
                            drop_vnode = _transport_drop_node(pick_node, new_phys)

                            pick_positions = _quick_best_positions(
                                carrier_route.nodes, carrier_route.base,
                                pick_vnode, evaluator, top_k=position_top_k)

                            for pp in pick_positions:
                                if time.time() - t_start > time_budget:
                                    break

                                saved_nodes = carrier_route.nodes[:]
                                saved_loads = dict(carrier_route.transport_loads)
                                carrier_route.nodes.insert(pp, pick_vnode)
                                carrier_route.transport_loads[pick_vnode] = (t.w, t.v)

                                drop_positions = _quick_best_positions(
                                    carrier_route.nodes, carrier_route.base,
                                    drop_vnode, evaluator, top_k=position_top_k,
                                    start_after=pp)

                                for dp in drop_positions:
                                    if time.time() - t_start > time_budget:
                                        break
                                    old_nodes2 = carrier_route.nodes[:]
                                    old_loads2 = dict(carrier_route.transport_loads)

                                    carrier_route.nodes.insert(dp, drop_vnode)
                                    carrier_route.transport_loads[drop_vnode] = (t.w, t.v)

                                    carrier_ok, carrier_arrivals, _carrier_return = evaluator.simulate_e2_route(
                                        carrier_route, sat_id, start_time)
                                    if carrier_ok and d_id in carrier_arrivals:
                                        if carrier_arrivals[pick_vnode] < (
                                                carrier_arrivals[d_id]
                                                + evaluator.service_time(d_id)
                                                - FEAS_TOL):
                                            carrier_ok = False

                                    if carrier_ok and (
                                            ben_arrivals.get(pick_node, float('inf'))
                                            >= carrier_arrivals.get(drop_vnode, float('inf'))
                                            - FEAS_TOL):
                                        new_cd = evaluator.route_distance(carrier_route)
                                        net = v2_saving - (new_cd - old_cd)

                                        if (best_plan is None
                                                or net > best_plan[0]):
                                            best_plan = (
                                                net, sat_id, ben_idx,
                                                pick_node, new_phys,
                                                carrier_idx, bp, pp, dp)

                                    carrier_route.nodes[:] = old_nodes2
                                    carrier_route.transport_loads = old_loads2

                                carrier_route.nodes[:] = saved_nodes
                                carrier_route.transport_loads = saved_loads

                        ben_route.nodes.pop(bp)
                    ben_route.nodes[:] = saved_ben_nodes
                finally:
                    evaluator.task_to_phys[pick_node] = old_phys

    if best_plan and best_plan[0] > min_net_saving:
        (net_saving, sat_id, ben_idx, pick_node, new_phys,
         carrier_idx, ben_pos, pick_pos, drop_pos) = best_plan

        old_phys = evaluator.phys(pick_node)
        t = evaluator.e2_task_map[pick_node]

        evaluator.task_to_phys[pick_node] = new_phys

        ben_route = sol.e2_routes[sat_id][ben_idx]
        carrier_route = sol.e2_routes[sat_id][carrier_idx]
        saved_ben_nodes = ben_route.nodes[:]
        saved_carrier_nodes = carrier_route.nodes[:]
        saved_carrier_loads = dict(carrier_route.transport_loads)
        pick_vnode = _transport_pick_node(pick_node, old_phys)
        drop_vnode = _transport_drop_node(pick_node, new_phys)

        ben_route.nodes.remove(pick_node)
        ben_route.nodes.insert(ben_pos, pick_node)

        carrier_route.nodes.insert(pick_pos, pick_vnode)
        carrier_route.transport_loads[pick_vnode] = (t.w, t.v)

        carrier_route.nodes.insert(drop_pos, drop_vnode)
        carrier_route.transport_loads[drop_vnode] = (t.w, t.v)

        sol.transshipments[(sat_id, ben_idx, pick_node, new_phys)] = (
            old_phys, carrier_idx, t.w, t.v)
        sol.task_to_phys = dict(evaluator.task_to_phys)
        if not evaluator.is_feasible(sol):
            evaluator.task_to_phys[pick_node] = old_phys
            sol.task_to_phys[pick_node] = old_phys
            ben_route.nodes[:] = saved_ben_nodes
            carrier_route.nodes[:] = saved_carrier_nodes
            carrier_route.transport_loads = saved_carrier_loads
            sol.transshipments.pop((sat_id, ben_idx, pick_node, new_phys), None)
            return False

        return {
            "satellite": sat_id,
            "pickup_node": pick_node,
            "original_pickup_location": old_phys,
            "handoff_location": new_phys,
            "beneficiary_route_index": ben_idx,
            "carrier_route_index": carrier_idx,
            "estimated_net_distance_saving": float(net_saving),
        }

    return False


def _transshipment_limit_reached(sol, limit):
    return limit is not None and len(sol.transshipments) >= limit


def _sat_transshipment_limit_reached(sol, sat_id, limit):
    if limit is None:
        return False
    return sum(1 for key in sol.transshipments if key[0] == sat_id) >= limit


def _quick_best_positions(nodes, base, vnode, evaluator, top_k=3,
                          start_after=-1):
    v_phys = evaluator.phys(vnode)
    path = [base] + nodes + [base]
    positions = []

    for pos in range(start_after + 1, len(nodes) + 1):
        prev_phys = evaluator.phys(path[pos])
        next_phys = evaluator.phys(path[pos + 1])
        detour = (evaluator.problem.distance(prev_phys, v_phys)
                  + evaluator.problem.distance(v_phys, next_phys)
                  - evaluator.problem.distance(prev_phys, next_phys))
        positions.append((detour, pos))

    positions.sort(key=lambda x: x[0])
    if top_k is None:
        return [p for _, p in positions]
    return [p for _, p in positions[:top_k]]


def _transport_pick_node(pick_node, old_phys):
    return f"TRANS_PICK_{pick_node}@{old_phys}"


def _transport_drop_node(pick_node, new_phys):
    return f"TRANS_DROP_{pick_node}@{new_phys}"


def _transport_node_phys(node_id, prefix):
    payload = node_id[len(prefix):]
    if "@" in payload:
        return payload.rsplit("@", 1)[1]
    return payload


def _transport_node_task(node_id, prefix):
    payload = node_id[len(prefix):]
    if "@" in payload:
        return payload.rsplit("@", 1)[0]
    return None


def _path_with_relocations(route, sol, evaluator):
    path = route.full_path()
    out = []
    for nid in path:
        if nid.startswith("TRANS_"):
            out.append(nid)
            continue
        phys = sol.task_to_phys.get(nid)
        default_phys = nid[:-1] if nid.endswith(('d', 'p')) else nid
        if phys and phys != default_phys and phys in evaluator.problem.nodes:
            out.append(phys)
        else:
            out.append(nid)
    return out


def _removal_saving(route, node_id, evaluator):
    path = route.full_path()
    node_pos_in_full = None
    for idx in range(1, len(path) - 1):
        if path[idx] == node_id:
            node_pos_in_full = idx
            break
    if node_pos_in_full is None:
        return 0.0

    prev_n = path[node_pos_in_full - 1]
    next_n = path[node_pos_in_full + 1]
    return (evaluator.dist(prev_n, node_id) + evaluator.dist(node_id, next_n)
            - evaluator.dist(prev_n, next_n))


# =====================================================================
#  主求解函数
# =====================================================================

def solve_alns(problem, e1_tasks, e2_tasks_by_sat, vehicles_per_satellite,
               TFp=9999.0, max_iterations=5000, max_time=120,
               seed=42, verbose=True,
               transshipment_time_budget=30.0,
               transshipment_sample_pickups=None,
               transshipment_sample_locations=None,
               transshipment_sample_carriers=None,
               transshipment_position_top_k=None,
               stage1_time_ratio=0.75,
               transshipment_min_net_saving=0.01,
               transshipment_max_total=None,
               transshipment_max_per_satellite=None,
               strong_initial_solution=True,
               return_info=False,
               initial_solution=None,
               refill_unused_time=True,
               target_objective=None, target_tolerance=1e-6,
               alns_config_overrides=None):
    random.seed(seed)
    start_time = time.time()

    evaluator = Evaluator(problem, e1_tasks, e2_tasks_by_sat, TFp,
                          vehicles_per_satellite=vehicles_per_satellite)

    if initial_solution is not None:
        sol = initial_solution.copy()
        if not sol.task_to_phys:
            sol.task_to_phys = dict(evaluator.task_to_phys)
    elif strong_initial_solution:
        sol = build_strong_initial_solution(
            problem, e1_tasks, e2_tasks_by_sat, evaluator)
    else:
        sol = build_initial_solution(problem, e1_tasks, e2_tasks_by_sat,
                                     evaluator)
    if not evaluator.is_feasible(sol):
        if verbose:
            print("\n[WARN] Model 6 ALNS 初始解不满足全局时间/容量约束")
        info = {"objective": None, "runtime": time.time() - start_time,
                "iterations": 0, "feasible": False, "status": "NO_SOLUTION",
                "transshipment_count": 0}
        return (None, None, info) if return_info else (None, None)

    sol.cost = evaluator.total_cost(sol)
    best_sol = sol.copy()
    best_cost = sol.cost

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"ALNS 求解器 (Model 6 两阶段：先规划再中转)")
        print(f"{'=' * 60}")
        print(f"初始解目标值: {sol.cost:.2f}")
        print(f"初始解可行性: {evaluator.is_feasible(sol)}")
        print(f"强初始解: {strong_initial_solution}")
        print(f"E1 路径数: {len(sol.e1_routes)}")
        e2_count = sum(len(v) for v in sol.e2_routes.values())
        print(f"E2 路径数: {e2_count}")
        print(f"阶段一时间占比: {stage1_time_ratio:.2f}")
        print("中转接受规则: "
              f"min_net={transshipment_min_net_saving}, "
              f"max_total={transshipment_max_total}, "
              f"max_per_sat={transshipment_max_per_satellite}")

    if verbose:
        print(f"\n--- 阶段一：Model 3 ALNS (无中转) ---")

    stage1_limit = max_time * max(0.0, min(stage1_time_ratio, 1.0))

    class Model6Stage1Hooks(ALNSHooks):
        def time_limit_message(self, iteration, max_time):
            if iteration == 0:
                return "  阶段一时间接近限制，停止"
            return None

    result = run_alns_search(
        initial_solution=sol,
        evaluator=evaluator,
        destroy_ops=[random_removal, worst_removal, shaw_removal,
                     paired_customer_removal, route_customer_pair_removal],
        repair_ops=[greedy_insertion, regret_insertion, paired_insertion],
        config=ALNSConfig(
            max_iterations=max_iterations,
            max_time=stage1_limit,
            target_cost=target_objective,
            target_tolerance=target_tolerance,
            **(alns_config_overrides or {}),
        ),
        hooks=Model6Stage1Hooks(),
        verbose=verbose,
        start_time=start_time,
    )
    best_sol = result.best_solution
    if best_sol is None:
        info = {"objective": None, "runtime": result.elapsed,
                "iterations": result.iterations, "feasible": False,
                "status": "NO_SOLUTION", "transshipment_count": 0}
        return (None, None, info) if return_info else (None, None)
    best_cost = result.best_cost
    stage1_cost = best_cost
    total_iterations = result.iterations
    target_hit_time = result.target_hit_time
    if verbose:
        elapsed = time.time() - start_time
        print(f"\n  阶段一完成: cost={best_cost:.2f}, 耗时={elapsed:.2f}s")

    if verbose:
        print(f"\n--- 阶段二：后处理中转搜索 ---")

    evaluator.task_to_phys = dict(best_sol.task_to_phys)
    remaining_budget = max(0.0, max_time - (time.time() - start_time))
    effective_transshipment_budget = (0.0 if target_hit_time is not None else min(
        max(0.0, float(transshipment_time_budget)), remaining_budget
    ))
    transshipment_events = []
    stage2_started = time.time()
    post_hit_time = postprocess_transshipment(
        best_sol, evaluator, verbose=verbose,
        time_budget=effective_transshipment_budget,
        sample_pickups=transshipment_sample_pickups,
        sample_locations=transshipment_sample_locations,
        sample_carriers=transshipment_sample_carriers,
        position_top_k=transshipment_position_top_k,
        min_net_saving=transshipment_min_net_saving,
        max_transshipments=transshipment_max_total,
        max_transshipments_per_sat=transshipment_max_per_satellite,
        target_cost=target_objective,
        target_tolerance=target_tolerance,
        solver_start_time=start_time,
        audit_log=transshipment_events,
    )
    stage2_elapsed = time.time() - stage2_started
    if target_hit_time is None:
        target_hit_time = post_hit_time

    # If the transshipment scan finishes early without changing the solution,
    # give its unused wall-clock budget back to the ordinary ALNS.  This keeps
    # Model 6 comparable with Model 3 under one common total time budget.
    if (refill_unused_time
            and target_hit_time is None
            and not best_sol.transshipments
            and total_iterations < max_iterations
            and time.time() - start_time < max_time - 1e-3):
        refill = run_alns_search(
            initial_solution=best_sol,
            evaluator=evaluator,
            destroy_ops=[random_removal, worst_removal, shaw_removal,
                         paired_customer_removal, route_customer_pair_removal],
            repair_ops=[greedy_insertion, regret_insertion, paired_insertion],
            config=ALNSConfig(
                max_iterations=max_iterations - total_iterations,
                max_time=max_time,
                target_cost=target_objective,
                target_tolerance=target_tolerance,
                **(alns_config_overrides or {}),
            ),
            hooks=Model6Stage1Hooks(),
            verbose=False,
            start_time=start_time,
        )
        total_iterations += refill.iterations
        if refill.best_solution is not None:
            best_sol = refill.best_solution
            best_cost = refill.best_cost
            stage1_cost = best_cost
        if refill.target_hit_time is not None:
            target_hit_time = refill.target_hit_time

    if verbose:
        print(f"\n  阶段二完成: cost={best_sol.cost:.2f} "
              f"(改进: {stage1_cost - best_sol.cost:.2f})")
        print(f"  中转操作数: {len(best_sol.transshipments)}")

    elapsed = time.time() - start_time
    if verbose:
        print(f"\n{'=' * 60}")
        print(f"Model 6 求解完成")
        print(f"阶段一(无中转): {stage1_cost:.2f}")
        print(f"阶段二(中转后): {best_sol.cost:.2f}")
        print(f"中转操作数: {len(best_sol.transshipments)}")
        print(f"总耗时: {elapsed:.2f} 秒")
        print(f"{'=' * 60}")

    evaluator.task_to_phys = dict(best_sol.task_to_phys)
    e1_routes_out = [
        _path_with_relocations(r, best_sol, evaluator)
        for r in best_sol.e1_routes
    ]
    e2_routes_out = {}
    for sat_id, routes in best_sol.e2_routes.items():
        e2_routes_out[sat_id] = [
            _path_with_relocations(r, best_sol, evaluator)
            for r in routes
        ]

    if verbose:
        _print_solution(best_sol, evaluator)
    feasible = bool(evaluator.is_feasible(best_sol))
    info = {
        "objective": float(best_sol.cost), "stage1_objective": float(stage1_cost),
        "runtime": float(elapsed), "iterations": int(total_iterations),
        "feasible": feasible,
        "status": "FEASIBLE" if feasible else "NO_SOLUTION",
        "transshipment_count": len(best_sol.transshipments),
        "transshipment_improvement": float(stage1_cost - best_sol.cost),
        "transshipment_events": transshipment_events,
        "strong_initial_solution": bool(strong_initial_solution),
        "external_initial_solution": initial_solution is not None,
        "refill_unused_time": bool(refill_unused_time),
        "stage1_time_ratio": float(stage1_time_ratio),
        "stage1_time_budget_seconds": float(stage1_limit),
        "transshipment_time_budget_requested": float(transshipment_time_budget),
        "transshipment_time_budget_effective": float(effective_transshipment_budget),
        # Backward-compatible alias: this is the budget made available, not
        # the measured wall-clock time.  Use the elapsed field below for audit.
        "transshipment_time_budget_used": float(effective_transshipment_budget),
        "transshipment_search_elapsed_seconds": float(stage2_elapsed),
        "transshipment_sample_pickups": transshipment_sample_pickups,
        "transshipment_sample_locations": transshipment_sample_locations,
        "transshipment_sample_carriers": transshipment_sample_carriers,
        "transshipment_position_top_k": transshipment_position_top_k,
        "best_found_time": result.best_found_time,
        "target_hit_time": target_hit_time,
        "termination_reason": ("target" if target_hit_time is not None
                               else result.termination_reason),
        "restarts": result.restarts,
        "local_search_improvements": result.local_search_improvements,
        "alns_diagnostics": result.diagnostics,
    }
    return ((e1_routes_out, e2_routes_out, info) if return_info
            else (e1_routes_out, e2_routes_out))


# =====================================================================
#  载量模拟和打印
# =====================================================================

def _simulate_e1_load(route, evaluator):
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
